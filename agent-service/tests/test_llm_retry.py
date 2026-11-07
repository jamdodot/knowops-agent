"""上游抖动时的重试边界：什么该重发，什么必须立刻失败。

写这些断言的原因是一次真实的跑批事故（2026-11-07）：300 条里 28 条遇到
`HTTP 503`，结果全被记成"答错了"，正确率从 99% 掉到 85%。
基础设施抖动被读成能力下降，是这类评测里最容易得出的错误结论。

但重试不能无脑加，流式下尤其危险：**已经吐出去的正文再重发一遍**，
用户会看到同一段答案出现两次。所以规则是：

- 非流式：5xx / 429 可以重发（同一份 prompt 是幂等的）；
- 流式：只有"还没吐出第一个字"时才允许重发；
- 4xx 一律不重试，重发只会再被拒一次，还白花一次额度。
"""

from __future__ import annotations

import httpx
import pytest

from runtime.llm import ModelConfig, OpenAICompatProvider, ProviderCfg, Rendered, RoleCfg


def _prompt() -> Rendered:
    return Rendered(
        prompt_id="test.synthesize",
        version=1,
        template_sha256="t",
        render_sha256="r",
        messages=({"role": "user", "content": "你好"},),
    )


def _provider(handler) -> OpenAICompatProvider:
    models = ModelConfig(
        roles={"generate": RoleCfg(provider="p", model="m", timeout_s=5, max_tokens=64)},
        providers={"p": ProviderCfg(kind="openai_compat", base_url="http://stub", api_key="k")},
        prices={},
    )
    p = OpenAICompatProvider(models)
    p._client = httpx.Client(transport=httpx.MockTransport(handler))
    return p


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """退避时间不影响正确性，只影响测试时长。"""
    monkeypatch.setattr(OpenAICompatProvider, "_sleep_before_retry", staticmethod(lambda *a, **k: None))


def _ok_body(text: str = "好的") -> dict:
    return {
        "model": "m",
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }


# --------------------------------------------------------------- 非流式
def test_chat_retries_through_503():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=_ok_body("重试成功"))

    out = _provider(handler).chat("generate", _prompt())

    assert out.text == "重试成功"
    assert calls["n"] == 3


def test_chat_gives_up_after_max_attempts():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="busy")

    with pytest.raises(Exception):
        _provider(handler).chat("generate", _prompt())

    assert calls["n"] == 3, "重试次数必须有上限，否则上游挂了会把请求挂死"


def test_chat_does_not_retry_4xx():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    with pytest.raises(Exception):
        _provider(handler).chat("generate", _prompt())

    assert calls["n"] == 1, "4xx 重发只会得到同样的拒绝"


# --------------------------------------------------------------- 流式
def _sse(*frames: str) -> bytes:
    out = ""
    for f in frames:
        out += f"data: {f}\n\n"
    return out.encode()


def test_stream_retries_when_nothing_was_emitted():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(
            200,
            content=_sse(
                '{"model":"m","choices":[{"delta":{"content":"半"}}]}',
                '{"model":"m","choices":[{"delta":{"content":"句"}}]}',
                '{"model":"m","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}',
                "[DONE]",
            ),
            headers={"content-type": "text/event-stream"},
        )

    chunks = list(_provider(handler).stream("generate", _prompt()))

    text = "".join(c.text for c in chunks)
    assert text == "半句"
    assert calls["n"] == 2


def test_stream_does_not_replay_after_first_chunk():
    """已经发出去的内容不能再发一遍——这是流式重试的红线。"""
    calls = {"n": 0}

    def broken_body():
        yield b'data: {"model":"m","choices":[{"delta":{"content":"\xe5\x89\x8d\xe5\x8d\x8a\xe5\x8f\xa5"}}]}\n\n'
        # 模拟连接中途被掐断：httpx 会把这里抛出成 HTTPError
        raise httpx.ReadError("connection reset")

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, content=broken_body(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, content=_sse("[DONE]"), headers={"content-type": "text/event-stream"})

    got: list[str] = []
    with pytest.raises(Exception):
        for c in _provider(handler).stream("generate", _prompt()):
            got.append(c.text)

    assert got == ["前半句"]
    assert calls["n"] == 1, "开口之后不允许重试"


def test_stream_reports_usage_from_tail_frame():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                '{"model":"m","choices":[{"delta":{"content":"答案"}}]}',
                '{"model":"m","choices":[],"usage":{"prompt_tokens":11,"completion_tokens":7}}',
                "[DONE]",
            ),
            headers={"content-type": "text/event-stream"},
        )

    chunks = list(_provider(handler).stream("generate", _prompt()))
    usage = chunks[-1].usage

    assert usage is not None
    assert usage.input_tokens == 11
    assert usage.output_tokens == 7


def test_stream_ignores_reasoning_content():
    """思考模型的 reasoning 不是答案，不能混进正文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                '{"model":"m","choices":[{"delta":{"reasoning_content":"我先想想"}}]}',
                '{"model":"m","choices":[{"delta":{"content":"结论"}}]}',
                "[DONE]",
            ),
            headers={"content-type": "text/event-stream"},
        )

    text = "".join(c.text for c in _provider(handler).stream("generate", _prompt()))

    assert text == "结论"
    assert "想想" not in text
