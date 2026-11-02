"""事件汇聚与预算账本。

这个模块存在的唯一理由：**把契约的顺序约束变成代码不变量**（docs/13 §4.3）。

为什么不能让各域自己 `yield` 事件：
- "meta 必须第一帧"、"citation 必须先于引用它的 delta"、"done/error 有且仅有一个"
  这三条只要有一条靠自觉，就一定会出现"客户端永远挂着"或"引用编号错位"。
- 所以所有事件都必须经过 EventSink，违反约束时**当场抛 ContractViolation**，
  而不是发出去让前端猜。

代价是多一层对象；收益是 `tests/test_event_order.py` 能证明约束成立，而不是文档里写着成立。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from api.schemas import (
    ActionProposal,
    ActionProposalEvent,
    Citation,
    CitationEvent,
    Code,
    DegradedEnum,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    EvidenceEvent,
    FinishReason,
    InputRequiredEvent,
    Kind,
    MetaEvent,
    ProgressEvent,
    SubjectContext,
    Usage,
    Verdict,
)

from .config import StopCfg
from .errors import BudgetStop, ContractViolation, KwError

#: 传输层回调：(seq, event_type, data_json) -> None
EmitFn = Callable[[int, str, str], None]


@dataclass
class Ledger:
    """预算账本：步数 / token / 成本 / 剩余时间，四者任一超限即硬停。

    放在 runtime 而不是各域里，因为"超预算怎么办"是全局策略，
    "超预算了落成什么语义"才是域策略（`stop.on_budget`）。
    """

    max_steps: int
    max_tokens: int
    max_cost_usd: float | None
    deadline_at: float | None = None
    steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    @classmethod
    def for_run(
        cls,
        stop: StopCfg,
        *,
        max_steps: int | None = None,
        max_tokens: int | None = None,
        max_cost_usd: float | None = None,
        deadline_ms: int | None = None,
    ) -> Ledger:
        return cls(
            max_steps=max_steps or stop.max_steps,
            max_tokens=max_tokens or stop.budget_tokens,
            max_cost_usd=max_cost_usd,
            deadline_at=(time.monotonic() + deadline_ms / 1000) if deadline_ms else None,
        )

    # ------------------------------------------------------------------
    def enter_node(self) -> int:
        self.steps += 1
        if self.steps > self.max_steps:
            raise BudgetStop("steps", f"steps={self.steps} > {self.max_steps}")
        self.check_clock()
        return self.steps

    def charge(self, usage: Usage | None) -> None:
        if usage is None:
            return
        self.tokens_in += usage.input_tokens or 0
        self.tokens_out += usage.output_tokens or 0
        self.cost_usd += usage.cost_usd or 0.0
        if self.tokens_in + self.tokens_out > self.max_tokens:
            raise BudgetStop(
                "tokens", f"tokens={self.tokens_in + self.tokens_out} > {self.max_tokens}"
            )
        if self.max_cost_usd is not None and self.cost_usd > self.max_cost_usd:
            raise BudgetStop("cost", f"cost=${self.cost_usd:.4f} > ${self.max_cost_usd}")

    def check_clock(self) -> None:
        if self.deadline_at is not None and time.monotonic() > self.deadline_at:
            raise BudgetStop("deadline", "X-Deadline-Ms 已耗尽")

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def usage(self) -> Usage:
        return Usage(
            input_tokens=self.tokens_in,
            output_tokens=self.tokens_out,
            cost_usd=round(self.cost_usd, 6),
            steps=self.steps,
        )


@dataclass
class RunContext:
    """一次请求的运行时上下文。图节点通过 `rt.current()` 拿到它。"""

    domain: str
    trace_id: str
    sink: EventSink
    ledger: Ledger
    session_id: str = ""
    turn_seq: int = 1
    config_version: str = "cfg-unknown"
    subject: SubjectContext | None = None
    acl_fingerprint: str = ""
    task_id: str | None = None
    #: 域自定义的中间态（例如"已产生的引用"），不参与契约
    scratch: dict[str, Any] = field(default_factory=dict)
    degraded: list[DegradedEnum] = field(default_factory=list)

    def degrade(self, mark: DegradedEnum) -> None:
        """显式降级：唯一入口。禁止静默降级（config/models.yaml 纪律）。"""
        if mark not in self.degraded:
            self.degraded.append(mark)
        if self.sink.started:
            self.sink.note_degraded(mark)


class EventSink:
    """SSE 事件出口，带契约不变量守卫。

    状态机：
        (init) --meta--> running --done/error--> closed
                          └── input_required --> paused（流关闭但不是失败）
    """

    def __init__(
        self,
        emit: EmitFn,
        *,
        trace_id: str,
        contract_version: str,
        config_version: str,
        prompt_set_sha: str | None = None,
        acl_fingerprint: str | None = None,
        degraded: list[DegradedEnum] | None = None,
    ) -> None:
        self._emit = emit
        self.trace_id = trace_id
        self.contract_version = contract_version
        self.config_version = config_version
        self.prompt_set_sha = prompt_set_sha
        self.acl_fingerprint = acl_fingerprint
        self.preflight_degraded: list[DegradedEnum] = list(degraded or [])
        self.late_degraded: list[DegradedEnum] = []

        self._seq = 0
        self.started = False
        self.closed = False
        self.paused = False
        self._terminal: str | None = None
        self._eids: set[str] = set()
        self._citation_sent = False
        self._delta_started = False
        self._citations: list[Citation] = []

    # ------------------------------------------------------------- 内部工具
    def _put(self, event: str, payload: dict[str, Any]) -> None:
        self._seq += 1
        self._emit(self._seq, event, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def _ensure_running(self, allow_paused: bool = False) -> None:
        if self.closed:
            raise ContractViolation(f"流已关闭，不能再发事件（terminal={self._terminal}）")
        if self.paused and not allow_paused:
            raise ContractViolation("已经 input_required，本流不应再发事件（docs/13 §2）")
        if not self.started:
            self.start()

    # ------------------------------------------------------------------ 起始
    def start(self) -> None:
        """发 meta。必须是第一帧，且只发一次（docs/13 §4.2）。"""
        if self.started:
            return
        self.started = True
        self._put(
            "meta",
            MetaEvent(
                trace_id=self.trace_id,
                contract_version=self.contract_version,
                config_version=self.config_version,
                prompt_set_sha=self.prompt_set_sha,
                acl_fingerprint=self.acl_fingerprint,
                degraded=self.preflight_degraded,
            ).model_dump(mode="json", exclude_none=True),
        )

    def note_degraded(self, mark: DegradedEnum) -> None:
        if mark not in self.late_degraded:
            self.late_degraded.append(mark)

    # ------------------------------------------------------------------ 内容
    def citation(self, citations: list[Citation]) -> None:
        self._ensure_running()
        if not citations:
            return
        if self._delta_started:
            # 引用必须先于引用它的正文；正文开吐之后补引用 = 前端已渲染正文，编号必然错位
            raise ContractViolation("citation 不得晚于 delta（docs/13 §4.3 约束 1）")
        for c in citations:
            if c.eid in self._eids:
                raise ContractViolation(f"eid 重复：{c.eid}（正文只能引用出现过的 eid）")
            self._eids.add(c.eid)
            self._citations.append(c)
        self._citation_sent = True
        self._put("citation", CitationEvent(citations=citations).model_dump(mode="json", exclude_none=True))

    def delta(self, text: str, eids: list[str] | None = None) -> None:
        self._ensure_running()
        if not text:
            return
        unknown = set(eids or ()) - self._eids
        if unknown:
            # 这是出口 5（引用生成）的运行时兜底：模型不能引用没出现过的证据
            raise ContractViolation(f"delta 引用了未发出的 eid：{sorted(unknown)}")
        self._delta_started = True
        self._put(
            "delta",
            DeltaEvent(text=text, eids=list(eids) if eids else None).model_dump(
                mode="json", exclude_none=True
            ),
        )

    # ------------------------------------------------------------ 排障域事件
    def progress(self, node: str, step: int, note: str | None = None) -> None:
        self._ensure_running()
        self._put(
            "progress",
            ProgressEvent(node=node, step=step, note=note).model_dump(mode="json", exclude_none=True),
        )

    def evidence(self, hypothesis_id: str, verdict: Verdict, summary: str | None = None) -> None:
        self._ensure_running()
        self._put(
            "evidence",
            EvidenceEvent(hypothesis_id=hypothesis_id, verdict=verdict, summary=summary).model_dump(
                mode="json", exclude_none=True
            ),
        )

    def action_proposal(self, approval: ActionProposal) -> None:
        self._ensure_running()
        self._put(
            "action_proposal",
            ActionProposalEvent(approval=approval).model_dump(mode="json", exclude_none=True),
        )

    # ------------------------------------------------------------------ 终止
    def input_required(
        self,
        *,
        task_id: str,
        kind: Kind,
        approval: ActionProposal | None = None,
        question: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> None:
        """发出后本流关闭，task → paused（docs/13 §2）。

        注意它**不是**错误：断流 ≠ 任务失败，调用方应 GET 或 resume 续接。
        """
        self._ensure_running()
        self.paused = True
        self.closed = True
        self._put(
            "input_required",
            InputRequiredEvent(
                task_id=task_id, kind=kind, approval=approval, question=question, schema=schema
            ).model_dump(mode="json", by_alias=True, exclude_none=True),
        )

    def done(
        self,
        finish_reason: FinishReason,
        usage: Usage,
        *,
        citations: list[Citation] | None = None,
        task_id: str | None = None,
    ) -> None:
        self._ensure_running()
        self._terminal_once("done")
        merged = list(self.preflight_degraded) + [d for d in self.late_degraded if d not in self.preflight_degraded]
        self._put(
            "done",
            DoneEvent(
                finish_reason=finish_reason,
                usage=usage,
                citations=citations if citations is not None else (list(self._citations) or None),
                task_id=task_id,
                degraded=merged or None,
            ).model_dump(mode="json", exclude_none=True),
        )
        self.closed = True

    def error(self, exc: KwError) -> None:
        if self.closed:
            raise ContractViolation("已终止的流不能再发 error（done 与 error 只能有一个）")
        if not self.started:
            self.start()
        self._terminal_once("error")
        self._put(
            "error",
            ErrorEvent(
                exc.to_contract(self.trace_id)
            ).model_dump(mode="json", exclude_none=True),
        )
        self.closed = True

    def _terminal_once(self, kind: str) -> None:
        if self._terminal is not None:
            raise ContractViolation(f"重复终止：已有 {self._terminal}，又要发 {kind}")
        self._terminal = kind

    # ------------------------------------------------------------------ 收尾
    def close(self) -> None:
        """流结束前的最终检查：没有终止事件就是 bug，必须炸出来而不是静默挂断。"""
        if not self.started:
            # 连 meta 都没发就结束了：说明失败发生在非常早的阶段，补一个 error 更友好
            self.start()
        if self._terminal is None and not self.paused:
            raise ContractViolation("图执行结束但没有 done/error/input_required——客户端会一直挂着")

    # ------------------------------------------------------------------ 只读
    @property
    def sequence(self) -> int:
        return self._seq

    @property
    def citations(self) -> list[Citation]:
        return list(self._citations)

    @property
    def unknown_code(self) -> Code:  # pragma: no cover - 仅供调试
        return Code.INTERNAL
