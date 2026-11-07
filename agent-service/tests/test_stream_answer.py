"""流式作答的切分规则：哪些边界必须等、哪些可以提前发。

这些断言看起来琐碎，但每一条都对应一个"线上会静默出错"的场景：

- 引用标记被切成两半 → 前端按 eid 精确匹配，切碎就等于没有引用 → 答案被判拒答；
- 手机号被切成两段 → 两段都匹配不上脱敏正则 → **以明文发出**；
- 提前发得太积极 → 前端每帧整体重渲染，切太碎会把渲染开销变成新的卡顿源。

`_stream_answer` 的取舍是"能提前发才提前发"，这里把它钉死。
"""

from __future__ import annotations

from types import SimpleNamespace

from api.schemas import Citation
from domains.knowledge.nodes import _stream_answer, verify_citations
from runtime.events import EventSink


class _Sink:
    """只记录发出去的片段，不做契约校验——契约有它自己的测试。"""

    def __init__(self) -> None:
        self.pieces: list[str] = []

    def delta(self, text: str, eids: list[str] | None = None) -> None:
        self.pieces.append(text)


class _RT:
    def __init__(self, desensitize=None) -> None:
        self.sink = _Sink()
        self._desensitize = desensitize or (lambda t: t)

    @property
    def guard(self):
        return SimpleNamespace(desensitize=self._desensitize)

    def delta(self, text: str, eids: list[str] | None = None) -> None:
        self.sink.delta(text, eids)

    def current(self):
        return SimpleNamespace(sink=self.sink)


EID = "e_报销管理办法_c1"
KNOWN = {EID}


# --------------------------------------------------------------- 引用标记
def test_eid_is_never_split_across_pieces():
    """模型把 `{` 和 `}` 分在两个 chunk 里，最终仍然是一个完整的标记。"""
    rt = _RT()
    chunks = ["按规定执行", "{", EID, "}", "，其余不变。"]
    text = _stream_answer(rt, iter(chunks), KNOWN)

    assert "{" in text and "}" in text
    # 每个发出去的片段里，花括号都必须配平
    for piece in rt.sink.pieces:
        assert piece.count("{") == piece.count("}"), piece


def test_unknown_eid_is_stripped_before_sending():
    """模型自造的 eid 不能发到前端——前端会渲染成 `?` 号，看起来像引用坏了。"""
    rt = _RT()
    text = _stream_answer(rt, iter(["按规定执行{e_不存在的_c9}，其余不变。"]), KNOWN)

    assert "e_不存在的_c9" not in text
    assert "按规定执行" in text


def test_known_eid_is_kept():
    rt = _RT()
    text = _stream_answer(rt, iter([f"按规定执行{{{EID}}}。"]), KNOWN)
    assert EID in text


# --------------------------------------------------------------- 提前发
def test_long_sentence_without_period_is_emitted_early():
    """没有句号也必须出字，否则长句的第一句要等一整句生成完。"""
    rt = _RT()
    long_no_period = "这一条规定的执行口径是" * 2  # 24 字，无标点
    text = _stream_answer(rt, iter([long_no_period]), KNOWN)

    assert text == long_no_period
    assert len(rt.sink.pieces) >= 2, "24 字应当拆成多帧，而不是等结尾一次性发"


def test_digits_block_early_emit():
    """含数字时等到句子结束：号码被切断就脱敏失败，这是泄露隐私而不是延迟问题。"""
    rt = _RT()
    text = _stream_answer(rt, iter(["联系电话 13800138000 请在工作日拨打。"]), KNOWN)

    # 只有一帧：整句攒齐才发
    assert len(rt.sink.pieces) == 1
    assert "13800138000" in text or "***" in text


def test_desensitize_runs_before_send():
    seen: list[str] = []

    def desensitize(text: str) -> str:
        seen.append(text)
        return text.replace("13800138000", "138****8000")

    rt = _RT(desensitize=desensitize)
    text = _stream_answer(rt, iter(["联系电话 13800138000 请在工作日拨打。"]), KNOWN)

    assert "13800138000" not in text
    assert "138****8000" in text
    assert seen, "脱敏函数必须被调用——它不在发送链路上就等于没有脱敏"


# --------------------------------------------------------------- 判定
def _sink_delivered(delivered: bool) -> EventSink:
    sink = EventSink(lambda seq, ev, data: None, trace_id="t", contract_version="1.0.0", config_version="cfg-test")
    if delivered:
        sink.citation([Citation(eid=EID, doc_id="d", title="t")])
        sink.delta("正文")
    return sink


def _verify_rt(sink: EventSink):
    return SimpleNamespace(current=lambda: SimpleNamespace(sink=sink))


def test_no_citation_degrades_when_already_delivered():
    """正文已经在屏幕上，API 不能再说"没答"——只能标 degraded。"""
    sink = _sink_delivered(True)
    state = {"answer": "一段没有任何引用标记的答案", "citations": [{"eid": EID}]}

    out = verify_citations(state, _verify_rt(sink))

    assert out["finish_reason"] == "degraded"
    assert out["stop_reason"] == "no_citation"


def test_no_citation_still_abstains_when_nothing_was_sent():
    """什么都没发出去时，拒答就是拒答，不能借流式的名义放宽。"""
    sink = _sink_delivered(False)
    state = {"answer": "一段没有任何引用标记的答案", "citations": [{"eid": EID}]}

    out = verify_citations(state, _verify_rt(sink))

    assert out["finish_reason"] == "abstained"


def test_bad_eid_still_fails():
    sink = _sink_delivered(True)
    state = {"answer": "答案{e_伪造的_c1}。", "citations": [{"eid": EID}]}

    out = verify_citations(state, _verify_rt(sink))

    assert out["finish_reason"] == "abstained"
    assert out["stop_reason"] == "citation_failed"
