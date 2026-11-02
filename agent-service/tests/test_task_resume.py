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
