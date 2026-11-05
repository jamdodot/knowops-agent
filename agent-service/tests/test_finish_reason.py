"""收尾语义：答案已经发出去之后，`finish_reason` 不许反悔。

背景（2026-11-04 实测）：`synthesize` 先发 `citation`、再逐句 `delta`，这两步之后
正文对用户**已经不可撤回**；随后撞上 deadline，`_finish_reason` 却一律落成
`abstained`。82 条里 28 条如此——用户屏幕上摆着带 5~8 条引用的完整答案，
API 说"拒答"，下游据此转人工。

契约里 `finish_reason` 本来就有 `degraded` 这个值（docs/13 §4.3），正是给这种情况用的。
这里证明三件事：
1. `delivered` 只在"引用 + 正文都发过"时为真；
2. 已交付时撞预算落 `degraded`，不是 `abstained`；
3. 没交付时仍然落 `abstained`——不能借这个修复把真拒答也放过去。
"""

from __future__ import annotations

from types import SimpleNamespace

from api.app_knowledge import _finish_reason
from api.schemas import Citation
from runtime.events import EventSink


def _sink() -> EventSink:
    return EventSink(
        lambda seq, ev, data: None,
        trace_id="t",
        contract_version="1.0.0",
        config_version="cfg-test",
    )


def _stopped(**state) -> SimpleNamespace:
    return SimpleNamespace(stopped=True, state=state)


# --------------------------------------------------------------- delivered
def test_delivered_requires_both_citation_and_delta():
    sink = _sink()
    assert sink.delivered is False

    sink.citation([Citation(eid="e1", doc_id="d1", title="t", acl_epoch=1)])
    assert sink.delivered is False, "只有引用、还没正文时不算交付"

    sink.delta("正文", eids=["e1"])
    assert sink.delivered is True


def test_delta_without_citation_is_not_delivered():
    """拒答话术也走 delta，但它没有引用，不该被当成"已交付答案"。"""
    sink = _sink()
    sink.delta("现有资料未覆盖。")
    assert sink.delivered is False


# ------------------------------------------------------------ finish_reason
def test_budget_stop_after_delivery_is_degraded_not_abstained():
    sink = _sink()
    sink.citation([Citation(eid="e1", doc_id="d1", title="t", acl_epoch=1)])
    sink.delta("答案已经发出去了", eids=["e1"])

    assert _finish_reason(_stopped(), "abstain", sink) == "degraded"


def test_budget_stop_before_delivery_stays_abstained():
    """没交付就得照实说拒答——修复不能把真拒答也顺手放过去。"""
    sink = _sink()
    assert _finish_reason(_stopped(), "abstain", sink) == "abstained"


def test_budget_stop_before_delivery_honours_escalate_policy():
    sink = _sink()
    assert _finish_reason(_stopped(), "escalate", sink) == "escalated"


def test_without_sink_behaviour_is_unchanged():
    """老调用方（不传 sink）语义不变，避免这个改动影响别的入口。"""
    assert _finish_reason(_stopped(), "abstain") == "abstained"


def test_graph_finished_without_verdict_still_abstains():
    """图跑完但没给结论 = 拒答。绝不能默认 answered（那会把 bug 变成"答了个空的"）。"""
    sink = _sink()
    sink.citation([Citation(eid="e1", doc_id="d1", title="t", acl_epoch=1)])
    sink.delta("正文", eids=["e1"])
    result = SimpleNamespace(stopped=False, state={})
    assert _finish_reason(result, "abstain", sink) == "abstained"
