"""排障域节点：假设树的深度优先搜索。

两个关键设计（都是踩过才知道的）：

1. **interrupt 单独成节点（`clarify` / `human_approve`），不和业务逻辑混在一个节点里。**
   LangGraph resume 时会**从头重放**被中断的节点。如果 interrupt 和 LLM 调用写在同一个节点里，
   每次审批恢复都会重新调一次模型：多花钱、结论还可能变。所以中断点必须是幂等的、无副作用的节点。

2. **允许证伪。** `evaluate` 的 verdict 有三个值，`refute` 和 `support` 一样是有效结论。
   只做"确认偏误"式诊断的系统，在 F5（判证偏差）上必然差。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from langgraph.types import interrupt

from api.schemas import ActionProposal, Verdict
from runtime import Runtime
from runtime.guard import risk_of

_SENTENCE = re.compile(r"(?<=[。；！？\n])")


def _json_obj(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if 0 <= start < end:
            try:
                data = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    return data if isinstance(data, dict) else {}


def _emit_text(rt: Runtime, text: str) -> None:
    for piece in _SENTENCE.split(text):
        if piece.strip():
            rt.delta(piece)


# --------------------------------------------------------------------- 节点
def analyze_symptom(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    raw = rt.llm_call("fulfillment.analyze_symptom", symptom=state["symptom"])
    data = _json_obj(raw)
    rt.remember_turn("user", state["symptom"])
    rt.progress(note=f"症状已结构化：订单={data.get('order_id') or '缺失'}，环节={data.get('stage')}")
    return {"fields": data, "refines": 0}


def clarify(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    """缺字段时主动追问，而不是猜（docs/01 §7）。

    这个节点只做一件事：interrupt + 合并答案。resume 重放它不会有副作用。
    """
    fields = dict(state.get("fields") or {})
    missing = [k for k in ("order_id",) if not fields.get(k)]
    if not missing:
        return {"fields": fields}

    answer = interrupt(
        {
            "kind": "clarification",
            "question": "请补充订单号（形如 O20261102001），否则无法定位履约链路。",
            "schema": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "pattern": "^O[0-9]{11}$"}},
                "required": ["order_id"],
            },
        }
    )
    provided = (answer or {}).get("fields") or {}
    for key, value in provided.items():
        if value:
            fields[key] = value
    return {"fields": fields}


def build_hypotheses(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    fields = state.get("fields") or {}
    symptom = f"订单 {fields.get('order_id')} 在 {fields.get('stage')} 环节出现：{fields.get('phenomenon')}"

    cases = rt.recall("episodic", symptom, k=3)
    similar = "\n".join(f"- 根因={c.meta.get('root_cause', '?')}：{c.text}" for c in cases) or "（无相似工单）"

    raw = rt.llm_call("fulfillment.build_hypotheses", symptom=symptom, similar_cases=similar)
    hypotheses = [h for h in (_json_obj(raw).get("hypotheses") or []) if isinstance(h, dict) and h.get("id")]
    cap = rt.pack.stop.max_hypotheses or 4
    kept = hypotheses[:cap]
    rt.progress(note=f"生成 {len(kept)} 个候选根因（上限 {cap}）：" + "、".join(str(h.get("root_cause")) for h in kept))
    return {"hypotheses": kept, "symptom_norm": symptom, "refines": int(state.get("refines") or 0)}


def gather_evidence(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    fields = state.get("fields") or {}
    order_id = fields.get("order_id")
    collected: dict[str, list[dict[str, Any]]] = {}

    for hypothesis in (state.get("hypotheses") or [])[: rt.pack.stop.max_hypotheses or 4]:
        rows: list[dict[str, Any]] = []
        for check in hypothesis.get("checks") or []:
            if check == "lookup_order":
                result = rt.tool("lookup_order", order_id=order_id)
            elif check == "search_logs":
                result = rt.tool("search_logs", keyword="分拨中心")
            elif check == "query_metric":
                result = rt.tool("query_metric", metric="inventory_diff_rate")
            elif check == "check_rule":
                result = rt.tool("check_rule", rule_id="compensation_v2")
            else:
                continue
            rows.append(
                {
                    "tool": check,
                    "ok": result.ok,
                    "summary": result.summary or (result.error or ""),
                }
            )
        collected[str(hypothesis["id"])] = rows
    rt.progress(note=f"按假设取证完成：{sum(len(v) for v in collected.values())} 次工具调用")
    return {"evidence_map": collected}


def evaluate(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    hypotheses = state.get("hypotheses") or []
    evidence_map = state.get("evidence_map") or {}
    verdicts: dict[str, dict[str, Any]] = {}

    for hypothesis in hypotheses:
        rows = evidence_map.get(str(hypothesis["id"])) or []
        text = "\n".join(f"- {r['tool']}: {r['summary']}" for r in rows) or "（无取证结果）"
        raw = rt.llm_call(
            "fulfillment.evaluate",
            hypothesis=f"{hypothesis.get('root_cause')}（先验 {hypothesis.get('prior')}）",
            evidence=text,
        )
        data = _json_obj(raw)
        verdict = str(data.get("verdict") or "irrelevant")
        if verdict not in ("support", "refute", "irrelevant"):
            verdict = "irrelevant"
        try:
            confidence = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        verdicts[str(hypothesis["id"])] = {
            "verdict": verdict,
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(data.get("reason") or ""),
            "root_cause": hypothesis.get("root_cause"),
        }
        rt.evidence(
            hypothesis_id=str(hypothesis["id"]),
            verdict=Verdict(verdict),
            summary=f"{hypothesis.get('root_cause')}：{data.get('reason') or ''}"[:120],
        )

    best_id, best = "", {"confidence": 0.0, "verdict": "irrelevant", "root_cause": ""}
    for hid, item in verdicts.items():
        if item["verdict"] == "support" and item["confidence"] > best["confidence"]:
            best_id, best = hid, item

    rt.progress(
        note=f"判证完成：{sum(1 for v in verdicts.values() if v['verdict'] == 'support')} 支持 / "
        f"{sum(1 for v in verdicts.values() if v['verdict'] == 'refute')} 反驳，最高置信度 {best['confidence']:.2f}"
    )
    return {
        "verdicts": verdicts,
        "confidence": best["confidence"],
        "root_cause": best["root_cause"],
        "best_hypothesis": best_id,
    }


def compose_diagnosis(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    verdicts = state.get("verdicts") or {}
    lines = []
    for hid, item in verdicts.items():
        lines.append(f"假设 {hid}（{item.get('root_cause')}）：{item['verdict']}，{item.get('reason')}")

    text = rt.llm_call(
        "fulfillment.compose_diagnosis",
        symptom=state.get("symptom_norm") or state.get("symptom", ""),
        root_cause=str(state.get("root_cause") or "未确认"),
        evidence="\n".join(lines) or "（无）",
    )
    text = rt.guard.desensitize(text)
    _emit_text(rt, text)
    return {"diagnosis": text}


def propose_action(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    action_type = "retry_push_carrier"
    allowed, reason = rt.guard.action_verdict(action_type)
    if not allowed:
        # 白名单外/红线动作：不提议、不重试，直接转人工（F7 红线）
        rt.progress(note=f"动作被护栏拒绝：{reason}")
        return {"finish_reason": "escalated", "stop_reason": reason or "action_blocked"}

    fields = state.get("fields") or {}
    params = {"order_id": fields.get("order_id"), "carrier": "SF"}
    result = rt.tool("propose_action", action_type=action_type, params=params)
    if not result.ok:
        return {"finish_reason": "escalated", "stop_reason": "propose_failed"}

    digest = hashlib.sha1(f"{params['order_id']}|{action_type}".encode()).hexdigest()[:8]
    proposal = ActionProposal(
        approval_id=f"ar_{digest}",
        action_type=action_type,
        params=params,
        risk_level=risk_of(action_type),
    )
    # 只提议不执行：SSE 出 action_proposal 后进入审批节点（docs/05 §7）
    rt.propose(proposal)
    return {"pending": proposal.model_dump(mode="json")}


def human_approve(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    """HITL 中断点。**只做 interrupt**，不调模型、不调工具。"""
    pending = state.get("pending") or {}
    decision = interrupt(
        {
            "kind": "approval",
            "approval": pending,
            "question": f"是否批准动作 {pending.get('action_type')}（风险 {pending.get('risk_level')}）？",
        }
    )
    verdict = str((decision or {}).get("decision") or "")
    if verdict == "approved":
        rt.remember(
            "episodic",
            f"{state.get('symptom_norm')} → 根因 {state.get('root_cause')}；审批通过并已执行 {pending.get('action_type')}",
            success=True,
            meta={"root_cause": state.get("root_cause")},
        )
        return {"finish_reason": "answered", "stop_reason": "approved"}
    return {"finish_reason": "escalated", "stop_reason": "rejected"}


def escalate(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    reason = state.get("stop_reason") or "not_converged"
    text = f"未能收敛到可执行结论，已转人工复核（原因：{reason}）。已排除的假设与证据链见上方进度。"
    _emit_text(rt, rt.guard.desensitize(text))
    rt.remember("episodic", f"{state.get('symptom_norm')} → 未收敛：{reason}", success=False)
    return {"finish_reason": "escalated", "stop_reason": reason}
