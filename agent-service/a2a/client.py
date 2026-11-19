"""A2A 客户端：跨域调用的唯一出口。

支持两个方向：
- 知识域 → 排障域：`delegate_diagnosis`，问"这一单为什么卡住"
- 排障域 → 知识域：`ask_knowledge`，问"这种情况制度/赔付条款怎么规定"

两者共用 `send_message`，差异只在 skill id 与必需字段。这样加第三个域不用再写一份
HTTP/降级/traceparent 代码，也不会出现"一边带 trace、一边忘带"的分叉。

三条硬约束（docs/13 §5.1 / §8）：

1. **子预算必须小于父预算。** 这里默认 30s 上限，且要求调用方传入"父剩余时间"，
   实取 `min(父剩余 - 500ms, 30s)`。少了那 500ms 会出现"子调用成功、父请求超时"
   ——trace 上看着全绿，用户那边是 504。
2. **委派只带最小 scope，不带 subject_ctx 全量。** 主体只传求权必需的字段，
   权限清单、审批单这些留在本域——传过去等于把本域的授权判断外包给对方。
3. **对端不可用必须降级，不能重试到超时。** 主职能永远优先于加分项：
   知识域为了排障追问把主答案拖死、排障域为了查条款把诊断拖死，是同一个错误。

降级语义统一用 `DegradedEnum.a2a`，让上游能看到"这次答案缺了什么"。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .types import SOURCE_FULFILLMENT, SOURCE_KNOWLEDGE, extend_scope

#: 默认给 A2A 的上限。再大没有意义：诊断本身 p95 已在 30s 附近。
DEFAULT_TIMEOUT_MS = 30_000

#: 留给父级组装响应的余量
PARENT_RESERVE_MS = 500

#: 排障域在 Agent Card 里声明的 skill
DIAGNOSE_SKILL = "diagnose_order_symptom"

#: 知识域在 Agent Card 里声明的 skill
ANSWER_SKILL = "answer_knowledge_question"


@dataclass
class PeerReply:
    """一次 A2A 调用的原始回复。域特定字段都在 `data` 里，由调用方自己解释。"""

    ok: bool
    state: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    degraded: str | None = None          # a2a_unreachable / a2a_timeout / a2a_failed / ...
    elapsed_ms: float = 0.0
    card_name: str = ""


@dataclass
class DelegateResult:
    ok: bool
    root_cause: str | None = None
    text: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    state: str = ""
    degraded: str | None = None          # a2a_unreachable / a2a_timeout / a2a_failed
    elapsed_ms: float = 0.0
    card_name: str = ""

    def as_evidence(self) -> str:
        """拼成一段给 synthesize 用的事实陈述。

        为什么不用 JSON：这段文本会进 prompt 的 evidence 块，模型对
        "结论 + 一句话依据"的格式比嵌套 JSON 稳健得多（实测 JSON 会被它改写）。
        """
        if not self.ok:
            return f"（排障域未返回结论：{self.degraded}）"
        return f"排障域诊断结论：{self.root_cause}（置信度 {self.data.get('confidence')}）。{self.text or ''}".strip()


def send_message(
    query: str,
    *,
    skill_id: str,
    subject: dict[str, Any],
    scope: dict[str, Any] | None = None,
    base_url: str,
    parent_budget_ms: int | None = None,
    required_key: str | None = None,
    reject: dict[str, Any] | None = None,
    transport: Any = None,
) -> PeerReply:
    """发一个 A2A SendMessage，等结果。

    `transport` 只为可测性存在：注入一个 httpx.MockTransport 就能验证
    "对端超时/连不上/缺 skill" 三种降级，不用真起两个进程。生产路径传 None。

    `base_url` 由调用方给（配置里写死），但**先读一次 Agent Card**：
    这不只是为了"发现"，而是拒绝一个能力不匹配的对端——如果对端没有我们要的
    skill，就不应该把请求发过去。对端把能力删了/改名了，这里要失败得明确，
    而不是发过去等它返回一个语义不明的空结果。

    `required_key` 是"什么算成功"的判据：状态完成还不够，产物里必须有这个字段。
    少了这条，"对端跑完了但什么也没答出来"会被当成成功上报。

    `reject` 补的是同一个漏洞的另一半：**字段存在但内容是"我没查到"**。
    知识域弃答时仍会把兜底话术写进 `answer`（前端要显示这句话），
    于是 `required_key="answer"` 照样为真——把弃答读成"拿到条款"。
    按值排除而不是按空判断，是因为那句话必须继续给用户看。
    """
    import httpx

    from runtime import tracing

    started = time.perf_counter()
    timeout_ms = _budget(parent_budget_ms)
    headers = {"Content-Type": "application/json"}
    tracing.inject(headers)          # traceparent 出站注入：跨进程后仍是一棵树

    try:
        with httpx.Client(timeout=timeout_ms / 1000, transport=transport) as client:
            card = _fetch_card(client, base_url, headers)
            if card is None:
                return PeerReply(ok=False, degraded="a2a_unreachable", elapsed_ms=_ms(started))

            skill_ids = {s.get("id") for s in card.get("skills") or []}
            if skill_id not in skill_ids:
                return PeerReply(ok=False, degraded="a2a_capability_mismatch",
                                 elapsed_ms=_ms(started), card_name=str(card.get("name")))

            body = {
                "jsonrpc": "2.0",
                "id": f"req_{int(time.time() * 1000)}",
                "method": "SendMessage",
                "params": {
                    "message": {"role": "user", "parts": [{"text": query}]},
                    "metadata": {"subject": subject, "scope": scope or {}},
                },
            }
            resp = client.post(f"{base_url.rstrip('/')}/a2a", json=body, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.TimeoutException:
        return PeerReply(ok=False, degraded="a2a_timeout", elapsed_ms=_ms(started))
    except Exception:  # noqa: BLE001  连接失败、5xx、JSON 坏了，对调用方都是"对端不可用"
        return PeerReply(ok=False, degraded="a2a_unreachable", elapsed_ms=_ms(started))

    if "error" in payload:
        return PeerReply(ok=False, degraded="a2a_failed", elapsed_ms=_ms(started),
                         card_name=str(card.get("name")), data={"error": payload["error"]})

    result = payload.get("result") or {}
    state = str((result.get("status") or {}).get("state") or "")
    artifacts = result.get("artifacts") or []
    data_part = next((a.get("data") for a in artifacts if a.get("data")), {}) or {}
    text_part = next((a.get("text") for a in artifacts if a.get("text")), "") or ""
    done = state == "TASK_STATE_COMPLETED"
    has_payload = bool(data_part.get(required_key)) if required_key else bool(data_part or text_part)
    # 弃答单独一个码：排障时"对端说没查到"和"对端挂了"的处理方式不同，
    # 混成 a2a_failed 会让人去查连通性，而实际该看的是文档库。
    rejected = any(data_part.get(k) == v for k, v in (reject or {}).items())
    ok = done and has_payload and not rejected

    return PeerReply(
        ok=ok,
        state=state,
        data=data_part,
        text=str(text_part),
        degraded=None if ok else ("a2a_abstained" if rejected else "a2a_failed"),
        elapsed_ms=_ms(started),
        card_name=str(card.get("name")),
    )


def delegate_diagnosis(
    symptom: str,
    *,
    subject: dict[str, Any],
    scope: dict[str, Any] | None = None,
    base_url: str,
    parent_budget_ms: int | None = None,
    transport: Any = None,
) -> DelegateResult:
    """知识域 → 排障域：拿根因结论。"""
    reply = send_message(
        symptom,
        skill_id=DIAGNOSE_SKILL,
        subject=subject,
        # 出站前声明来源：对端据此判断自己还能不能再往外委派（防 A→B→A 回环）
        scope=extend_scope(scope, domain=SOURCE_KNOWLEDGE),
        base_url=base_url,
        parent_budget_ms=parent_budget_ms,
        required_key="root_cause",
        transport=transport,
    )
    return DelegateResult(
        ok=reply.ok,
        root_cause=reply.data.get("root_cause"),
        text=reply.text[:600],
        data=reply.data,
        state=reply.state,
        degraded=reply.degraded,
        elapsed_ms=reply.elapsed_ms,
        card_name=reply.card_name,
    )


def ask_knowledge(
    question: str,
    *,
    subject: dict[str, Any],
    scope: dict[str, Any] | None = None,
    base_url: str,
    parent_budget_ms: int | None = None,
    transport: Any = None,
) -> PeerReply:
    """排障域 → 知识域：查制度/条款原文。

    这是排障域唯一缺的一类证据——"这单该不该赔"取决于赔付条款，而条款在文档库里，
    不在那几张订单表里。`check_rule` 只能回答"这个规则 id 的触发条件"，
    回答不了"用户签的那版制度原文怎么写的"。

    `required_key="answer"` + `reject={"finish_reason": "abstained"}`：
    知识域在证据不足时会走 abstain，此时 state 仍是 COMPLETED、`answer` 里也**有字**
    （"现有资料未覆盖…"，前端要显示），所以单看 `answer` 非空会把弃答当成"查到条款"。
    判据必须落到 `finish_reason` 上——那是知识域自己给的"我到底答没答"的结论。
    """
    return send_message(
        question,
        skill_id=ANSWER_SKILL,
        subject=subject,
        scope=extend_scope(scope, domain=SOURCE_FULFILLMENT),
        base_url=base_url,
        parent_budget_ms=parent_budget_ms,
        required_key="answer",
        reject={"finish_reason": "abstained"},
        transport=transport,
    )


def _fetch_card(client, base_url: str, headers: dict[str, str]) -> dict[str, Any] | None:
    try:
        resp = client.get(f"{base_url.rstrip('/')}/.well-known/agent-card.json", headers=headers)
        if resp.status_code != 200:
            return None
        card = resp.json()
        return card if isinstance(card, dict) and card.get("skills") is not None else None
    except Exception:  # noqa: BLE001
        return None


def _budget(parent_budget_ms: int | None) -> int:
    if not parent_budget_ms:
        return DEFAULT_TIMEOUT_MS
    return max(1_000, min(DEFAULT_TIMEOUT_MS, parent_budget_ms - PARENT_RESERVE_MS))


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
