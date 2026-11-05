"""A2A 服务端：把 JSON-RPC 请求桥接到本域已有的 Task 语义。

设计上只做三件事，其余全部复用：

| A2A 概念 | 落到本项目 |
|---|---|
| SendMessage | 建一个 fulfillment Task 并**同步**跑完（诊断不需要人工审批，见下） |
| GetTask | 复用 `TaskStore.get` |
| Agent Card | 静态声明 + 由 domain pack 生成 skills |

一个与 REST 入口的关键差异：A2A 委派走 `diagnose_only`，不进入动作审批。
理由——知识域问的是"这单为什么没到"，它要的是根因，不是"重推承运商"这个动作。
让上游 Agent 去批一个下游 Agent 的写动作，等于把审批责任偷偷转移到没有审批界面的一侧。
需要执行动作时，仍由人从排障域自己的入口进来（REST + input_required）。
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import Request

from api.schemas import SubjectContext
from runtime.errors import KwError

from .types import (
    AgentCard,
    Part,
    rpc_err,
    rpc_ok,
    task_payload,
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_WORKING,
)

#: JSON-RPC 错误码（标准段 + 自定义段）
ERR_METHOD = -32601
ERR_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_TASK_NOT_FOUND = -32001


def mount(app, *, card: AgentCard, run_symptom: Callable[[str, dict[str, Any], SubjectContext], dict[str, Any]]) -> None:
    """把 A2A 端点挂到已有的 FastAPI app 上。

    `run_symptom` 由宿主注入：server 不关心图怎么跑、工具怎么调，
    这样 A2A 这层可以独立测试（喂一个假 run_symptom 就能验证协议）。
    """

    @app.get("/.well-known/agent-card.json")
    def agent_card() -> dict[str, Any]:
        return card.to_dict()

    @app.post("/a2a")
    async def a2a_rpc(request: Request) -> dict[str, Any]:
        # 手埋 span 而不是靠全局中间件：A2A 是跨进程的唯一入口，
        # trace 必须在这里接上上游的 traceparent，否则"跨进程一条 trace"
        # 只是文档里的一句话——两边各记各的，排障时对不上。
        from runtime import tracing

        span, token = tracing.begin_http_span(
            "POST", "/a2a", dict(request.headers), domain="a2a", service_version=""
        )
        try:
            return await _dispatch(request)
        finally:
            tracing.end_http_span(span, token)

    async def _dispatch(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return rpc_err(None, ERR_PARAMS, "请求体不是合法 JSON")

        req_id = body.get("id")
        method = str(body.get("method") or "")
        params = body.get("params") or {}

        if method in ("SendMessage", "message/send"):
            return _send_message(req_id, params, run_symptom)
        if method in ("GetTask", "tasks/get"):
            return rpc_err(req_id, ERR_METHOD, "GetTask 由宿主的 /v1 路由提供；A2A 侧只做同步委派")
        if method in ("CancelTask", "tasks/cancel"):
            return rpc_ok(req_id, task_payload(str(params.get("id") or ""), TASK_STATE_CANCELED))
        return rpc_err(req_id, ERR_METHOD, f"未知方法：{method}")


def _send_message(req_id: Any, params: dict[str, Any], run_symptom) -> dict[str, Any]:
    message = params.get("message") or {}
    parts = message.get("parts") or []
    # Part 无 kind：文本就是带 text 的那个
    text = next((str(p.get("text")) for p in parts if p.get("text")), "").strip()
    if not text:
        return rpc_err(req_id, ERR_PARAMS, "message.parts 里没有 text 内容")

    metadata = params.get("metadata") or {}
    subject = _subject_from(metadata)
    if subject is None:
        # 被调方二次校验的第一道：没有主体就不给数据。绝不因为"是自己人调的"就放宽。
        return rpc_err(req_id, ERR_PARAMS, "metadata.subject 缺失或非法（委派也必须带主体）")

    scope = metadata.get("scope") or {}
    task_id = f"a2a_{uuid.uuid4().hex[:12]}"

    try:
        out = run_symptom(text, scope, subject)
    except KwError as exc:
        return rpc_ok(req_id, task_payload(task_id, TASK_STATE_FAILED, status_message=exc.message,
                                           metadata={"code": str(exc.code)}))
    except Exception as exc:  # noqa: BLE001
        return rpc_ok(req_id, task_payload(task_id, TASK_STATE_FAILED,
                                           status_message=f"{type(exc).__name__}: {exc}"))

    state = out.get("state") or TASK_STATE_COMPLETED
    artifacts: list[Part] = []
    if out.get("root_cause"):
        artifacts.append(Part(data={
            "root_cause": out["root_cause"],
            "confidence": out.get("confidence"),
            "hypotheses": out.get("hypotheses") or [],
        }))
    if out.get("diagnosis"):
        artifacts.append(Part(text=str(out["diagnosis"])))
    return rpc_ok(
        req_id,
        task_payload(
            task_id,
            state,
            artifacts=artifacts,
            status_message=str(out.get("status_message") or ""),
            metadata={"elapsed_ms": out.get("elapsed_ms"), "usage": out.get("usage")},
        ),
    )


def _subject_from(metadata: dict[str, Any]) -> SubjectContext | None:
    raw = metadata.get("subject")
    if not isinstance(raw, dict) or not raw.get("user_id"):
        return None
    try:
        return SubjectContext(
            user_id=str(raw["user_id"]),
            account_status=str(raw.get("account_status") or "active"),
            dept_paths=list(raw.get("dept_paths") or []),
            roles=list(raw.get("roles") or []),
            groups=list(raw.get("groups") or []),
            approval_doc_ids=list(raw.get("approval_doc_ids") or []),
            subject_epoch=int(raw.get("subject_epoch") or 1),
        )
    except Exception:  # noqa: BLE001
        return None


#: 供宿主把这几个状态名映射成自己的 Task 状态
STATE_MAP = {
    "completed": TASK_STATE_COMPLETED,
    "working": TASK_STATE_WORKING,
    "input_required": TASK_STATE_INPUT_REQUIRED,
    "failed": TASK_STATE_FAILED,
    "canceled": TASK_STATE_CANCELED,
}
