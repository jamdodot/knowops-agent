"""排障域入口（:8002）：有状态 Task。

和知识域的差别只有一处，但这一处是本质的：**断流 ≠ 任务失败**（docs/13 §2）。
审批可能等几小时，HTTP 连接撑不住，所以：

    POST /tasks            → 流里出 input_required 后**正常关闭**，task → input_required
    GET  /tasks/{id}       → 断线后查状态（幂等，不重跑）
    POST /tasks/{id}/resume→ 带审批结论/补充信息，返回**新的一条流**
    POST /tasks/{id}/cancel→ 显式取消

M0 的 Task 存在内存里（进程重启即丢）。这一点必须写在 README，不能让人以为"重启还能续"。
M1 落 `task` 表 + TTL 清理，语义不变。
"""

from __future__ import annotations

import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from api import http as http_util  # noqa: E402
from api.schemas import (  # noqa: E402
    ActionProposal,
    Code,
    FinishReason,
    FulfillmentTaskRequest,
    Kind,
    Pending,
    Result,
    ResumeRequest,
    State,
    Task,
)
from api.sse import SSE_HEADERS, Channel, frame  # noqa: E402
from runtime import acl_fingerprint, build_runtime, new_run_context, tracing  # noqa: E402
from runtime.config import Settings  # noqa: E402
from runtime.errors import ContractViolation, KwError  # noqa: E402
from runtime.events import EventSink  # noqa: E402

DOMAINS_ROOT = SERVICE_ROOT / "domains"

#: 允许 resume/cancel 的状态。其余状态操作都是状态机冲突，不是"服务器出错"
RESUMABLE = {State.input_required}
CANCELABLE = {State.submitted, State.working, State.input_required}


