"""知识域节点。

每个节点只做一件事，并且**只通过 rt 访问外部能力**（模型、工具、事件、记忆）。
这条约束让"节点"变成可替换件：换检索实现不改节点，换模型不改节点。

事件发射的边界要特别注意：节点发 `citation` / `delta`，
但**不发 `done`**——终止事件统一由 API 层在拿到最终 state 后发一次。
这么分是因为"有且仅有一个终止事件"太难靠多个节点自觉保证，
而放在一个地方发，它就变成结构性成立，不依赖纪律。
"""

from __future__ import annotations

import json
import re
from typing import Any

from api.schemas import Citation
from runtime import Runtime

_EID = re.compile(r"\{([A-Za-z0-9_]+)\}")
_SENTENCE = re.compile(r"(?<=[。；！？\n])")


def _json_obj(text: str) -> dict[str, Any]:
    """模型输出 → dict。容忍 ```json 围栏，但不容忍"随便返回一段话"。

    解析失败返回空 dict，由各节点用默认值兜底——注意这是**有意的**：
    模型少给字段是常态，节点必须能退化；但退化路径不能让空答案冒充答案。
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    return data if isinstance(data, dict) else {}


def _history(rt: Runtime) -> str:
    turns = rt.window()
    return "\n".join(f"{t['role']}: {t['content']}" for t in turns) or "（无）"


def _fmt_evidence(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（无）"
    lines = []
    for r in rows:
        lines.append(
            f"[{r['eid']}] 《{r['title']}》{r.get('section_path') or ''} 第{r.get('page_no', '?')}页\n{r['content']}"
        )
    return "\n\n".join(lines)


def _emit_text(rt: Runtime, text: str) -> None:
    """按句切分再流式发出。

    切分不是为了"看起来像在打字"，是因为整段一次性发出会让首 token 时间等于整段生成时间，
    P95 < 4s 的指标就守不住（docs/01 §10）。
    """
    for piece in _SENTENCE.split(text):
        if not piece.strip():
            continue
        eids = [e for e in _EID.findall(piece) if e]
        rt.delta(piece, eids=eids or None)


# --------------------------------------------------------------------- 节点
def analyze_intent(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    raw = rt.llm_call("knowledge.analyze_intent", query=state["query"], history=_history(rt))
    data = _json_obj(raw)
    need = data.get("need_retrieval")
    return {
        "need_retrieval": True if need is None else bool(need),
        "query_type": str(data.get("query_type") or "policy"),
    }


def plan(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    raw = rt.llm_call("knowledge.plan", query=state["query"])
    subs = [s for s in (_json_obj(raw).get("sub_questions") or []) if isinstance(s, str) and s.strip()]
    # 上限 3 个：拆多了会把 top_k 稀释成同义重复（01 §6 表格的"最多 3 个子问题"）
    return {"sub_questions": subs[:3] or [state["query"]], "rounds": 0, "tried": [state["query"]]}


def retrieve(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for question in (state.get("sub_questions") or [state["query"]])[:3]:
        result = rt.tool("hybrid_retrieve", query=question, top_k=5)
        if not result.ok:
            # 检索失败不抛：降级为"证据不足"，由 grade 判 abstain（宁可拒答，不可编造）
            continue
        for row in result.data or []:
            merged.setdefault(row["chunk_id"], row)
    return {"evidence": list(merged.values())[:8]}


def grade_evidence(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    rows = state.get("evidence") or []
    rounds = int(state.get("rounds") or 0) + 1
    if not rows:
        return {"sufficiency": 0.0, "rounds": rounds}
    raw = rt.llm_call(
        "knowledge.grade_evidence", query=state["query"], evidence=_fmt_evidence(rows)
    )
    try:
        score = float(_json_obj(raw).get("sufficiency", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    return {"sufficiency": max(0.0, min(1.0, score)), "rounds": rounds}


def rewrite_query(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    tried = state.get("tried") or []
    raw = rt.llm_call(
        "knowledge.rewrite_query", query=state["query"], tried="\n".join(tried) or "（无）"
    )
    rewritten = [s for s in (_json_obj(raw).get("rewritten") or []) if isinstance(s, str) and s.strip()]
    rewritten = [s for s in rewritten if s not in tried][:2]
    return {"sub_questions": rewritten or [state["query"] + " 制度规定"], "tried": tried + rewritten}


def synthesize(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    rows = state.get("evidence") or []
    citations = [
        Citation(
            eid=r["eid"],
            doc_id=r["doc_id"],
            title=r["title"],
            page_no=r.get("page_no"),
            section_path=r.get("section_path"),
            acl_epoch=r.get("acl_epoch", 0),
        )
        for r in rows
    ]
    # 先吐引用骨架：这样"模型引用了没出现过的 eid"在协议层就不可能（docs/13 §4.3 约束 1）
    rt.citation(citations)
    text = rt.llm_call(
        "knowledge.synthesize",
        query=state["query"],
        evidence=_fmt_evidence(rows),
        history=_history(rt),
    )
    text = rt.guard.desensitize(text)
    _emit_text(rt, text)
    return {"answer": text, "citations": [c.model_dump(mode="json") for c in citations]}


def verify_citations(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    answer = (state.get("answer") or "").strip()
    if not answer:
        return {"finish_reason": "abstained", "stop_reason": "empty_answer"}
    known = {c["eid"] for c in (state.get("citations") or [])}
    cited = set(_EID.findall(answer))
    if not cited:
        # 有证据却一个引用都不给，等于无票作答（05 §5 出口 5）
        return {"finish_reason": "abstained", "stop_reason": "no_citation"}
    unknown = cited - known
    if unknown:
        return {
            "finish_reason": "abstained",
            "stop_reason": "citation_failed",
            "bad_eids": sorted(unknown),
        }
    return {"finish_reason": "answered", "stop_reason": "sufficiency"}


def smalltalk(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    text = rt.llm_call("knowledge.smalltalk", query=state["query"], history=_history(rt))
    text = rt.guard.desensitize(text)
    _emit_text(rt, text)
    return {"answer": text, "finish_reason": "answered", "stop_reason": "direct"}


def abstain(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    reason = state.get("stop_reason") or "insufficient_evidence"
    return {"finish_reason": "abstained", "stop_reason": reason}
