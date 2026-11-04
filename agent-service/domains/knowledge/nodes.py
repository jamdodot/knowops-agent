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

# eid 是 `e_{doc_id}_{chunk_id}`，而 doc_id 来自文件名，中文文档的 doc_id 就是中文。
# 只允许 [A-Za-z0-9_] 会把中文 eid 全部漏掉，verify_citations 于是判"无票作答"，
# 答案明明带了对的引用也被降级成 abstained（2026-11-04 真模型跑出来的）。
_EID = re.compile(r"\{([A-Za-z0-9_\u4e00-\u9fff.\-]+)\}")
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
        # 用 {} 包 eid，和答案要求的引用格式保持一致。
        # 曾用 [eid]，结果模型在正文里抄 `[e_xxx]`、在需要 `{e_xxx}` 时又抄模板占位符，
        # 两种括号并存只会让模型选错一种。
        lines.append(
            f"{{{r['eid']}}} 《{r['title']}》{r.get('section_path') or ''} 第{r.get('page_no', '?')}页\n{r['content']}"
        )
    return "\n\n".join(lines)


def _strip_unknown_eids(text: str, known: set[str]) -> str:
    """把模型自造的引用标记从正文里抹掉。

    最典型的来源是模型把 prompt 里的模板占位符原样抄回来（正文出现 `{eid}` 三个字母）。
    这里**不抛异常**：引用是否成立由 verify 判定——一个有效引用都没有就该拒答，
    而不是让整条链路 INTERNAL（2026-11-04 跑 82 条 e2e 时被这条打挂过）。
    """
    return _EID.sub(lambda m: m.group(0) if m.group(1) in known else "", text)


#: 全角字符归一。模型偶尔会把 `{ｅ_xxx}` 打成全角（尤其句末混排时），
#: 正则匹配不到就等于"没引用"，整题被误判拒答（实测 g-115 即此情况）。
_FW_CITE = str.maketrans({"ｅ": "e", "Ｅ": "E", "｛": "{", "｝": "}", "＿": "_", "－": "-"})


def _normalize_cite_chars(text: str) -> str:
    return text.translate(_FW_CITE)


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
    # 新一轮检索的结果排在前面，**上一轮的证据补在后面而不是丢掉**。
    # 早先这里是整体覆盖：grade 判不足 → rewrite → 覆盖 → 上一轮那条正确证据没了，
    # 模型却还记得它的 eid，于是引用校验失败、正确答案被判"无票作答"降级成拒答。
    # 换查询是为了补证据，不是为了抹掉已有证据。
    merged: dict[str, dict[str, Any]] = {}
    for question in (state.get("sub_questions") or [state["query"]])[:3]:
        result = rt.tool("hybrid_retrieve", query=question, top_k=5)
        if not result.ok:
            # 检索失败不抛：降级为"证据不足"，由 grade 判 abstain（宁可拒答，不可编造）
            continue
        for row in result.data or []:
            merged.setdefault(row["chunk_id"], row)
    for row in state.get("evidence") or []:
        merged.setdefault(row["chunk_id"], row)
    return {"evidence": list(merged.values())[:8]}


# 三档判定 → 分数。阈值（domain.yaml 的 stop.evidence_threshold=0.75）只把
# sufficient 放过，partial 与 insufficient 都进改写/拒答路径。
_VERDICT_SCORE = {"sufficient": 1.0, "partial": 0.5, "insufficient": 0.0}


def grade_evidence(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    rows = state.get("evidence") or []
    rounds = int(state.get("rounds") or 0) + 1
    if not rows:
        return {"sufficiency": 0.0, "rounds": rounds}
    raw = rt.llm_call(
        "knowledge.grade_evidence", query=state["query"], evidence=_fmt_evidence(rows)
    )
    data = _json_obj(raw)
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict in _VERDICT_SCORE:
        score = _VERDICT_SCORE[verdict]
    else:
        # 兼容旧格式（sufficiency 小数）：模型没按新契约输出时不至于整轮判 0
        try:
            score = float(data.get("sufficiency", 0.0))
        except (TypeError, ValueError):
            score = 0.0
    return {
        "sufficiency": max(0.0, min(1.0, score)),
        "rounds": rounds,
        "grade_verdict": verdict or None,
        "grade_missing": str(data.get("missing") or "")[:200],
    }


def rewrite_query(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    tried = state.get("tried") or []
    raw = rt.llm_call(
        "knowledge.rewrite_query", query=state["query"], tried="\n".join(tried) or "（无）"
    )
    rewritten = [s for s in (_json_obj(raw).get("rewritten") or []) if isinstance(s, str) and s.strip()]
    rewritten = [s for s in rewritten if s not in tried][:2]
    # 模型没给出可用改写就**别自己造查询**。之前这里兜底成 "原查询 + 制度规定"，
    # 而"制度"几乎出现在每份制度文档里，等于把一堆不相干文档硬塞进第二轮证据，
    # 最终表现为"越权查询也有召回"。宁可原样再检一轮然后拒答（有据可查的 abstain），
    # 也不要制造看起来合理的噪声。
    return {"sub_questions": rewritten or [state["query"]], "tried": tried + rewritten}


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
    known = {c.eid for c in citations}
    text = _strip_unknown_eids(_normalize_cite_chars(text), known)
    if known and not _EID.findall(text):
        # 模型整段不给引用标记是真实高频失败（82 条里 5 条）：有证据、答案也对，
        # 却因为"无票作答"被 verify 判拒答。补一次引用比直接拒答划算得多——
        # 成本只有一次短补全，收益是把"知识错误"和"格式错误"分开。
        repaired = rt.llm_call(
            "knowledge.synthesize_cite",
            query=state["query"],
            answer=text,
            evidence=_fmt_evidence(rows),
        )
        repaired = _strip_unknown_eids(_normalize_cite_chars(rt.guard.desensitize(repaired)), known)
        if _EID.findall(repaired):
            text = repaired
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


#: 拒答话术。要按 reason 分开：用户看到的不是"系统错误"，而是"这条为什么没答"。
_ABSTAIN_TEXT = {
    "insufficient_evidence": "现有资料未覆盖这个问题，无法给出有依据的答案。",
    "no_citation": "现有资料不足以给出可引用的答案。",
    "citation_failed": "现有资料不足以给出可引用的答案。",
    "empty_answer": "现有资料不足以给出可引用的答案。",
}


def abstain(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    reason = state.get("stop_reason") or "insufficient_evidence"
    # 已经吐过正文就不再补话术：synthesize→verify 降级到这里的路径，
    # 用户已经看到答案了，再追加一句"未覆盖"只会自相矛盾。
    if not (state.get("answer") or "").strip():
        rt.delta(_ABSTAIN_TEXT.get(reason, _ABSTAIN_TEXT["insufficient_evidence"]))
    return {"finish_reason": "abstained", "stop_reason": reason}
