"""Runtime：域无关的执行内核。

这个包是项目自己写的那部分（docs/07 §4：Agent Runtime 自研，编排内核复用 LangGraph）。
它的对外形态只有 `Runtime` 一个类，域拿到的能力就是它的方法。

**一条硬约束**：本包里不出现任何具体的域名。抽象是否成立不看设计文档，
看 `tests/test_domain_isolation.py`——它会扫描本目录源码，出现域名即失败。
这么做的理由很实际：抽象一旦漏了，补文档没用，只有测试能拦住下一次顺手加 if。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from api.schemas import Code, DegradedEnum, SubjectContext, Usage

from . import tracing
from .config import DomainPack, LoadedPack, Settings, load_pack
from .errors import KwError
from .events import EventSink, Ledger, RunContext
from .graph import GraphBuilder, GraphResult, run as run_graph
from .guard import Guard
from .llm import LLMProvider, ModelConfig, build_provider
from .memory import InMemoryMemory, MemoryItem, NullMemory, WorkingMemory, namespace_for
from .prompts import PromptLibrary, PromptSpec, Rendered
from .registry import ToolRegistry, ToolResult

SERVICE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVICE_ROOT.parent

_CURRENT: ContextVar[RunContext | None] = ContextVar("knowops_run_ctx", default=None)


def current_context() -> RunContext:
    """模块级取当前运行上下文。

    给**工具实现**用：工具不接受 subject 参数（否则提示注入就能改权限），
    只能从上下文里取。工具跑在线程池里，所以注册表必须把 contextvars 一起带过去，
    见 `registry.ToolRegistry.call`。
    """
    ctx = _CURRENT.get()
    if ctx is None:
        raise KwError(Code.INTERNAL, "没有活跃的运行上下文")
    return ctx


@dataclass
class Runtime:
    pack: DomainPack
    settings: Settings
    tools: ToolRegistry
    guard: Guard
    prompts: PromptLibrary
    models: ModelConfig
    llm: LLMProvider | None
    memory: Any
    working: WorkingMemory
    checkpointer: Any
    _graph_module: Any = None

    # ------------------------------------------------------------------ 上下文
    @contextmanager
    def use(self, ctx: RunContext) -> Iterator[RunContext]:
        token = _CURRENT.set(ctx)
        try:
            yield ctx
        finally:
            _CURRENT.reset(token)

    def current(self) -> RunContext:
        ctx = _CURRENT.get()
        if ctx is None:
            raise KwError(Code.INTERNAL, "没有活跃的运行上下文（节点必须在 rt.use() 内执行）")
        return ctx

    @property
    def prompt_set_sha(self) -> str:
        return self.prompts.prompt_set_sha()

    # ------------------------------------------------------------------ 图
    def graph(self, state_schema: type) -> GraphBuilder:
        return GraphBuilder(self, state_schema, name=self.pack.graph)

    def build_graph(self) -> Any:
        if self._graph_module is None:
            raise KwError(Code.INTERNAL, "pack 未加载 graph 模块")
        built = self._graph_module.build(self)
        if not isinstance(built, GraphBuilder):
            raise KwError(Code.INTERNAL, "pack 的 build() 必须返回 rt.graph(...) 得到的 builder")
        return built.compile()

    def run_graph(self, graph: Any, inputs: dict[str, Any], *, thread_id: str, resume: dict[str, Any] | None = None) -> GraphResult:
        return run_graph(self, graph, inputs, thread_id=thread_id, resume=resume)

    def new_ledger(
        self,
        *,
        max_steps: int | None = None,
        max_tokens: int | None = None,
        max_cost_usd: float | None = None,
        deadline_ms: int | None = None,
    ) -> Ledger:
        """请求级预算账本：请求体里的 budget 覆盖 pack 的默认值，取更严格的那个。"""
        stop = self.pack.stop
        return Ledger.for_run(
            stop,
            max_steps=min(max_steps or stop.max_steps, stop.max_steps),
            max_tokens=min(max_tokens or stop.budget_tokens, stop.budget_tokens),
            max_cost_usd=max_cost_usd,
            deadline_ms=deadline_ms,
        )

    # ------------------------------------------------------------------ 模型
    def llm_call(
        self,
        prompt_id: str,
        *,
        role: str | None = None,
        max_tokens: int | None = None,
        with_tools: bool = False,
        **variables: Any,
    ) -> str:
        ctx = self.current()
        spec: PromptSpec = self.prompts.get(prompt_id)
        rendered: Rendered = self.prompts.render(
            prompt_id,
            tool_descriptions=self.tools.descriptions() if with_tools else (),
            **variables,
        )
        used_role = role or spec.role
        if self.llm is None:
            raise KwError(Code.UPSTREAM_UNAVAILABLE, "模型未配置（KW_LLM_MODE=none）")

        with tracing.span(
            "shared.llm.chat",
            {
                "knowops.domain": ctx.domain,
                "knowops.llm.role": used_role,
                "knowops.prompt.id": rendered.prompt_id,
                "knowops.prompt.version": rendered.version,
                "knowops.prompt.template_sha": rendered.template_sha256,
                "knowops.prompt.render_sha": rendered.render_sha256,
            },
        ) as sp:
            result = self.llm.chat(used_role, rendered, max_tokens=max_tokens)
            tracing.set_attrs(
                sp,
                **{
                    "gen_ai.request.model": result.model,
                    "gen_ai.usage.input_tokens": result.usage.input_tokens,
                    "gen_ai.usage.output_tokens": result.usage.output_tokens,
                    "knowops.llm.cost_usd": result.usage.cost_usd,
                    "knowops.llm.cache_hit": result.cache_hit,
                },
            )
        ctx.ledger.charge(result.usage)
        return result.text

    # ------------------------------------------------------------------ 工具
    def tool(self, name: str, **args: Any) -> ToolResult:
        result = self.tools.call(name, args)
        if not result.ok:
            # 取证失败不是致命错：域要根据"哪个工具失败"决定是降级还是转人工（F3/F4）
            self.degrade(DegradedEnum.tool)
        return result

    # ------------------------------------------------------------------ 记忆
    def recall(self, kind: str, query: str, k: int = 3) -> list[MemoryItem]:
        ctx = self.current()
        if not getattr(self.pack.memory, kind, False):
            return []
        namespace = self._namespace(kind)
        with tracing.span(
            "shared.memory.recall", {"knowops.memory.kind": kind, "knowops.memory.namespace": namespace}
        ) as sp:
            items = self.memory.recall(kind, namespace, query, k)
            tracing.set_attrs(sp, **{"knowops.memory.hits": len(items)})
        return items

    def remember(
        self, kind: str, text: str, *, success: bool = True, meta: dict[str, Any] | None = None
    ) -> MemoryItem | None:
        if not getattr(self.pack.memory, kind, False):
            return None
        return self.memory.write(kind, self._namespace(kind), text, success=success, meta=meta or {})

    def _namespace(self, kind: str) -> str:
        ctx = self.current()
        if kind == "semantic":
            if ctx.subject is None:
                raise KwError(Code.SUBJECT_MISSING, "语义记忆需要 subject_ctx")
            return namespace_for(ctx.subject.user_id)
        # 情景记忆是"历史工单"，按域隔离而不是按用户——
        # 用户隔离会让同一个工单库被切成 N 份，相似案例永远召不回来
        return f"domain:{ctx.domain}"

    # ------------------------------------------------------------------ 事件
    def citation(self, *args: Any, **kw: Any) -> None:
        self.current().sink.citation(*args, **kw)

    def delta(self, *args: Any, **kw: Any) -> None:
        self.current().sink.delta(*args, **kw)

    def progress(self, note: str | None = None) -> None:
        ctx = self.current()
        ctx.sink.progress(node=str(ctx.scratch.get("_node", "?")), step=ctx.ledger.steps, note=note)

    def evidence(self, *args: Any, **kw: Any) -> None:
        self.current().sink.evidence(*args, **kw)

    def propose(self, *args: Any, **kw: Any) -> None:
        self.current().sink.action_proposal(*args, **kw)

    def degrade(self, mark: DegradedEnum) -> None:
        self.current().degrade(mark)

    # ------------------------------------------------------------------ 工作记忆
    def window(self, k: int | None = None) -> list[dict[str, str]]:
        ctx = self.current()
        return self.working.window(ctx.session_id or ctx.trace_id, k) if self.pack.memory.working else []

    def remember_turn(self, role_name: str, content: str) -> None:
        if not self.pack.memory.working:
            return
        ctx = self.current()
        self.working.append(ctx.session_id or ctx.trace_id, role_name, content)

    # ------------------------------------------------------------------ 收尾
    def close(self) -> None:
        self.tools.close()


# ---------------------------------------------------------------------- 装配
def build_runtime(
    pack_dir: Path | str,
    settings: Settings | None = None,
    *,
    checkpointer: Any | None = None,
) -> Runtime:
    settings = settings or Settings.from_env()
    loaded: LoadedPack = load_pack(pack_dir)
    pack = loaded.pack

    registry = ToolRegistry()
    if loaded.tools_module is not None:
        register = getattr(loaded.tools_module, "register", None)
        if not callable(register):
            raise ValueError(f"{pack.name}/tools.py 必须暴露 register(registry)")
        register(registry)
    registry.require(pack.tools)

    models = ModelConfig.load(REPO_ROOT / "config" / "models.yaml")
    prompts = PromptLibrary.load(SERVICE_ROOT / "prompts" / "manifest.yaml", root=SERVICE_ROOT)
    provider = build_provider(settings.llm_mode, models, SERVICE_ROOT / "recordings")

    # 声明了 prompts 就必须都能加载，否则会在运行到某个节点时才炸
    missing = [p for p in pack.prompts if p not in prompts]
    if missing:
        raise ValueError(f"{pack.name} 声明的 prompt 未登记：{missing}")

    if checkpointer is None:
        checkpointer = _build_checkpointer(settings)

    return Runtime(
        pack=pack,
        settings=settings,
        tools=registry,
        guard=Guard(pack.guard),
        prompts=prompts,
        models=models,
        llm=provider,
        memory=InMemoryMemory() if (pack.memory.semantic or pack.memory.episodic) else NullMemory(),
        working=WorkingMemory(),
        checkpointer=checkpointer,
        _graph_module=loaded.graph_module,
    )


def _build_checkpointer(settings: Settings) -> Any:
    """checkpoint 后端。

    M0 用内存：`InMemorySaver` 的语义和 PG 版一致（都能 interrupt/resume），
    区别只在进程重启后丢失——这一点必须写在 README 里，不能让人以为"重启还在"。
    M1 换 `PostgresSaver`，表已在 `deploy/init/01_schema.sql` 预留。
    """
    if not settings.pg_dsn:
        return InMemorySaver()
    try:
        from langgraph.checkpoint.postgres import PostgresSaver  # noqa: PLC0415
    except ImportError as exc:
        raise KwError(
            Code.INTERNAL,
            "配置了 KW_PG_DSN 但未安装 langgraph-checkpoint-postgres",
            internal="pip install langgraph-checkpoint-postgres psycopg[binary]",
        ) from exc
    return PostgresSaver.from_conn_string(settings.pg_dsn)


# ---------------------------------------------------------------------- 工具
def acl_fingerprint(subject: SubjectContext) -> str:
    """主体属性指纹：语义缓存 key 与审计对齐用（docs/13 §3.1）。

    只取会影响"能看到什么"的字段，`subject_epoch` 也在内——
    否则权限版本变了、指纹没变，语义缓存会命中旧答案（07 §3.4 的同一类坑）。
    """
    material = {
        "user_id": subject.user_id,
        "dept_paths": sorted(subject.dept_paths),
        "roles": sorted(subject.roles),
        "groups": sorted(subject.groups or []),
        "approvals": sorted(subject.approval_doc_ids or []),
        "epoch": subject.subject_epoch,
    }
    payload = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def new_run_context(
    *,
    domain: str,
    sink: EventSink,
    ledger: Ledger,
    subject: SubjectContext | None = None,
    session_id: str = "",
    turn_seq: int = 1,
    config_version: str = "cfg-unknown",
    task_id: str | None = None,
) -> RunContext:
    return RunContext(
        domain=domain,
        trace_id=sink.trace_id,
        sink=sink,
        ledger=ledger,
        session_id=session_id,
        turn_seq=turn_seq,
        subject=subject,
        acl_fingerprint=acl_fingerprint(subject) if subject else "",
        config_version=config_version,
        task_id=task_id,
        degraded=list(sink.preflight_degraded),
    )


__all__ = [
    "Runtime",
    "Settings",
    "build_runtime",
    "new_run_context",
    "acl_fingerprint",
    "CostModel",
    "Usage",
]

# 便于外部按名字引用成本模型而不必再 import 子模块
CostModel = ModelConfig
