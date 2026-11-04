"""知识域图定义二：管线式 RAG（M1 基线，用于和 agentic 做对照）。

故意做得"朴素"，这是基线应有的样子：

    retrieve → synthesize → verify_citations

没有 analyze_intent（不判能不能闲聊）、没有 plan（不拆子问题）、
没有 grade_evidence（不判证据够不够）、没有 rewrite_query（不重试）。

## 为什么必须保留这张图

`docs/04` M2 的验收标准是「正确率 **+8pt**」。那个 +8 是相对**这张图**的。
没有基线，agentic 的效果就只是"能跑"，不是"更好"——而且事后补基线最容易自欺：
你会不自觉地用当前的 prompt 和参数去重跑，得出一个虚低的基线来撑大差距。

## 两个刻意不做的"优化"

1. **不做"证据为空就不调用模型"之外的任何兜底**。检索没召回，管线式就是拿着
   空气去生成，这正是它比 agentic 差的地方（E3/E10）。给它加兜底等于提前偷了
   agentic 的分数。
2. **不拆子问题、不调 top_k**。多跳问题在这里必然只命中一半证据，这是能力差异，
   不是实现缺陷。

## 保留的部分

`verify_citations` 保留。它是**引用完整性**保护（"无票作答"），不是 agentic 特性；
把它从基线里拿掉，基线会因为"编造引用"而虚低，同样不可比。
"""

from __future__ import annotations

from typing import Any, TypedDict

from runtime import Runtime

from . import nodes

NAME = "knowledge_pipeline"


class State(TypedDict, total=False):
    query: str
    evidence: list[dict[str, Any]]
    answer: str
    citations: list[dict[str, Any]]
    finish_reason: str
    stop_reason: str
    bad_eids: list[str]


def build(rt: Runtime):
    g = rt.graph(State)
    (g.node("retrieve", _retrieve).node("synthesize", nodes.synthesize).node(
        "verify_citations", nodes.verify_citations
    ).node("abstain", nodes.abstain))

    g.entry("retrieve")
    g.route(
        "retrieve",
        lambda s: "synthesize" if s.get("evidence") else "abstain",
        {"synthesize": "synthesize", "abstain": "abstain"},
    )
    g.edge("synthesize", "verify_citations")
    g.route("verify_citations", _after_verify, {"end": "END", "abstain": "abstain"})
    g.edge("abstain", "END")
    return g


def _retrieve(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    """单 query 检索。复用 agentic 的 `retrieve` 会带进 sub_questions 逻辑，
    这里显式写成一次调用，让"基线只检一次"这件事在代码里看得见。"""
    result = rt.tool("hybrid_retrieve", query=state["query"], top_k=5)
    if not result.ok:
        return {"evidence": [], "stop_reason": "retrieve_failed"}
    return {"evidence": list(result.data or [])[:8]}


def _after_verify(state: dict[str, Any]) -> str:
    return "end" if state.get("finish_reason") == "answered" else "abstain"
