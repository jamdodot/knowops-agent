"""知识域入口（:8001）。

无状态：一个请求一条流，结束即结束（docs/13 §2）。所以这里没有 Task，只有幂等重放。

预检（契约版本 / 主体 / 注入）在建立流**之前**做完，失败直接返回 JSON 错误。
理由：一旦回了 `200 text/event-stream`，再想用 4xx 表达"请求不合法"就没机会了——
客户端只能从流里读 error 帧，而它可能根本没在读。
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from api import http as http_util  # noqa: E402
from api.replay import ReplayCache, idempotency_key  # noqa: E402
from api.schemas import AnswerRequest, Code, FinishReason  # noqa: E402
from api.sse import SSE_HEADERS, Channel, frame  # noqa: E402
from runtime import acl_fingerprint, build_runtime, new_run_context, tracing  # noqa: E402
from runtime.config import Settings  # noqa: E402
from runtime.errors import ContractViolation, KwError  # noqa: E402
from runtime.events import EventSink  # noqa: E402

DOMAINS_ROOT = SERVICE_ROOT / "domains"
#: 演示页在项目根 web/，不在 agent-service 下——它同时展示知识域问答和评测结果，
#: 归给任何一个域都不合适。
WEB_ROOT = SERVICE_ROOT.parent / "web"
EVAL_RUNS = SERVICE_ROOT.parent / "eval" / "runs"

#: run 文件名白名单。(?!.*\.\.) 是必须的：只写 [..._-] 的话 ".." 本身能通过，
#: 再拼上 .json 虽然越不出目录，但没必要留这种余地。
_TAG_RE = re.compile(r"(?!.*\.\.)[A-Za-z0-9_.\-]{1,64}")


def create_app(settings: Settings | None = None, *, domain_dir: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    tracing.setup(settings.trace_export)

    rt = build_runtime(domain_dir or DOMAINS_ROOT / "knowledge", settings)
    graph = rt.build_graph()
    replay = ReplayCache()

    app = FastAPI(title=f"KnowOps {rt.pack.name}", version=settings.service_version)
    app.state.runtime = rt
    app.state.graph = graph

    # ------------------------------------------------------------------ 探针
    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        # 存活探针不碰任何依赖：它挂掉只该因为进程挂了
        return {
            "status": "ok",
            "domain": rt.pack.name,
            "version": settings.service_version,
            "graph": rt.pack.graph,
        }

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        checks: dict[str, object] = {
            "llm": "mock" if rt.llm is not None and settings.llm_mode == "mock" else ("ok" if rt.llm else "missing"),
            # checkpoint 在 M0 是内存实现，不依赖 PG；M1 切 PostgresSaver 后这里必须改成真实探测
            "checkpoint": "memory",
            "pg": "not_required" if not settings.pg_dsn else "configured",
            "prompt_set_sha": rt.prompt_set_sha[:12],
        }
        ready = rt.llm is not None
        return JSONResponse(status_code=200 if ready else 503, content={"ready": ready, "checks": checks})

    # ------------------------------------------------------------------ 问答
    @app.post("/v1/knowledge/answers")
    async def create_answer(request: Request):
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            http_util.check_contract(headers, settings.contract_version)
            req = await http_util.parse_body(request, AnswerRequest)
            preflight = list(rt.guard.check_subject(req.subject_ctx))
            rt.guard.check_input(req.query)
            budget_ms = http_util.deadline_ms(headers, settings.default_deadline_ms)
        except KwError as exc:
            return http_util.error_response(exc, tracing.current_trace_id() or None)

        key = idempotency_key(req, headers.get("idempotency-key"))
        try:
            cached = replay.begin(key)
        except KwError as exc:
            return http_util.error_response(exc, tracing.current_trace_id() or None)

        degraded_header = ",".join(d.value for d in preflight)
        if cached is not None:
            # 直接重放原流（含 meta）。不是只重放 done——客户端可能整条流都丢了，
            # 而重放整条流的 id 单调性与首次完全一致，前端逻辑不用分叉。
            return StreamingResponse(
                iter(cached.frames),
                media_type="text/event-stream; charset=utf-8",
                headers={**SSE_HEADERS, **({"X-Degraded": degraded_header} if degraded_header else {})},
            )

        channel = Channel()
        span, token = tracing.begin_http_span(
            "POST", "/v1/knowledge/answers", headers, domain=rt.pack.name, service_version=settings.service_version
        )
        frames: list[str] = []
        budget = req.budget

        def work() -> None:
            def emit(seq: int, event: str, data: str) -> None:
                frames.append(frame(seq, event, data))
                channel.emit(seq, event, data)

            trace_id = tracing.current_trace_id()
            sink = EventSink(
                emit,
                trace_id=trace_id,
                contract_version=settings.contract_version,
                config_version=req.config_version or "cfg-local",
                prompt_set_sha=rt.prompt_set_sha,
                acl_fingerprint=acl_fingerprint(req.subject_ctx),
                degraded=preflight,
            )
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
                turn_seq=req.turn_seq,
                config_version=req.config_version or "cfg-local",
            )
            ok = False
            with rt.use(ctx):
                try:
                    result = rt.run_graph(
                        graph, {"query": req.query}, thread_id=f"{req.session_id}:{req.turn_seq}:{req.subject_ctx.subject_epoch}"
                    )
                    finish = _finish_reason(result, rt.pack.stop.on_budget)
                    sink.done(FinishReason(finish), ledger.usage())
                    ok = True
                except KwError as exc:
                    if exc.internal:
                        # internal 只进日志与 span，绝不进响应（含文档正文/内部代号）
                        print(f"[kw] {exc.code}: {exc.internal}", file=sys.stderr)
                    sink.error(exc)
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    sink.error(KwError(Code.INTERNAL, "内部错误", internal=f"{type(exc).__name__}: {exc}"))
                finally:
                    try:
                        sink.close()
                    except ContractViolation as exc:
                        # 走到这里说明某条路径漏了终止事件——必须留痕，否则只是"客户端挂了"
                        print(f"[sse] 契约违规 trace={sink.trace_id}: {exc}", file=sys.stderr)
            channel.close()
            tracing.end_http_span(span, token, status_code=200)
            if ok and sink.closed and sink.sequence:
                replay.commit(key, frames, sink.late_degraded)
            else:
                replay.release(key)

        import asyncio

        # 图是同步的（LangGraph + 工具），丢到线程里跑，事件通过 Channel 回到事件循环。
        # 必须用 asyncio.to_thread：它会复制 contextvars，所以 http span 与 trace_id
        # 在线程里仍然可见。用 loop.run_in_executor 不会复制，症状是 meta.trace_id 为空。
        asyncio.create_task(asyncio.to_thread(work))

        return StreamingResponse(
            channel.stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={**SSE_HEADERS, **({"X-Degraded": degraded_header} if degraded_header else {})},
        )

    # ------------------------------------------------------------------ 调试
    if settings.debug_endpoints:

        @app.get("/_debug/trace/{trace_id}")
        def debug_trace(trace_id: str) -> dict[str, object]:
            """开发期断言工具，不在契约里（不带 X-Contract-Version 也能调）。

            M0 没有 collector，Jaeger/Langfuse 要 M5 才上；但这不意味着"树对不对"不能验证。
            """
            return {"trace_id": trace_id, "spans": tracing.span_tree(trace_id)}

    # ------------------------------------------------------------------ 评测结果
    if settings.debug_endpoints:

        @app.get("/v1/eval/runs")
        def eval_run_list() -> dict[str, object]:
            """列出可展示的跑批结果（只读）。

            列表不带 cases：一份 run 约 30KB，页面先看列表再点进去。
            读不到的跳过——评测产物是本地文件，缺了不该让整个服务起不来。
            """
            runs = [{k: v for k, v in r.items() if k != "cases"} for r in _scan_runs()]
            return {"root": str(EVAL_RUNS), "runs": runs}

        @app.get("/v1/eval/runs/{key}")
        def eval_run_detail(key: str) -> JSONResponse:
            # key 来自 URL：挡住 ../ 这类穿越。eval 产物在项目目录里，不该被路径绕过读别的文件。
            if not _TAG_RE.fullmatch(key):
                return JSONResponse(status_code=400, content={"error": "bad key"})
            for run in _scan_runs():
                if run["key"] == key:
                    return JSONResponse(content=run)
            return JSONResponse(status_code=404, content={"error": "run not found", "key": key})

    # ------------------------------------------------------------------ 演示页
    # 挂最后：Starlette 按注册顺序匹配，前面定义的路由优先，这里只兜底静态资源。
    if WEB_ROOT.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_ROOT), html=True), name="web")

    return app


def _scan_runs() -> list[dict[str, object]]:
    """扫 eval/runs，把每个"含 cases 的顶层子键"当成一个可展示的 run。

    不能假设文件名 == tag == 子键：检索层一次跑批产出两个子键
    （`ret-baseline__rerank0` / `__rerank1`），而顶层 `tag` 是字符串 `ret-baseline`。
    按文件名取 `doc[tag]` 会拿到字符串而不是 payload。

    每次调用都重新读盘、不做缓存：跑完评测刷新页面必须立刻看到新结果，
    而 4 个文件 × 30KB 的读取成本远低于"看到过期数字"的代价。
    """
    runs: list[dict[str, object]] = []
    if not EVAL_RUNS.is_dir():
        return runs

    paths = sorted(EVAL_RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        for key, payload in doc.items():
            if not (isinstance(payload, dict) and isinstance(payload.get("cases"), list)):
                continue
            cases = payload["cases"]
            # 判定口径与 scripts/eval_compare.py 的 kind() 一致：检索层 case 带 retrieved
            kind = "retrieval" if cases and "retrieved" in cases[0] else "e2e"
            runs.append(
                {
                    "key": key,
                    "tag": doc.get("tag") or path.stem,
                    "kind": kind,
                    "created_at": doc.get("created_at"),
                    "git_sha": doc.get("git_sha"),
                    "config": doc.get("config") or {},
                    "summary": payload.get("summary") or {},
                    "n_cases": len(cases),
                    "cases": cases,
                }
            )
    return runs


def _finish_reason(result, on_budget: str) -> str:
    if result.stopped:
        return "abstained" if on_budget == "abstain" else "escalated"
    reason = str(result.state.get("finish_reason") or "")
    # 图跑完却没给结论，只能算拒答——绝不能默认"answered"，那会把 bug 变成"答了个空的"
    return reason if reason in ("answered", "abstained", "escalated", "degraded") else "abstained"
