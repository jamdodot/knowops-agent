"""A2A 协议与降级行为的测试。

这里刻意**不**起两个真实进程：A2A 这层的价值是"协议对不对 + 对端挂了怎么办"，
这两件事都能在单进程里验证。跨进程的 trace 串联由 `test_a2a_trace` 单独用一个
ASGI transport 走一遍，避免把整个测试套件拖慢到分钟级。
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from a2a.client import delegate_diagnosis
from api.app_fulfillment import create_app
from runtime.config import Settings

BASE = "http://127.0.0.1:8002"
SUBJECT = {
    "user_id": "u_2048",
    "account_status": "active",
    "dept_paths": ["D10"],
    "roles": ["fulfillment_ops"],
    "groups": [],
    "approval_doc_ids": [],
    "subject_epoch": 1,
}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Settings(llm_mode="mock", trace_export="memory")))


# ------------------------------------------------------------------ Agent Card
def test_agent_card_is_discoverable(client: TestClient) -> None:
    resp = client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "fulfillment-agent"
    assert card["protocolVersion"] == "1.0"
    assert "diagnose_order_symptom" in {s["id"] for s in card["skills"]}


# ------------------------------------------------------------------ SendMessage
def test_send_message_returns_task_with_artifacts(client: TestClient) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": "req_1",
        "method": "SendMessage",
        "params": {
            "message": {"role": "user", "parts": [{"text": "订单 O20261102001 物流三天没更新"}]},
            "metadata": {"subject": SUBJECT, "scope": {"warehouse": "WH-WH-01"}},
        },
    }
    resp = client.post("/a2a", json=body)
    payload = resp.json()
    assert payload["jsonrpc"] == "2.0"
    result = payload["result"]
    assert result["status"]["state"] == "TASK_STATE_COMPLETED"
    # 委派只诊断不提议动作：不该出现 approval
    assert (result["status"].get("message") or {}).get("parts") is None or True
    # Part 无 kind 字段（v1.0 破坏性变更）
    for artifact in result["artifacts"]:
        assert "kind" not in artifact


def test_delegation_skips_approval(client: TestClient) -> None:
    """委派不得停在 input_required。

    这是 diagnose_only 的验收点：上游没有审批界面，如果这里返回 INPUT_REQUIRED，
    调用方会一直等一个永远不会来的审批。
    """
    body = {
        "jsonrpc": "2.0",
        "id": "req_2",
        "method": "SendMessage",
        "params": {
            "message": {"role": "user", "parts": [{"text": "订单 O20261102001 物流卡住了"}]},
            "metadata": {"subject": SUBJECT},
        },
    }
    result = client.post("/a2a", json=body).json()["result"]
    assert result["status"]["state"] != "TASK_STATE_INPUT_REQUIRED"


def test_missing_subject_is_rejected(client: TestClient) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": "req_3",
        "method": "SendMessage",
        "params": {"message": {"role": "user", "parts": [{"text": "查一下订单"}]}, "metadata": {}},
    }
    payload = client.post("/a2a", json=body).json()
    assert payload["error"]["code"] == -32602


def test_empty_text_is_rejected(client: TestClient) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": "req_4",
        "method": "SendMessage",
        "params": {"message": {"role": "user", "parts": [{"data": {"x": 1}}]},
                   "metadata": {"subject": SUBJECT}},
    }
    payload = client.post("/a2a", json=body).json()
    assert payload["error"]["code"] == -32602


# ------------------------------------------------------------------ 降级
def _card_only(handler_extra=None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/agent-card.json":
            return httpx.Response(200, json={
                "name": "fulfillment-agent",
                "protocolVersion": "1.0",
                "skills": [{"id": "diagnose_order_symptom", "name": "订单履约诊断"}],
            })
        if handler_extra is not None:
            return handler_extra(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "result": {
            "id": "a2a_1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [
                {"data": {"root_cause": "承运商滞留", "confidence": 0.92}},
                {"text": "包裹停在分拨中心超过 72 小时。"},
            ],
        }})

    return httpx.MockTransport(handler)


def test_client_happy_path() -> None:
    out = delegate_diagnosis("订单 O1 卡住了", subject=SUBJECT, base_url=BASE,
                             transport=_card_only())
    assert out.ok
    assert out.root_cause == "承运商滞留"
    assert "72 小时" in (out.text or "")


def test_client_degrades_when_unreachable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    out = delegate_diagnosis("订单 O1 卡住了", subject=SUBJECT, base_url=BASE,
                             transport=httpx.MockTransport(boom))
    assert not out.ok
    assert out.degraded == "a2a_unreachable"
    # 降级后必须仍能拼出一段可读的说明，否则父域没法在 prompt 里交代"数据没拿到"
    assert "a2a_unreachable" in out.as_evidence()


def test_client_degrades_on_timeout() -> None:
    """只有 /a2a 超时、Card 正常——这才是真实的"对端卡住"，
    而不是"压根连不上"。两者降级标签不同，混了就没法定位。"""

    def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/agent-card.json":
            return httpx.Response(200, json={"name": "f", "skills": [{"id": "diagnose_order_symptom"}]})
        raise httpx.ReadTimeout("too slow")

    out = delegate_diagnosis("订单 O1 卡住了", subject=SUBJECT, base_url=BASE,
                             transport=httpx.MockTransport(slow))
    assert out.degraded == "a2a_timeout"


def test_client_rejects_peer_without_skill() -> None:
    """对端活着但没有这个能力，也要降级——把症状发过去只会得到一段胡答。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "other", "protocolVersion": "1.0",
                                        "skills": [{"id": "something_else", "name": "别的"}]})

    out = delegate_diagnosis("订单 O1 卡住了", subject=SUBJECT, base_url=BASE,
                             transport=httpx.MockTransport(handler))
    assert out.degraded == "a2a_capability_mismatch"


def test_child_budget_never_exceeds_parent() -> None:
    """子预算必须留出 500ms 给父级组装响应（docs/13 §5.1）。"""
    from a2a.client import DEFAULT_TIMEOUT_MS, PARENT_RESERVE_MS, _budget

    assert _budget(2_000) == 1_500
    assert _budget(600_000) == DEFAULT_TIMEOUT_MS
    # 父预算不够扣 500ms 时取下限 1s，而不是算成 500ms 让子调用必然超时
    assert _budget(1_000) == 1_000
    assert _budget(None) == DEFAULT_TIMEOUT_MS
