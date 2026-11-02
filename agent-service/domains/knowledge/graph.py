"""知识域图定义：Agentic RAG。

对应 `docs/01` §6 的状态机。这里刻意保留两个"看起来多余"的节点：

- `abstain`：拒答是一条正常路径，不是 `return {}` 的兜底。
  单独成节点才能在 trace 里一眼看出"这轮是拒答，不是失败"（E10 归因靠它）。
- `smalltalk`：寒暄直答省掉一次检索。没有它，每个"你好"都要走一遍向量检索 + 两次 LLM 调用。

路由函数是**纯函数**（只看 state），阈值来自 `domain.yaml` 的 `stop.evidence_threshold`——
所以调阈值不需要改代码，也不会出现"代码里 0.75、文档里 0.8"这种两套数。
"""

from __future__ import annotations

from typing import Any, TypedDict

from runtime import Runtime

from . import nodes

NAME = "knowledge_agentic_rag"


class State(TypedDict, total=False):
    query: str
    need_retrieval: bool
    query_type: str
    sub_questions: list[str]
    tried: list[str]
    rounds: int
    evidence: list[dict[str, Any]]
    sufficiency: float
    answer: str
    citations: list[dict[str, Any]]
    finish_reason: str
    stop_reason: str
    bad_eids: list[str]


def build(rt: Runtime):
    g = rt.graph(State)
    (g.node("analyze_intent", nodes.analyze_intent)
      .node("plan", nodes.plan)
      .node("retrieve", nodes.retrieve)
      .node("grade_evidence", nodes.grade_evidence)
      .node("rewrite_query", nodes.rewrite_query)
      .node("synthesize", nodes.synthesize)
      .node("verify_citations", nodes.verify_citations)
      .node("smalltalk", nodes.smalltalk)
      .node("abstain", nodes.abstain))

    g.entry("analyze_intent")
    g.route("analyze_intent", _after_intent, {"plan": "plan", "direct": "smalltalk"})
    g.edge("plan", "retrieve")
    g.edge("retrieve", "grade_evidence")
    g.route(
        "grade_evidence",
        _after_grade(rt),
        {"synthesize": "synthesize", "rewrite": "rewrite_query", "abstain": "abstain"},
    )
    g.edge("rewrite_query", "retrieve")
    g.edge("synthesize", "verify_citations")
    g.route("verify_citations", _after_verify, {"end": "END", "abstain": "abstain"})
    g.edge("smalltalk", "END")
    g.edge("abstain", "END")
    return g


def _after_intent(state: State) -> str:
    return "plan" if state.get("need_retrieval", True) else "direct"


def _after_grade(rt: Runtime):
    threshold = rt.pack.stop.evidence_threshold or 0.75
    max_rounds = 2  # docs/01 §6：rewrite 最多 2 轮

    def route(state: State) -> str:
        if float(state.get("sufficiency") or 0.0) >= threshold:
            return "synthesize"
        if int(state.get("rounds") or 0) < max_rounds:
            return "rewrite"
        return "abstain"

    return route


def _after_verify(state: State) -> str:
    return "end" if state.get("finish_reason") == "answered" else "abstain"
