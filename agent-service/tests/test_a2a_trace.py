"""跨进程 trace：C3 的验收。

为什么非得起一个真端口：这条要证的是**进程边界上 traceparent 有没有传过去**。
用 TestClient 或在同进程内直接调函数，都绕过了 HTTP 头，等于没测。
所以这里用 uvicorn 在线程里起一个真服务，client 走真实 socket。

同进程起服务还有个副作用是我们要的：两边共用同一个 memory exporter，
所以能直接用 `span_tree(trace_id)` 检查"下游的 span 是不是挂在上游那棵树上"。
换成真跨主机就得接 OTLP 后端才能看，M5 做。
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from a2a.client import delegate_diagnosis
from api.app_fulfillment import create_app
from runtime import tracing
from runtime.config import Settings

SUBJECT = {
    "user_id": "u_2048",
    "account_status": "active",
    "dept_paths": ["D10"],
    "roles": ["fulfillment_ops"],
    "groups": [],
    "subject_epoch": 1,
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def live_server():
    tracing.setup("memory")
    app = create_app(Settings(llm_mode="mock", trace_export="memory"))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _names(nodes) -> list[str]:
    out: list[str] = []
    for node in nodes:
        out.append(str(node.get("name")))
        out.extend(_names(node.get("children") or []))
    return out


def test_delegation_trace_is_one_tree(live_server: str) -> None:
    with tracing.span("knowledge.answer"):
        trace_id = tracing.current_trace_id()
        out = delegate_diagnosis(
            "订单 O20261102001 物流三天没更新",
            subject=SUBJECT,
            scope={"source": "knowledge"},
            base_url=live_server,
            parent_budget_ms=30_000,
        )
    assert out.ok, f"委派失败：{out.degraded}"

    names = _names(tracing.span_tree(trace_id))
    # 下游必须挂在同一个 trace 上——否则"跨进程一条 trace"只是文档里的一句话
    assert any("a2a.http.serve" in n for n in names), f"没接上 A2A 入口 span：{names[:8]}"
    # 诊断图的节点也应在同一棵树里
    assert any("fulfillment.node.analyze_symptom" in n for n in names), names[:12]
    assert any("shared.tool.call" in n for n in names), "工具调用没进 trace"


def test_delegation_does_not_require_approval_over_http(live_server: str) -> None:
    """跨进程委派不得停在审批。

    上游 Agent 没有审批界面。如果这里返回 INPUT_REQUIRED，调用方会一直等一个
    永远不会来的审批，而父请求的超时时间远短于人的审批时间。
    """
    out = delegate_diagnosis(
        "订单 O20261102001 物流卡住了",
        subject=SUBJECT,
        base_url=live_server,
        parent_budget_ms=30_000,
    )
    assert out.state == "TASK_STATE_COMPLETED"
    assert out.ok
