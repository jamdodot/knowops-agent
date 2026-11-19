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
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import a2a  # noqa: E402  跨域调用的唯一通道（docs/13 §8）
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

#: 请求可指定的图。**必须是白名单**：req.graph 最终会拼进 import_module，
#: 让客户端决定导入哪个模块等于把模块加载权交出去。
#: 名字刻意与领域概念解耦（pipeline / agentic 是控制流风格，不是域名）。
_GRAPH_CHOICES: dict[str, str] = {
    "pipeline": "graph_pipeline",
    "agentic": "graph",
}


def create_app(settings: Settings | None = None, *, domain_dir: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    tracing.setup(settings.trace_export)

    rt = build_runtime(domain_dir or DOMAINS_ROOT / "knowledge", settings)
    graph = rt.build_graph()
    replay = ReplayCache()

    #: 换图要缓存：build() 每次都重新编译 LangGraph，放在请求路径上等于每问一次多花几十毫秒。
    #: 加锁是因为首次并发请求可能同时进来，两边都建一遍图不会错，但没必要。
    _graphs: dict[str, object] = {rt.pack.graph: graph}
    _graphs_lock = threading.Lock()

    def graph_for(name: str | None):
        """缺省返回启动时那张图；显式给名字就按白名单换一张（不存在则编译一次并缓存）。"""
        if not name:
            return graph
        module = _GRAPH_CHOICES.get(name)
        if module is None:
            raise KwError(Code.INVALID_SCHEMA, f"不支持的图：{name}")
        with _graphs_lock:
            hit = _graphs.get(module)
            if hit is None:
                hit = rt.build_graph(module)
                _graphs[module] = hit
            return hit

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
            # 换图在**建流之前**做完：选了不存在的图要能用 4xx 表达，
            # 一旦回了 200 text/event-stream 就只能从流里读 error 帧了。
            run_graph_obj = graph_for(req.graph)
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
                        run_graph_obj,
                        {"query": req.query},
                        # thread_id 带图名：同一个 session 先后跑两种图时，
                        # 共享 checkpointer 会让第二张图读到第一张图的 state。
                        thread_id=(
                            f"{req.session_id}:{req.turn_seq}:{req.subject_ctx.subject_epoch}"
                            f":{req.graph or 'default'}"
                        ),
                    )
                    finish = _finish_reason(result, rt.pack.stop.on_budget, sink)
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

    # ------------------------------------------------------------------ 文档入库
    # 上传文件 → 临时落盘 → 复用 retrieval.pg_ingest.ingest_path 解析+分块+写库。
    # 切片参数（child_size/parent_size/overlap/strategy）由前端透传，缺省用 ChunkConfig 默认。
    @app.post("/v1/knowledge/documents")
    async def upload_document(
        file: UploadFile = File(...),
        child_size: int = Form(320),
        parent_size: int = Form(1200),
        overlap: int = Form(60),
        strategy: str = Form("semantic"),
        acl_mode: str = Form("public"),
    ) -> JSONResponse:
        # 后缀白名单：与 parse 路由支持的格式对齐，避免把任意字节丢进解析器
        allow = {".md", ".txt", ".pdf", ".docx", ".pptx", ".xlsx", ".markdown"}
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in allow:
            return JSONResponse(
                status_code=400,
                content={"error": f"不支持的文件类型 {suffix or '(无后缀)'}", "allow": sorted(allow)},
            )

        # 临时落盘：解析引擎依赖真实文件路径（PDF 走 PyMuPDF、office 走 Docling）
        import tempfile
        import uuid

        from parse.chunker import ChunkConfig
        from retrieval import pg_ingest
        from retrieval.dsn import pg_dsn

        dsn = pg_dsn()
        if not dsn:
            return JSONResponse(
                status_code=503,
                content={"error": "数据库未配置，无法入库（检查 config/.env 的 POSTGRES_*）"},
            )

        # 校验切片参数范围，防止越界值把分块搞坏
        try:
            cfg = ChunkConfig(
                strategy=strategy if strategy in ("semantic",) else "semantic",
                child_size=max(50, min(int(child_size), 4000)),
                parent_size=max(100, min(int(parent_size), 8000)),
                overlap=max(0, min(int(overlap), 500)),
            )
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={"error": "切片参数必须是整数"})

        # acl：本期只支持 public / dept，前端默认 public（全员可见）
        acl: dict[str, object]
        if acl_mode == "public":
            acl = {"v": "public", "depts": [], "roles": [], "groups": [], "acl_epoch": 1}
        elif acl_mode == "dept":
            acl = {"v": "scoped", "depts": ["D10"], "roles": [], "groups": [], "acl_epoch": 1}
        else:
            return JSONResponse(status_code=400, content={"error": "acl_mode 只支持 public / dept"})

        # 落盘到临时目录，用真实文件名（保留后缀供解析器路由）
        tmpdir = tempfile.mkdtemp(prefix="knowops_upload_")
        tmp_path = Path(tmpdir) / (Path(file.filename or "doc.txt").name)
        data = await file.read()
        tmp_path.write_bytes(data)

        try:
            import psycopg

            did = f"up-{uuid.uuid4().hex[:12]}"
            with psycopg.connect(dsn) as conn:
                rep = pg_ingest.ingest_path(
                    conn,
                    tmp_path,
                    doc_id=did,
                    title=Path(file.filename or did).stem,
                    acl=acl,
                    embed=True,
                    chunk_cfg=cfg,
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=500,
                content={"error": f"入库失败：{type(exc).__name__}: {exc}"},
            )
        finally:
            # 清理临时文件（尽力而为）
            try:
                import shutil

                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

        if rep.error:
            return JSONResponse(status_code=422, content={"error": rep.error, "doc_id": rep.doc_id})

        return JSONResponse(
            content={
                "ok": True,
                "doc_id": rep.doc_id,
                "title": rep.title,
                "parents": rep.parents,
                "children": rep.children,
                "embedded": rep.embedded,
                "acl": rep.acl,
                "chunk": {
                    "strategy": cfg.strategy,
                    "child_size": cfg.child_size,
                    "parent_size": cfg.parent_size,
                    "overlap": cfg.overlap,
                },
            }
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

    # ------------------------------------------------------------------ A2A
    # 知识域作为**被调方**：排障域诊断到"该不该赔/该走哪个流程"时，来查文档原文。
    # 与排障域挂的是同一个 mount，只是 handler 不同——协议层不认识文档，也不该认识。
    def run_answer(question: str, scope: dict, subject) -> dict:
        import time
        import uuid

        started = time.perf_counter()
        sink = EventSink(
            lambda *_: None,
            trace_id=tracing.current_trace_id() or "a2a",
            contract_version=settings.contract_version,
            config_version="cfg-a2a",
            prompt_set_sha=rt.prompt_set_sha,
            acl_fingerprint=acl_fingerprint(subject),
        )
        ledger = rt.new_ledger(deadline_ms=settings.default_deadline_ms)
        ctx = new_run_context(
            domain=rt.pack.name,
            sink=sink,
            ledger=ledger,
            subject=subject,
            session_id=f"a2a-{uuid.uuid4().hex[:8]}",
            config_version="cfg-a2a",
            task_id=f"a2a_{uuid.uuid4().hex[:8]}",
        )
        # 防环：把上游的委派链带进上下文。agentic 图本身不委派，但 pipeline 图会走
        # `maybe_delegate`，它据此判断"这一跳还能不能往外发"，避免 A→B→A 无限套娃。
        ctx.scratch["delegation_chain"] = a2a.chain_of(scope)

        with rt.use(ctx):
            result = rt.run_graph(graph, {"query": question},
                                  thread_id=f"a2a-{uuid.uuid4().hex[:8]}")
        state = result.state or {}
        answer = str(state.get("answer") or "")

        # `answer` 单独给，不塞进 text：调用方靠它判断"真答了"还是"弃答"。
        # 弃答时 state 仍是 COMPLETED，只有 answer 为空 —— 这正是 `required_key`
        # 存在的理由，否则一次 abstain 会被当成"查到条款了"。
        return {
            "state": "TASK_STATE_INPUT_REQUIRED" if result.paused else "TASK_STATE_COMPLETED",
            "data": {
                "answer": answer,
                "citations": state.get("citations") or [],
                "finish_reason": str(state.get("finish_reason") or ""),
                "evidence_count": len(state.get("evidence") or []),
            },
            "text": answer,
            "status_message": str(state.get("stop_reason") or ""),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "usage": ledger.usage().model_dump(mode="json"),
        }

    a2a.mount(
        app,
        card=a2a.AgentCard(
            id=a2a.SOURCE_KNOWLEDGE,
            name="knowledge-agent",
            description="制度与操作手册问答：带引用的检索式回答（权限在检索前下推）",
            url=f"http://127.0.0.1:{settings.port}" if hasattr(settings, "port") else "http://127.0.0.1:8001",
            version=settings.service_version,
            skills=[
                a2a.Skill(
                    id=a2a.ANSWER_SKILL,
                    name="制度条款问答",
                    description="输入自然语言问题与最小 scope，返回带引用的答案；证据不足时 answer 为空（弃答）。",
                )
            ],
        ),
        handler=run_answer,
    )

    # ------------------------------------------------------------------ 演示页
    # 挂最后：Starlette 按注册顺序匹配，前面定义的路由优先，这里只兜底静态资源。
    # 注意顺序坑：/app 必须注册在 "/" 之前，否则根 mount 会把 /app 吞掉。
    if WEB_ROOT.is_dir():
        app.mount("/app", StaticFiles(directory=str(WEB_ROOT / "app"), html=True), name="toc")
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
            # 判定口径与 `scripts/eval_compare.py` 的 kind() 一致：检索层 case 带 retrieved。
            # 排障域既不是检索也不是端到端问答——它没有 golden 段，只有 root_cause，
            # 按 cases[0] 有没有 `root_cause` 单独认一档，否则会被当成 e2e 去要 golden。
            if cases and "retrieved" in cases[0]:
                kind = "retrieval"
            elif cases and "root_cause" in cases[0]:
                kind = "ops"
            else:
                kind = "e2e"
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


def _finish_reason(result, on_budget: str, sink=None) -> str:
    if result.stopped:
        # 撞预算时先看"已经发出去什么"。正文和引用都发了，用户屏幕上就是一份
        # 带引用的完整答案，此时再落 abstained 等于 API 否认刚交付的内容，
        # 下游还会据此转人工工单。契约里 `degraded` 就是给这种情况留的。
        if sink is not None and sink.delivered:
            return "degraded"
        return "abstained" if on_budget == "abstain" else "escalated"
    reason = str(result.state.get("finish_reason") or "")
    # 图跑完却没给结论，只能算拒答——绝不能默认"answered"，那会把 bug 变成"答了个空的"
    return reason if reason in ("answered", "abstained", "escalated", "degraded") else "abstained"
