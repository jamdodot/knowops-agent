"""A2A 服务端：把 JSON-RPC 请求桥接到本域已有的执行入口。

双向对称：知识域与排障域挂的是同一个 `mount`，只是注入的 handler 不同。
这层的职责只有三件事，其余全部复用：

| A2A 概念 | 落到本项目 |
|---|---|
| SendMessage | 调宿主的 handler **同步**跑完（跨域委派不接人工审批，见下） |
| GetTask | 由宿主的 /v1 路由提供；A2A 侧只做同步委派 |
| Agent Card | 静态声明 + 由 domain pack 生成 skills |

handler 的返回形状是统一的，与服务端解耦：

    {"state": TASK_STATE_*, "data": {...}, "text": "...", "status_message": "", "usage": {...}}

`data` 进结构化 artifact，`text` 进文本 artifact。服务端不解释业务字段——
它不知道 `root_cause` 和 `answer` 的区别，也不该知道。

**为什么不接人工审批**：委派方是另一个 Agent，它没有审批界面。
让上游 Agent 去批一个下游 Agent 的写动作，等于把审批责任偷偷转移到
没有审批界面的一侧。需要执行动作时，仍由人从本域自己的入口进来（REST + input_required）。

**回环门禁**放在这一层而不是靠调用方自觉：链里已经出现本域，说明
"请求绕回了我自己"，继续跑下去只会靠预算耗尽才停，trace 上是一串套娃 span。
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
    chain_of,
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

#: handler 返回里由框架消费、不进 artifact 的键
_ENVELOPE_KEYS = {"state", "data", "text", "status_message", "elapsed_ms", "usage"}


def mount(
    app,
    *,
    card: AgentCard,
    handler: Callable[[str, dict[str, Any], SubjectContext], dict[str, Any]] | None = None,
    run_symptom: Callable[[str, dict[str, Any], SubjectContext], dict[str, Any]] | None = None,
) -> None:
    """把 A2A 端点挂到已有的 FastAPI app 上。

    `handler` 由宿主注入：server 不关心图怎么跑、工具怎么调，
    这样 A2A 这层可以独立测试（喂一个假 handler 就能验证协议）。

    `run_symptom` 是历史参数名，保留它只为不打断既有调用方；新代码一律用 `handler`。
    """
    fn = handler or run_symptom
    if fn is None:
        raise ValueError("mount() 必须提供 handler")

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
            return await _dispatch(request, card, fn)
        finally:
            tracing.end_http_span(span, token)


async def _dispatch(request: Request, card: AgentCard, run) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return rpc_err(None, ERR_PARAMS, "请求体不是合法 JSON")

    req_id = body.get("id")
    method = str(body.get("method") or "")
    params = body.get("params") or {}

    if method in ("SendMessage", "message/send"):
        return _send_message(req_id, params, run, card)
    if method in ("GetTask", "tasks/get"):
        return rpc_err(req_id, ERR_METHOD, "GetTask 由宿主的 /v1 路由提供；A2A 侧只做同步委派")
    if method in ("CancelTask", "tasks/cancel"):
        return rpc_ok(req_id, task_payload(str(params.get("id") or ""), TASK_STATE_CANCELED))
    return rpc_err(req_id, ERR_METHOD, f"未知方法：{method}")


def _send_message(req_id: Any, params: dict[str, Any], run, card: AgentCard) -> dict[str, Any]:
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
    # 回环门禁：链里已经有本域，说明这次调用绕回来了。继续跑只会靠预算耗尽才停。
    chain = chain_of(scope)
    me = card.domain_id()
    if me and me in chain:
        return rpc_err(req_id, ERR_PARAMS, f"检测到 A2A 回环：chain={chain}", {"chain": chain})

    task_id = f"a2a_{uuid.uuid4().hex[:12]}"

    try:
        out = run(text, scope, subject) or {}
    except KwError as exc:
        return rpc_ok(req_id, task_payload(task_id, TASK_STATE_FAILED, status_message=exc.message,
                                           metadata={"code": str(exc.code)}))
    except Exception as exc:  # noqa: BLE001
        return rpc_ok(req_id, task_payload(task_id, TASK_STATE_FAILED,
                                           status_message=f"{type(exc).__name__}: {exc}"))

    state = out.get("state") or TASK_STATE_COMPLETED
    return rpc_ok(
        req_id,
        task_payload(
            task_id,
            state,
            artifacts=_artifacts_from(out),
            status_message=str(out.get("status_message") or ""),
            metadata={"elapsed_ms": out.get("elapsed_ms"), "usage": out.get("usage"),
                      "chain": chain},
        ),
    )


def _artifacts_from(out: dict[str, Any]) -> list[Part]:
    """把 handler 的返回拆成 artifact。

    两代形状都吃：新形状显式给 `data` / `text`，老形状把业务字段平铺在顶层。
    只认新形状会让既有调用方（排障域的 run_symptom）必须同批次改完，
    而这不是一个协议层该强加的顺序约束。
    """
    if "data" in out or "text" in out:
        artifacts: list[Part] = []
        data = out.get("data")
        if isinstance(data, dict) and data:
            artifacts.append(Part(data=data))
        if out.get("text"):
            artifacts.append(Part(text=str(out["text"])))
        return artifacts

    # 老形状：平铺字段。`diagnosis` 当正文，其余当结构化数据。
    data = {k: v for k, v in out.items() if k not in _ENVELOPE_KEYS and k != "diagnosis"}
    artifacts = []
    if data:
        artifacts.append(Part(data=data))
    if out.get("diagnosis"):
        artifacts.append(Part(text=str(out["diagnosis"])))
    return artifacts


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
