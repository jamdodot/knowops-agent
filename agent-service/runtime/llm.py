"""模型接入。

配置的唯一真相源是 `config/models.yaml`（08 / ADR-0003）。这里只做三件事：
按角色路由、算成本、把 mock 与真实调用藏在同一个接口后面。

为什么 M0 必须有 mock 而不是"先接真模型再说"：
1. 图逻辑与契约正确性不该依赖网络和额度；
2. `fallback.mock` 本来就要求"按 hash(prompt) 命中录制响应"，这是离线演示与 CI 的前提；
3. 真模型接进来后，「同一条链路的 token/成本」才有对照组。

一个诚实的边界：mock 的 usage 是**估算**（按字符数折算），不是真实计费。
所以 mock 模式下 `usage.cost_usd` 只能用于链路自检，绝不能进成本报表。
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol

import yaml

from api.schemas import Code, Usage

from .errors import KwError
from .prompts import Rendered


#: mock 流式按句切分用的边界。真提供商的切分在 tokens 边界，这里只是让
#: 离线链路也能产出"多帧"，避免测试只覆盖到"一帧到底"这条最容易过的路径。
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")

#: 值得重试的上游状态：都是"这一瞬间不行"，不是"这个请求不对"。
#: 4xx 不在此列——重发只会得到同样的拒绝，还会浪费一次额度。
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class RoleCfg:
    provider: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 1024
    timeout_s: float = 30.0


@dataclass(frozen=True)
class ProviderCfg:
    kind: str
    base_url: str
    api_key: str = ""
    timeout_s: float = 30.0
    chat_path: str = "/chat/completions"


@dataclass
class LLMResult:
    text: str
    usage: Usage
    role: str
    model: str
    cache_hit: bool = False


@dataclass
class StreamChunk:
    """流式增量。

    `usage` 只在**最后一帧**出现：OpenAI 兼容协议把统计放在 `stream_options.
    include_usage` 的尾帧里，正文帧的 usage 恒为 None。所以调用方不能假设
    "每帧都有 usage"，也不能假设"一定有尾帧"——有的网关干脆不给。
    """

    text: str = ""
    usage: Usage | None = None
    model: str = ""


class LLMProvider(Protocol):
    def chat(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> LLMResult: ...

    def stream(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> Iterator[StreamChunk]: ...


def _apply_role_overrides(roles: dict[str, RoleCfg]) -> dict[str, RoleCfg]:
    """用环境变量临时改「某个角色走哪个模型」。

        KW_ROLE_OVERRIDE="generate=siliconflow/Qwen/Qwen2.5-72B-Instruct,classify=siliconflow/Qwen/Qwen3-8B"
        KW_ROLE_TIMEOUT_S="90"     # 可选，统一放大超时

    为什么用环境变量而不是直接改 models.yaml：
    换模型是**一次评测的临时决策**。写进 yaml 之后，"默认用哪个模型"这件事
    就不可追了——下次有人看仓库会以为硅基流动才是默认。环境变量的作用域
    只活在这一条命令里，跑完即弃，yaml 仍然是决策记录。

    只覆盖 provider 与 model，temperature / max_tokens 保持原角色：
    那些参数是针对任务形状调的（比如 classify 必须 1024 才不会把思考链截断），
    跟着模型一起换等于把踩过的坑重踩一遍。

    格式只支持 `<role>=<provider>/<model>`，provider 与 model 之间用**第一个**
    斜杠切——model id 本身带斜杠（Qwen/Qwen2.5-72B-Instruct）是常态。
    """
    raw = (os.environ.get("KW_ROLE_OVERRIDE") or "").strip()
    timeout_raw = (os.environ.get("KW_ROLE_TIMEOUT_S") or "").strip()
    timeout = float(timeout_raw) if timeout_raw else None
    if not raw and timeout is None:
        return roles

    out = dict(roles)
    for item in filter(None, (s.strip() for s in raw.split(","))):
        role, _, spec = item.partition("=")
        role, spec = role.strip(), spec.strip()
        provider, _, model = spec.partition("/")
        if not role or not provider or not model:
            raise KwError(Code.INTERNAL, f"KW_ROLE_OVERRIDE 条目格式不对：{item!r}，应为 role=provider/model")
        if role not in out:
            # 拼错角色名不报错的话，你会以为切换生效了、实际还在跑老模型，
            # 然后拿两批不同模型的结果做对比。这比直接崩掉危险得多。
            raise KwError(Code.INTERNAL, f"KW_ROLE_OVERRIDE 引用了不存在的角色：{role}")
        old = out[role]
        out[role] = RoleCfg(
            provider=provider,
            model=model,
            temperature=old.temperature,
            max_tokens=old.max_tokens,
            timeout_s=timeout or old.timeout_s,
        )
    if timeout is not None:
        out = {k: RoleCfg(v.provider, v.model, v.temperature, v.max_tokens, timeout) for k, v in out.items()}
    return out


class ModelConfig:
    """`config/models.yaml` 的只读视图。"""

    def __init__(self, roles: dict[str, RoleCfg], providers: dict[str, ProviderCfg], prices: dict[str, dict[str, float]]):
        self.roles = roles
        self.providers = providers
        self.prices = prices

    @classmethod
    def load(cls, path: Path | str) -> ModelConfig:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        providers = {
            name: ProviderCfg(
                kind=cfg.get("kind", "openai_compat"),
                base_url=str(cfg.get("base_url", "")).rstrip("/"),
                api_key=_expand(cfg.get("api_key", "")),
                timeout_s=float(cfg.get("timeout_s", 30)),
                chat_path=str((cfg.get("endpoints") or {}).get("chat", "/chat/completions")),
            )
            for name, cfg in (doc.get("providers") or {}).items()
        }
        roles = {
            name: RoleCfg(
                provider=cfg["provider"],
                model=_expand(cfg["model"]),
                temperature=float(cfg.get("temperature", 0.2)),
                max_tokens=int(cfg.get("max_tokens", 1024)),
                timeout_s=float(cfg.get("timeout_s", 30)),
            )
            for name, cfg in (doc.get("roles") or {}).items()
            if "provider" in cfg and "model" in cfg
        }
        roles = _apply_role_overrides(roles)
        prices = doc.get("cost_ref") or {}
        return cls(roles=roles, providers=providers, prices=prices)

    def role(self, name: str) -> RoleCfg:
        try:
            return self.roles[name]
        except KeyError as exc:
            raise KwError(Code.INTERNAL, f"models.yaml 未定义角色 {name}") from exc

    def price(self, model: str) -> tuple[float, float, float] | None:
        """(input_miss, input_hit, output) 每百万 token 美元；查不到返回 None。

        查不到就返回 None，不做"取平均价"这类猜测——猜出来的成本比没有成本更危险，
        因为它会让预算控制看起来在工作。
        """
        key = model.replace("-", "_").replace(".", "_").replace("/", "_")
        entry = self.prices.get(key)
        if not entry:
            return None
        return (
            float(entry.get("input_miss_usd_per_m", 0)),
            float(entry.get("input_hit_usd_per_m", 0)),
            float(entry.get("output_usd_per_m", 0)),
        )

    def cost_usd(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        price = self.price(model)
        if price is None:
            return None
        miss, _hit, out = price
        return round((input_tokens * miss + output_tokens * out) / 1_000_000, 8)


class MockProvider:
    """录制回放：优先按 render 指纹命中，其次按 prompt id 的默认应答。"""

    def __init__(self, models: ModelConfig, recordings: Path | str, default_role: str = "generate") -> None:
        self._models = models
        self._dir = Path(recordings)
        self._default_role = default_role
        self._by_id: dict[str, Any] = {}
        mapping = self._dir / "mock_llm.yaml"
        if mapping.is_file():
            self._by_id = (yaml.safe_load(mapping.read_text(encoding="utf-8")) or {}).get("responses", {}) or {}

    def chat(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> LLMResult:
        try:
            cfg = self._models.role(role)
        except KwError:
            cfg = RoleCfg(provider="mock", model="mock", max_tokens=max_tokens or 1024)

        text = self._lookup(prompt)
        cfg_max = max_tokens or cfg.max_tokens
        text = text[: max(cfg_max * 4, 64)]

        in_tok = sum(_estimate_tokens(m["content"]) for m in prompt.messages)
        out_tok = _estimate_tokens(text)
        return LLMResult(
            text=text,
            usage=Usage(input_tokens=in_tok, output_tokens=out_tok, cost_usd=0.0),
            role=role,
            model=f"mock:{cfg.model}",
            cache_hit=True,
        )

    def stream(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> Iterator[StreamChunk]:
        """按句切分产出，让链路测试能覆盖"多帧"，而不是只测一帧。

        和真提供商一样在最后补一个仅含 usage 的尾帧——调用方不该为 mock
        单独写一条分支。
        """
        result = self.chat(role, prompt, max_tokens=max_tokens)
        for piece in _SENTENCE_SPLIT.split(result.text):
            if piece:
                yield StreamChunk(text=piece)
        yield StreamChunk(usage=result.usage, model=result.model)

    def _lookup(self, prompt: Rendered) -> str:
        captured = self._dir / f"{prompt.render_sha256}.json"
        if captured.is_file():
            data = json.loads(captured.read_text(encoding="utf-8"))
            return str(data.get("text", ""))

        entry = self._by_id.get(prompt.prompt_id)
        if entry is None:
            # 兜底：返回空 JSON 对象。节点必须能容忍字段缺失——
            # 这不是"容错设计"，是因为模型本来就可能少给字段，节点不防御就是生产事故。
            return "{}"
        if isinstance(entry, str):
            return entry

        # 规则式录制：只用于本地假链路的分支验证，不参与任何评测分数
        user_text = prompt.messages[-1]["content"] if prompt.messages else ""
        for rule in entry.get("rules", []) or []:
            if str(rule.get("contains", "")) in user_text:
                return str(rule.get("text", ""))
        return str(entry.get("default", "{}"))


class OpenAICompatProvider:
    """OpenAI 兼容的 chat 调用。只有 mode != mock 时才会被实例化。"""

    def __init__(self, models: ModelConfig) -> None:
        import httpx  # 延迟导入：mock 模式下不需要网络栈

        self._models = models
        self._client = httpx.Client(timeout=None)
        self._httpx = httpx

    def _prepare(
        self, role: str, prompt: Rendered, max_tokens: int | None
    ) -> tuple[RoleCfg, ProviderCfg, str, dict[str, Any], dict[str, str]]:
        """把"角色 → provider → URL + 请求体 + 头"这段重复逻辑收一处。

        chat 与 stream 的差别**只在 body 里有没有 stream 字段**；分成两份写，
        迟早出现"非流式加了新参数、流式忘了加"的漂移。
        """
        cfg = self._models.role(role)
        try:
            provider = self._models.providers[cfg.provider]
        except KeyError as exc:
            raise KwError(Code.UPSTREAM_UNAVAILABLE, f"未配置 provider {cfg.provider}") from exc

        url = provider.base_url + provider.chat_path
        payload: dict[str, Any] = {
            "model": cfg.model,
            "messages": prompt.openai_messages(),
            "temperature": cfg.temperature,
            "max_tokens": max_tokens or cfg.max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        return cfg, provider, url, payload, headers

    def _raise_for_status(self, status_code: int, body: str = "") -> None:
        if status_code == 429:
            raise KwError(Code.RATE_LIMITED, "模型限流", retry_after_ms=1_000)
        if status_code >= 500:
            raise KwError(Code.UPSTREAM_UNAVAILABLE, "模型服务异常", internal=f"HTTP {status_code}")
        if status_code >= 400:
            raise KwError(Code.INVALID_SCHEMA, "模型请求被拒绝", internal=body[:300])

    @staticmethod
    def _sleep_before_retry(attempt: int, status: int | None = None) -> None:
        """退避重试。

        为什么值得写这十几行：一次 300 条跑批里，上游 503 会稳定吃掉 5~30 条题，
        而这些题在报告里会表现为"答错了"——**用基础设施抖动污染能力指标**，
        是最容易得出错误结论的一种方式（2026-11-07 实测：28 条 '?' 让正确率从
        99% 掉到 85%）。重试不是"让测试好看"，是让被测对象成为唯一变量。
        """
        import time

        base = 1.0 if status == 429 else 0.6
        time.sleep(base * (2**attempt))

    def chat(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> LLMResult:
        cfg, provider, url, payload, headers = self._prepare(role, prompt, max_tokens)

        resp = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = self._client.post(url, json=payload, headers=headers, timeout=cfg.timeout_s)
            except self._httpx.TimeoutException as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise KwError(Code.MODEL_TIMEOUT, "模型调用超时", internal=str(exc)) from exc
                self._sleep_before_retry(attempt)
                continue
            except self._httpx.HTTPError as exc:
                raise KwError(Code.UPSTREAM_UNAVAILABLE, "模型服务不可用", internal=str(exc)) from exc

            if resp.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
                # 5xx / 429 是上游的瞬时状态，同一份 prompt 重发是安全的
                self._sleep_before_retry(attempt, resp.status_code)
                continue
            break

        assert resp is not None
        self._raise_for_status(resp.status_code, resp.text)

        body = resp.json()
        message = (body.get("choices") or [{}])[0].get("message", {}) or {}
        # deepseek-v4-flash 等推理模型把正文放在 reasoning_content，content 可能为空。
        # 非流式调用里，若有 reasoning 就取 reasoning，否则取 content；两者皆空才视为空。
        text = message.get("content") or message.get("reasoning_content") or ""
        raw_usage = body.get("usage") or {}
        input_tokens = int(raw_usage.get("prompt_tokens", 0))
        output_tokens = int(raw_usage.get("completion_tokens", 0))
        cost = self._models.cost_usd(cfg.model, input_tokens, output_tokens)
        return LLMResult(
            text=text,
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost),
            role=role,
            model=cfg.model,
            cache_hit=bool(raw_usage.get("prompt_cache_hit_tokens")),
        )

    def stream(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> Iterator[StreamChunk]:
        """逐帧解析 SSE。

        两个必须处理的现实问题：

        1. **usage 在尾帧**，而且要显式请求（`stream_options.include_usage`），
           否则整条流一个 token 数都拿不到——账本就会静默记 0。
        2. **思考模型（Qwen3 / deepseek-reasoner）的 delta 里是 reasoning_content**，
           正文 content 要等思考完才出现。这里**只发 content**：把思考过程推给用户
           等于把草稿当答案，而且会让"首字时间"看起来很好、实际可用内容没变。
        """
        cfg, _provider, url, payload, headers = self._prepare(role, prompt, max_tokens)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        usage: Usage | None = None
        model = cfg.model
        out_parts: list[str] = []
        #: 一旦吐出过正文，就不能再重试——重试会把同一段内容发第二遍。
        #: 这是流式与非流式最本质的差别：非流式可以随便重发，流式只有"开口之前"可反悔。
        emitted = False
        finished = False

        for attempt in range(_MAX_ATTEMPTS):
            try:
                with self._client.stream("POST", url, json=payload, headers=headers, timeout=cfg.timeout_s) as resp:
                    if resp.status_code in _RETRY_STATUS and not emitted and attempt < _MAX_ATTEMPTS - 1:
                        resp.read()
                        self._sleep_before_retry(attempt, resp.status_code)
                        continue
                    if resp.status_code >= 400:
                        resp.read()
                        self._raise_for_status(resp.status_code, resp.text)

                    for line in resp.iter_lines():
                        if not line or not line.startswith("data: "):
                            # 空行是帧分隔符，`:` 开头是心跳注释，都不是数据
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            # 网关偶尔会插非 JSON 的注释行；丢掉比整条流崩掉好
                            continue

                        model = str(obj.get("model") or model)
                        raw_usage = obj.get("usage")
                        if raw_usage:
                            in_tok = int(raw_usage.get("prompt_tokens", 0))
                            out_tok = int(raw_usage.get("completion_tokens", 0))
                            usage = Usage(
                                input_tokens=in_tok,
                                output_tokens=out_tok,
                                cost_usd=self._models.cost_usd(model, in_tok, out_tok),
                            )
                        for choice in obj.get("choices") or []:
                            piece = (choice.get("delta") or {}).get("content") or ""
                            if piece:
                                out_parts.append(piece)
                                emitted = True
                                yield StreamChunk(text=piece)
                finished = True
                break
            except KwError:
                if emitted or attempt == _MAX_ATTEMPTS - 1:
                    raise
                self._sleep_before_retry(attempt)
            except self._httpx.TimeoutException as exc:
                if emitted or attempt == _MAX_ATTEMPTS - 1:
                    raise KwError(Code.MODEL_TIMEOUT, "模型流式调用超时", internal=str(exc)) from exc
                self._sleep_before_retry(attempt)
            except self._httpx.HTTPError as exc:
                if emitted:
                    raise KwError(Code.UPSTREAM_UNAVAILABLE, "模型流式调用中断", internal=str(exc)) from exc
                if attempt == _MAX_ATTEMPTS - 1:
                    raise KwError(Code.UPSTREAM_UNAVAILABLE, "模型服务不可用", internal=str(exc)) from exc
                self._sleep_before_retry(attempt)

        if not finished and not emitted:
            raise KwError(Code.UPSTREAM_UNAVAILABLE, "模型流式调用未产出任何内容")

        if usage is None:
            # 网关没给尾帧。**估算比记 0 好**：记 0 会让预算控制看起来在工作，
            # 而实际上一直在免费跑。cost 留 None（查不到价就不编价）。
            usage = Usage(
                input_tokens=sum(_estimate_tokens(m["content"]) for m in prompt.messages),
                output_tokens=_estimate_tokens("".join(out_parts)),
                cost_usd=None,
            )
        yield StreamChunk(usage=usage, model=model)

    def close(self) -> None:
        self._client.close()


def build_provider(mode: str, models: ModelConfig, recordings: Path | str) -> LLMProvider | None:
    """按 KW_LLM_MODE 选实现。online 但没 key 时**不静默降级成 mock**。

    静默降级是线上事故最常见的形态（models.yaml §4 原话）：
    功能还在跑，效果掉一半，没人知道。所以宁可起不来。
    """
    if mode == "mock":
        return MockProvider(models, recordings)
    if mode == "none":
        return None
    if mode in ("online", "hybrid", "local"):
        missing = [n for n, p in models.providers.items() if not p.api_key]
        if missing:
            raise KwError(
                Code.UPSTREAM_UNAVAILABLE,
                f"provider 缺 API key：{missing}；要么补 key，要么把 KW_LLM_MODE 设为 mock",
            )
        return OpenAICompatProvider(models)
    raise KwError(Code.INTERNAL, f"未知 KW_LLM_MODE={mode}")


def _expand(value: Any) -> str:
    """支持 `${VAR}` 与 `${VAR:default}`，与 models.yaml 的写法一致。"""
    text = str(value)
    if not text.startswith("${"):
        return text
    inner = text[2:-1]
    name, _, default = inner.partition(":")
    return os.environ.get(name, default)


def _estimate_tokens(text: str) -> int:
    """粗估：CJK 约 1 字 1 token，ASCII 约 4 字符 1 token。只用于 mock。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return max(1, cjk + math.ceil(rest / 4))
