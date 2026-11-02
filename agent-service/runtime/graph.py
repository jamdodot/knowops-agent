"""图执行模板。

`docs/01` §3.1 说内核提供"状态机模板，节点可插拔"。落到代码就是这一层的两个职责：

1. **埋点不可绕过**：节点只能通过 `builder.node()` 加进来，包装在这里做。
   如果让域自己写 `sg.add_node("x", fn)`，那"每个节点必须有 span"就变成自觉问题，
   而自觉的结局一定是某个节点忘了埋。这是"唯一入口"式设计，不是封装洁癖。
2. **预算在节点边界检查**：步数超限必须在进入下一个节点之前停，
   跑到一半停会留下半截状态，恢复时不可解释。

图结构（边、条件、循环）仍然由域自己声明——内核不知道检索和假设树的区别，
这正是 01 §3.4 那道自检题要证明的东西。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from api.schemas import Code

from . import tracing
from .errors import BudgetStop, KwError

NodeFn = Callable[[dict[str, Any], Any], dict[str, Any] | None]
RouterFn = Callable[[dict[str, Any]], str]


@dataclass
class GraphResult:
    """一次图执行的结果。

    paused 与 stopped 是两件事，混起来会写出错误的状态迁移：
    - paused：图停在 interrupt 上等人（断流 ≠ 失败，docs/13 §2）
    - stopped：预算耗尽被硬停，需要按 `stop.on_budget` 收尾
    """

    state: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""
    paused: bool = False
    interrupt: dict[str, Any] | None = None
    stopped: bool = False
    elapsed_ms: float = 0.0


class GraphBuilder:
    """StateGraph 的门面：所有节点自动包装 span + 预算检查。"""

    def __init__(self, rt: Any, state_schema: type, *, name: str) -> None:
        self._rt = rt
        self._name = name
        self._sg = StateGraph(state_schema)
        self._nodes: list[str] = []

    # ------------------------------------------------------------------ 节点
    def node(self, name: str, fn: NodeFn) -> GraphBuilder:
        if name in self._nodes:
            raise ValueError(f"节点重复：{name}")
        self._nodes.append(name)
        self._sg.add_node(name, self._wrap(name, fn))
        return self

    def _wrap(self, name: str, fn: NodeFn) -> Callable[[dict[str, Any]], dict[str, Any]]:
        rt = self._rt

        def runner(state: dict[str, Any]) -> dict[str, Any]:
            ctx = rt.current()
            step = ctx.ledger.enter_node()
            previous = ctx.scratch.get("_node")
            ctx.scratch["_node"] = name
            with tracing.span(
                f"{ctx.domain}.node.{name}",
                {
                    "knowops.domain": ctx.domain,
                    "knowops.node": name,
                    "knowops.step": step,
                    "knowops.session_id": ctx.session_id,
                    "knowops.turn_seq": ctx.turn_seq,
                    "knowops.config_version": ctx.config_version,
                },
            ):
                try:
                    out = fn(state, rt) or {}
                except (GraphBubbleUp, BudgetStop, KwError):
                    # interrupt 是靠异常实现的，包一层就废了；预算与业务错误同理，
                    # 必须原样上抛给 runner 处理
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise KwError(
                        Code.INTERNAL,
                        "内部执行错误",
                        internal=f"node={name} {type(exc).__name__}: {exc}",
                    ) from exc
                finally:
                    ctx.scratch["_node"] = previous
            return out

        return runner

    # ------------------------------------------------------------------ 边
    def entry(self, name: str) -> GraphBuilder:
        self._sg.add_edge(START, name)
        return self

    def edge(self, src: str, dst: str) -> GraphBuilder:
        self._sg.add_edge(src, END if dst == "END" else dst)
        return self

    def route(self, src: str, router: RouterFn, mapping: Mapping[str, str]) -> GraphBuilder:
        """条件边。mapping 的 key 是 router 的返回值，value 是目标节点（或 "END"）。"""
        target = {k: (END if v == "END" else v) for k, v in mapping.items()}
        self._sg.add_conditional_edges(src, router, target)
        return self

    def compile(self) -> Any:
        return self._sg.compile(checkpointer=self._rt.checkpointer)

    # ------------------------------------------------------------------ 自检
    def describe(self) -> dict[str, Any]:
        return {"name": self._name, "nodes": list(self._nodes)}


def run(
    rt: Any,
    graph: Any,
    inputs: dict[str, Any] | None,
    *,
    thread_id: str,
    resume: dict[str, Any] | None = None,
) -> GraphResult:
    """执行一次图，并把停止原因写到 span 上（14 §4.2 `graph.run`）。

    注意 resume 走的是 `Command(resume=...)`，**不是**重新 invoke 一次 inputs：
    后者会从头跑图，工具重复调用、重复计费，假设树也变了（docs/13 §2）。
    """
    ctx = rt.current()
    started = time.perf_counter()
    config = {
        "configurable": {"thread_id": thread_id},
        # 留出 3 倍余量：条件边与子步也会消耗 recursion，卡太死会在预算停之前先崩
        "recursion_limit": max(ctx.ledger.max_steps * 3, 12),
    }

    result = GraphResult()
    stop_reason = ""
    paused = False

    with tracing.span(
        f"{ctx.domain}.graph.run",
        {
            "knowops.domain": ctx.domain,
            "knowops.session_id": ctx.session_id,
            "knowops.turn_seq": ctx.turn_seq,
            "knowops.config_version": ctx.config_version,
            "knowops.prompt_set_sha": rt.prompt_set_sha,
        },
    ) as sp:
        try:
            payload = Command(resume=resume) if resume is not None else (inputs or {})
            out = graph.invoke(payload, config)
            result.state = dict(out)
            interrupts = out.get("__interrupt__") if isinstance(out, dict) else None
            if interrupts:
                paused = True
                first = interrupts[0]
                result.interrupt = dict(getattr(first, "value", {}) or {})
                result.paused = True
            stop_reason = str(result.state.get("stop_reason") or ("paused" if paused else "completed"))
        except BudgetStop as stop:
            result.stopped = True
            stop_reason = stop.reason
            result.stop_reason = stop.reason
        finally:
            tracing.set_attrs(
                sp,
                **{
                    "knowops.steps": ctx.ledger.steps,
                    "knowops.stop_reason": None if paused else stop_reason,
                    "knowops.paused": paused or None,
                    "knowops.degraded": [d.value for d in ctx.degraded] or None,
                },
            )

    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if not result.stop_reason:
        result.stop_reason = stop_reason
    return result
