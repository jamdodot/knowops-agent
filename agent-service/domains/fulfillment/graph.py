"""排障域图定义：假设验证（深度优先）。

对应 `docs/01` §7。与知识域共用同一个 Runtime、同一个 GraphBuilder，
但控制流完全相反：知识域是"广度检索 + 早停"，这里是"生成假设 → 逐条取证 → 允许证伪 → 收敛或转人工"。

两处刻意的结构：
- `refine` 单独成节点：只负责 `refines += 1`。把计数塞进路由函数里是不行的——
  路由必须是纯函数，否则同一份 state 两次路由可能给出不同结果，trace 就无法复现。
- 拒绝审批走 `escalate` 而不是回到 `compose_diagnosis`：驳回意味着"人不认可这个结论"，
  在证据不变的情况下重写报告只是换个说法，没有信息增益。
"""

from __future__ import annotations

from typing import Any, TypedDict

from runtime import Runtime

from . import nodes

NAME = "fulfillment_diagnosis"


class State(TypedDict, total=False):
    symptom: str
    symptom_norm: str
    fields: dict[str, Any]
    refines: int
    hypotheses: list[dict[str, Any]]
    evidence_map: dict[str, list[dict[str, Any]]]
    verdicts: dict[str, dict[str, Any]]
    confidence: float
    root_cause: str
    best_hypothesis: str
    diagnosis: str
    pending: dict[str, Any]
    finish_reason: str
    stop_reason: str


def build(rt: Runtime):
    g = rt.graph(State)
    (g.node("analyze_symptom", nodes.analyze_symptom)
      .node("clarify", nodes.clarify)
      .node("build_hypotheses", nodes.build_hypotheses)
      .node("gather_evidence", nodes.gather_evidence)
      .node("evaluate", nodes.evaluate)
      .node("refine", _refine)
      .node("compose_diagnosis", nodes.compose_diagnosis)
      .node("propose_action", nodes.propose_action)
      .node("human_approve", nodes.human_approve)
      .node("escalate", nodes.escalate))

    g.entry("analyze_symptom")
    g.route("analyze_symptom", _need_clarify, {"clarify": "clarify", "go": "build_hypotheses"})
    g.route("clarify", _after_clarify, {"go": "build_hypotheses", "give_up": "escalate"})
    g.edge("build_hypotheses", "gather_evidence")
    g.edge("gather_evidence", "evaluate")
    g.route(
        "evaluate",
        _after_evaluate(rt),
        {"compose": "compose_diagnosis", "refine": "refine", "escalate": "escalate"},
    )
    g.edge("refine", "build_hypotheses")
    g.edge("compose_diagnosis", "propose_action")
    g.route("propose_action", _after_propose, {"approve": "human_approve", "end": "END"})
    g.route("human_approve", _after_approve, {"end": "END", "escalate": "escalate"})
    g.edge("escalate", "END")
    return g


def _refine(state: State) -> dict[str, Any]:
    return {"refines": int(state.get("refines") or 0) + 1}


def _need_clarify(state: State) -> str:
    fields = state.get("fields") or {}
    return "go" if fields.get("order_id") else "clarify"


def _after_clarify(state: State) -> str:
    fields = state.get("fields") or {}
    return "go" if fields.get("order_id") else "give_up"


def _after_evaluate(rt: Runtime):
    threshold = rt.pack.stop.confidence_threshold or 0.8
    max_refines = 1  # 允许一次"补充线索后重建假设"，再多就是穷举

    def route(state: State) -> str:
        if float(state.get("confidence") or 0.0) >= threshold:
            return "compose"
        if int(state.get("refines") or 0) < max_refines:
            return "refine"
        return "escalate"

    return route


def _after_propose(state: State) -> str:
    return "end" if state.get("finish_reason") in ("escalated", "abstained") else "approve"


def _after_approve(state: State) -> str:
    return "end" if state.get("finish_reason") == "answered" else "escalate"
