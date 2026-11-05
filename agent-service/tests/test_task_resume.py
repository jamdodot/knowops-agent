"""Task 中断与恢复：docs/13 §2 有状态 Task 的落点。

这是排障域设计里最容易"只在纸面成立"的地方——审批一超时连接就被掐，
checkpointer 停在 interrupt 上，谁来唤醒？答案是 resume。
这里通过完整 HTTP 链路证明：create → input_required → resume → completed。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.app_fulfillment import create_app as create_fulfillment_app
from api.app_knowledge import create_app as create_knowledge_app
from runtime.config import Settings


def _subject():
    return {
        "user_id": "u_2048",
        "account_status": "active",
        "dept_paths": ["D10"],
        "roles": ["fulfillment_ops"],
        "groups": [],
        "approval_doc_ids": [],
        "subject_epoch": 1,
    }


def _payload(text: str) -> dict:
    return json.loads(text.split("data: ", 1)[1].split("\n", 1)[0])


def _frame_event(frame: str) -> str:
    for line in frame.split("\n"):
        if line.startswith("event: "):
            return line[len("event: ") :]
    return ""


def test_task_pause_and_resume():
    app = create_fulfillment_app(Settings(llm_mode="mock", trace_export="memory"))
    c = TestClient(app)

    r = c.post(
        "/v1/fulfillment/tasks",
        json={"symptom": "订单 O20261102001 物流三天没更新", "subject_ctx": _subject(), "session_id": "s_9"},
    )
    assert r.status_code == 200

    frames = [f for f in r.text.split("\n\n") if f.strip()]
    events = [_frame_event(f) for f in frames]
    assert events[0] == "meta"
    assert "input_required" in events
    # 审批前不允许 done
    assert "done" not in events

    ir_frame = next(f for f in frames if _frame_event(f) == "input_required")
    ir = _payload(ir_frame)
    task_id = ir["task_id"]
    approval_id = ir["approval"]["approval_id"]

    # GET 状态：input_required + pending approval
    g = c.get(f"/v1/fulfillment/tasks/{task_id}")
    assert g.status_code == 200
    assert g.json()["state"] == "input_required"
    assert g.json()["pending"]["kind"] == "approval"

    # resume：带上审批结论
    res = c.post(
        f"/v1/fulfillment/tasks/{task_id}/resume",
        json={
            "task_id": task_id,
            "subject_ctx": _subject(),
            "approval": {"approval_id": approval_id, "decision": "approved", "comment": "确认重推"},
        },
    )
    assert res.status_code == 200
    res_events = [_frame_event(f) for f in res.text.split("\n\n") if f.strip()]
    assert "done" in res_events

    # 恢复后状态 completed
    assert c.get(f"/v1/fulfillment/tasks/{task_id}").json()["state"] == "completed"


def test_task_rejected_escalates_not_reexplains():
    """驳回必须转人工，不能"重写报告再给一遍"。

    证据没变的情况下重写只是换个说法，没有信息增益——而且会让用户觉得
    "我不批它就一直换措辞劝我"，这是审批链路的信任问题，不是文案问题。
    """
    app = create_fulfillment_app(Settings(llm_mode="mock", trace_export="memory"))
    c = TestClient(app)

    r = c.post(
        "/v1/fulfillment/tasks",
        json={"symptom": "订单 O20261102001 物流三天没更新", "subject_ctx": _subject(), "session_id": "s_10"},
    )
    frames = [f for f in r.text.split("\n\n") if f.strip()]
    ir = _payload(next(f for f in frames if _frame_event(f) == "input_required"))
    task_id, approval_id = ir["task_id"], ir["approval"]["approval_id"]

    res = c.post(
        f"/v1/fulfillment/tasks/{task_id}/resume",
        json={
            "task_id": task_id,
            "subject_ctx": _subject(),
            "approval": {"approval_id": approval_id, "decision": "rejected", "comment": "先别重推，联系客户"},
        },
    )
    res_events = [_frame_event(f) for f in res.text.split("\n\n") if f.strip()]
    assert "done" in res_events
    done = _payload(next(f for f in res.text.split("\n\n") if _frame_event(f) == "done"))
    assert done["finish_reason"] == "escalated"

    got = c.get(f"/v1/fulfillment/tasks/{task_id}").json()
    assert got["state"] == "completed"
    # 驳回不能重开一轮诊断：状态必须是 completed，pending 必须清掉
    assert got.get("pending") is None


def test_resume_does_not_replay_llm_calls():
    """恢复时不得重复调模型。

    这条是 interrupt 节点"必须幂等"的直接验证：LangGraph resume 会**从头重放**
    被中断的节点。如果 interrupt 和 LLM 调用写在同一个节点里，每次审批恢复
    都要多花一次模型调用，而且结论可能变——同一个任务点两次"批准"，拿到两份
    不一样的诊断，这在排障场景里是致命的。
    """
    app = create_fulfillment_app(Settings(llm_mode="mock", trace_export="memory"))
    c = TestClient(app)
    rt = app.state.runtime

    calls: list[str] = []
    original = rt.llm_call

    def counting(role: str, *args, **kwargs):
        calls.append(role)
        return original(role, *args, **kwargs)

    rt.llm_call = counting  # type: ignore[method-assign]

    r = c.post(
        "/v1/fulfillment/tasks",
        json={"symptom": "订单 O20261102001 物流三天没更新", "subject_ctx": _subject(), "session_id": "s_11"},
    )
    frames = [f for f in r.text.split("\n\n") if f.strip()]
    ir = _payload(next(f for f in frames if _frame_event(f) == "input_required"))
    before = list(calls)
    assert before, "诊断阶段应当调过模型，否则这条测试没有意义"

    c.post(
        f"/v1/fulfillment/tasks/{ir['task_id']}/resume",
        json={
            "task_id": ir["task_id"],
            "subject_ctx": _subject(),
            "approval": {"approval_id": ir["approval"]["approval_id"], "decision": "approved"},
        },
    )
    assert calls == before, f"恢复阶段不该再调模型，实际新增：{calls[len(before):]}"


def test_knowledge_answer_sse():
    app = create_knowledge_app(Settings(llm_mode="mock", trace_export="memory"))
    c = TestClient(app)

    r = c.post(
        "/v1/knowledge/answers",
        json={
            "query": "跨部门调拨要几级审批？",
            "subject_ctx": {
                "user_id": "u_2048",
                "account_status": "active",
                "dept_paths": ["D10", "D10.3"],
                "roles": ["employee"],
                "groups": [],
                "approval_doc_ids": ["doc_902"],
                "subject_epoch": 1743,
            },
            "session_id": "s_1",
            "turn_seq": 1,
        },
    )
    assert r.status_code == 200
    frames = [f for f in r.text.split("\n\n") if f.strip()]
    events = [_frame_event(f) for f in frames]
    assert events[0] == "meta"
    assert "done" in events
    # done 必须是最后一帧
    assert events[-1] == "done"