class TaskStore:
    """M0 内存实现。

    刻意不做"自动清理"：M0 任务量小，而隐式过期会让测试出现间歇性 404，
    排查成本远大于收益。TTL 在 M1 随表一起做（`docs/06` 的任务生命周期）。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._pending: dict[str, dict] = {}

    def create(self, session_id: str, trace_id: str) -> Task:
        task_id = f"tsk_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        task = Task(
            task_id=task_id,
            state=State.submitted,
            session_id=session_id,
            trace_id=trace_id or None,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise KwError(Code.TASK_NOT_FOUND, f"任务不存在或已过期：{task_id}")
        return task

    def set_state(self, task_id: str, state: State, **fields) -> Task:
        task = self.get(task_id).model_copy(update={"state": state, "updated_at": datetime.now(timezone.utc), **fields})
        self._tasks[task_id] = task
        return task

    def set_pending(self, task_id: str, payload: dict) -> None:
        self._pending[task_id] = payload

    def pending(self, task_id: str) -> dict:
        return self._pending.get(task_id, {})


def create_app(settings: Settings | None = None, *, domain_dir: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    tracing.setup(settings.trace_export)

    rt = build_runtime(domain_dir or DOMAINS_ROOT / "fulfillment", settings)
    graph = rt.build_graph()
    store = TaskStore()

    app = FastAPI(title=f"KnowOps {rt.pack.name}", version=settings.service_version)
    app.state.runtime = rt
    app.state.graph = graph
    app.state.tasks = store

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "domain": rt.pack.name,
            "version": settings.service_version,
            "graph": rt.pack.graph,
            "tasks": len(store._tasks),  # noqa: SLF001  （探针要能看出内存任务数）
        }

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        ready = rt.llm is not None
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "ready": ready,
                "checks": {
                    "llm": "mock" if settings.llm_mode == "mock" else ("ok" if rt.llm else "missing"),
                    # 内存 checkpointer 无法跨进程/重启恢复，这是 M0 明确的边界
                    "checkpoint": "memory",
                    "task_store": "memory",
                },
            },
        )

    # ------------------------------------------------------------------ 创建
    @app.post("/v1/fulfillment/tasks")
    async def create_task(request: Request):
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            http_util.check_contract(headers, settings.contract_version)
            req = await http_util.parse_body(request, FulfillmentTaskRequest)
            preflight = list(rt.guard.check_subject(req.subject_ctx))
            budget_ms = http_util.deadline_ms(headers, settings.default_deadline_ms)
        except KwError as exc:
            return http_util.error_response(exc, tracing.current_trace_id() or None)

        task = store.create(req.session_id, tracing.current_trace_id())
        channel = Channel()
        span, token = tracing.begin_http_span(
            "POST", "/v1/fulfillment/tasks", headers, domain=rt.pack.name, service_version=settings.service_version
        )

        def work() -> None:
            sink = EventSink(
                channel.emit,
                trace_id=tracing.current_trace_id() or task.task_id,
                contract_version=settings.contract_version,
                config_version=req.config_version or "cfg-local",
                prompt_set_sha=rt.prompt_set_sha,
                acl_fingerprint=acl_fingerprint(req.subject_ctx),
                degraded=preflight,
            )
            budget = req.budget
            ledger = rt.new_ledger(
                max_steps=budget.max_steps if budget else None,
                max_tokens=budget.max_tokens if budget else None,
                max_cost_usd=budget.max_cost_usd if budget else None,
                deadline_ms=budget_ms,
            )
            ctx = new_run_context(
                domain=rt.pack.name,
                sink=sink,
                ledger=ledger,
                subject=req.subject_ctx,
                session_id=req.session_id,
                config_version=req.config_version or "cfg-local",
                task_id=task.task_id,
            )
            store.set_state(task.task_id, State.working)
            with rt.use(ctx):
                try:
                    result = rt.run_graph(graph, {"symptom": req.symptom}, thread_id=task.task_id)
                    _finish(store, task.task_id, result, sink, ledger, rt.pack.stop.on_budget)
                except KwError as exc:
                    sink.error(exc)
                    store.set_state(task.task_id, State.failed)
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    sink.error(KwError(Code.INTERNAL, "内部错误", internal=f"{type(exc).__name__}: {exc}"))
                    store.set_state(task.task_id, State.failed)
                finally:
                    try:
                        sink.close()
                    except ContractViolation as exc:
                        print(f"[sse] 契约违规 trace={sink.trace_id}: {exc}", file=sys.stderr)
            channel.close()
            tracing.end_http_span(span, token, status_code=200)

        import asyncio

        # 同知识域：必须 to_thread 才能把 contextvars（trace_id）带进工作线程
        asyncio.create_task(asyncio.to_thread(work))

        return StreamingResponse(
            channel.stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={**SSE_HEADERS, **({"X-Degraded": ",".join(d.value for d in preflight)} if preflight else {})},
        )

    # ------------------------------------------------------------------ 查询
    @app.get("/v1/fulfillment/tasks/{task_id}")
    def get_task(task_id: str) -> JSONResponse:
        try:
            task = store.get(task_id)
        except KwError as exc:
            return http_util.error_response(exc, tracing.current_trace_id() or None)
        return JSONResponse(content=task.model_dump(mode="json", exclude_none=True))

    # ------------------------------------------------------------------ 恢复
    @app.post("/v1/fulfillment/tasks/{task_id}/resume")
    async def resume_task(task_id: str, request: Request):
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            http_util.check_contract(headers, settings.contract_version)
            task = store.get(task_id)
            req = await http_util.parse_body(request, ResumeRequest)
            if task.state not in RESUMABLE:
                raise KwError(
                    Code.TASK_STATE_CONFLICT,
                    f"任务当前状态 {task.state.value} 不可恢复",
                )
            # 恢复必须重新校验主体：恢复不是"延续旧权限"（docs/05 §5 出口 6）
            preflight = list(rt.guard.check_subject(req.subject_ctx))
            resume_value = _resume_value(req)
            budget_ms = http_util.deadline_ms(headers, settings.default_deadline_ms)
        except KwError as exc:
            return http_util.error_response(exc, tracing.current_trace_id() or None)

        channel = Channel()
        span, token = tracing.begin_http_span(
            "POST", "/v1/fulfillment/tasks/resume", headers, domain=rt.pack.name, service_version=settings.service_version
        )

        def work() -> None:
            sink = EventSink(
                channel.emit,
                trace_id=tracing.current_trace_id() or task.task_id,
                contract_version=settings.contract_version,
                config_version="cfg-local",
                prompt_set_sha=rt.prompt_set_sha,
                acl_fingerprint=acl_fingerprint(req.subject_ctx),
                degraded=preflight,
            )
            ledger = rt.new_ledger(deadline_ms=budget_ms)
            ctx = new_run_context(
                domain=rt.pack.name,
                sink=sink,
                ledger=ledger,
                subject=req.subject_ctx,
                session_id=task.session_id,
                config_version="cfg-local",
                task_id=task.task_id,
            )
            store.set_state(task.task_id, State.working)
            with rt.use(ctx):
                try:
                    # resume 走 Command(resume=...)，不重跑整张图（docs/13 §2）
                    result = rt.run_graph(graph, {}, thread_id=task.task_id, resume=resume_value)
                    _finish(store, task.task_id, result, sink, ledger, rt.pack.stop.on_budget)
                except KwError as exc:
                    sink.error(exc)
                    store.set_state(task.task_id, State.failed)
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    sink.error(KwError(Code.INTERNAL, "内部错误", internal=f"{type(exc).__name__}: {exc}"))
                    store.set_state(task.task_id, State.failed)
                finally:
                    try:
                        sink.close()
                    except ContractViolation as exc:
                        print(f"[sse] 契约违规 trace={sink.trace_id}: {exc}", file=sys.stderr)
            channel.close()
            tracing.end_http_span(span, token, status_code=200)

        import asyncio

        # 同知识域：必须 to_thread 才能把 contextvars（trace_id）带进工作线程
        asyncio.create_task(asyncio.to_thread(work))

        return StreamingResponse(
            channel.stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={**SSE_HEADERS},
        )

    # ------------------------------------------------------------------ 取消
    @app.post("/v1/fulfillment/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> JSONResponse:
        try:
            task = store.get(task_id)
            if task.state not in CANCELABLE:
                raise KwError(Code.TASK_STATE_CONFLICT, f"任务当前状态 {task.state.value} 不可取消")
            store.set_state(task_id, State.canceled)
        except KwError as exc:
            return http_util.error_response(exc, tracing.current_trace_id() or None)
        # 诚实边界：M0 的图跑在线程里，cancel 只阻止后续 resume，不中断已开始的执行。
        # 真正的执行中断要等 asyncio 任务句柄 + 工具侧协作取消，M1 做。
        return JSONResponse(content=store.get(task_id).model_dump(mode="json", exclude_none=True))

    return app


def _resume_value(req: ResumeRequest) -> dict:
    if req.approval is not None:
        return {
            "decision": req.approval.decision.value,
            "approval_id": req.approval.approval_id,
            "comment": req.approval.comment or "",
        }
    if req.clarification is not None:
        return {"fields": dict(req.clarification.fields or {})}
    raise KwError(Code.INVALID_SCHEMA, "resume 必须带 approval 或 clarification")


def _finish(store: TaskStore, task_id: str, result, sink: EventSink, ledger, on_budget: str) -> None:
    """把图结果翻译成"终止事件 + Task 状态"。

    这是两条语义的唯一汇合点：SSE 的终止事件（一次且仅一次）与 Task 的状态迁移。
    合在一起写，是为了避免"流里 done 了但任务还是 working"这类幽灵状态。
    """
    if result.paused:
        payload = result.interrupt or {}
        kind = Kind(payload.get("kind") or "approval")
        approval = ActionProposal(**payload["approval"]) if payload.get("approval") else None
        question = payload.get("question")
        schema = payload.get("schema")
        store.set_pending(task_id, payload)
        store.set_state(
            task_id,
            State.input_required,
            pending=Pending(kind=kind, approval=approval, question=question),
            usage=ledger.usage(),
        )
        sink.input_required(task_id=task_id, kind=kind, approval=approval, question=question, schema=schema)
        return

    if result.stopped:
        finish = "abstained" if on_budget == "abstain" else "escalated"
        store.set_state(task_id, State.failed if finish == "abstained" else State.completed, usage=ledger.usage())
        sink.done(FinishReason(finish), ledger.usage(), task_id=task_id)
        return

    reason = str(result.state.get("finish_reason") or "escalated")
    if reason not in ("answered", "abstained", "escalated", "degraded"):
        reason = "escalated"
    state = State.completed if reason in ("answered", "escalated") else State.failed
    store.set_state(
        task_id,
        state,
        usage=ledger.usage(),
        pending=None,
        result=Result(
            root_cause=result.state.get("root_cause"),
            confidence=result.state.get("confidence"),
            summary=(result.state.get("diagnosis") or "")[:500] or None,
        )
        if reason == "answered"
        else None,
    )
    sink.done(FinishReason(reason), ledger.usage(), task_id=task_id)
