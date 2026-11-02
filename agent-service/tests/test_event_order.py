"""SSE 顺序守卫：docs/13 §4.3 的三条契约不变量，必须能被代码证明。

- meta 必须第一帧
- citation 必须先于引用它的 delta
- done/error/input_required 有且仅有一个（terminal 唯一）

这三条是"客户端永远挂着"和"引用编号错位"这两类线上事故的直接防线。
"""

from __future__ import annotations

import pytest

from runtime.errors import ContractViolation
from runtime.events import EventSink


def _sink(**kw):
    frames = []
    sink = EventSink(
        lambda seq, ev, data: frames.append((seq, ev, data)),
        trace_id="t",
        contract_version="1.0.0",
        config_version="cfg-test",
        **kw,
    )
    return sink, frames


def test_meta_is_first_and_only_once():
    sink, frames = _sink()
    sink.start()
    sink.start()  # 第二次 start 应幂等，不重复发
    events = [e for _, e, _ in frames]
    assert events.count("meta") == 1
    assert events[0] == "meta"


def test_citation_before_delta():
    from api.schemas import Citation

    sink, frames = _sink()
    sink.citation([Citation(eid="e1", doc_id="d1", title="t", acl_epoch=1)])
    sink.delta("正文引用 e1", eids=["e1"])
    events = [e for _, e, _ in frames]
    assert events == ["meta", "citation", "delta"]


def test_delta_cannot_reference_unseen_eid():
    sink, _ = _sink()
    with pytest.raises(ContractViolation):
        sink.delta("引用不存在的证据", eids=["e404"])


def test_citation_after_delta_forbidden():
    from api.schemas import Citation

    sink, _ = _sink()
    sink.delta("先吐正文")
    with pytest.raises(ContractViolation):
        sink.citation([Citation(eid="e1", doc_id="d1", title="t", acl_epoch=1)])


def test_terminal_unique():
    from api.schemas import FinishReason, Usage

    sink, _ = _sink()
    sink.done(FinishReason.answered, Usage(input_tokens=1))
    with pytest.raises(ContractViolation):
        sink.done(FinishReason.answered, Usage(input_tokens=1))


def test_duplicate_eid_forbidden():
    from api.schemas import Citation

    sink, _ = _sink()
    c = Citation(eid="e1", doc_id="d1", title="t", acl_epoch=1)
    sink.citation([c])
    with pytest.raises(ContractViolation):
        sink.citation([c])


def test_close_without_terminal_raises():
    sink, _ = _sink()
    with pytest.raises(ContractViolation):
        sink.close()


def test_input_required_closes_stream_but_not_failed():
    from api.schemas import ActionProposal, Kind

    sink, frames = _sink()
    sink.input_required(
        task_id="t1",
        kind=Kind.approval,
        approval=ActionProposal(
            approval_id="ar_1", action_type="retry_push_carrier", params={}, risk_level="medium"
        ),
    )
    assert sink.paused is True
    assert sink.closed is True
    events = [e for _, e, _ in frames]
    assert events == ["meta", "input_required"]
    # 断流 ≠ 失败：close 不应抛错
    sink.close()
