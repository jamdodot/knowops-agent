"""A2A 客户端：知识域委派诊断给排障域。

三条硬约束（docs/13 §5.1 / §8）：

1. **子预算必须小于父预算。** 这里默认 30s 上限，且要求调用方传入"父剩余时间"，
   实取 `min(父剩余 - 500ms, 30s)`。少了那 500ms 会出现"子调用成功、父请求超时"
   ——trace 上看着全绿，用户那边是 504。
2. **委派只带最小 scope，不带 subject_ctx 全量。** 主体只传求权必需的字段，
   权限清单、审批单这些留在本域——传过去等于把本域的授权判断外包给对方。
3. **对端不可用必须降级，不能重试到超时。** 知识域的主职能是文档问答，
   排障域只是加分项；为了一个加分项把主答案拖死，是典型的本末倒置。

降级语义统一用 `DegradedEnum.a2a`，让上游能看到"这次答案缺了实时数据"。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: 默认给 A2A 的上限。再大没有意义：诊断本身 p95 已在 30s 附近。
DEFAULT_TIMEOUT_MS = 30_000

#: 留给父级组装响应的余量
PARENT_RESERVE_MS = 500


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


def delegate_diagnosis(
    symptom: str,
    *,
    subject: dict[str, Any],
    scope: dict[str, Any] | None = None,
    base_url: str,
    parent_budget_ms: int | None = None,
    transport: Any = None,
) -> DelegateResult:
    """发一个 A2A SendMessage，等结果。

    `transport` 只为可测性存在：注入一个 httpx.MockTransport 就能验证
    "对端超时/连不上/缺 skill" 三种降级，不用真起两个进程。生产路径传 None。

    `base_url` 由调用方给（配置里写死），但**先读一次 Agent Card**：
    这不只是为了"发现"，而是拒绝一个能力不匹配的对端——如果对端没有
    `diagnose_order_symptom` 这个 skill，我们不应该把症状发过去。
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
                return DelegateResult(ok=False, degraded="a2a_unreachable",
                                      elapsed_ms=_ms(started))

            skill_ids = {s.get("id") for s in card.get("skills") or []}
            if "diagnose_order_symptom" not in skill_ids:
                return DelegateResult(ok=False, degraded="a2a_capability_mismatch",
                                      elapsed_ms=_ms(started), card_name=str(card.get("name")))

            body = {
                "jsonrpc": "2.0",
                "id": f"req_{int(time.time() * 1000)}",
                "method": "SendMessage",
                "params": {
                    "message": {"role": "user", "parts": [{"text": symptom}]},
                    "metadata": {"subject": subject, "scope": scope or {}},
                },
            }
            resp = client.post(f"{base_url.rstrip('/')}/a2a", json=body, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.TimeoutException:
        return DelegateResult(ok=False, degraded="a2a_timeout", elapsed_ms=_ms(started))
    except Exception:  # noqa: BLE001  连接失败、5xx、JSON 坏了，对调用方都是"对端不可用"
        return DelegateResult(ok=False, degraded="a2a_unreachable", elapsed_ms=_ms(started))

    if "error" in payload:
        return DelegateResult(ok=False, degraded="a2a_failed", elapsed_ms=_ms(started),
                              card_name=str(card.get("name")), data={"error": payload["error"]})

    result = payload.get("result") or {}
    state = str((result.get("status") or {}).get("state") or "")
    artifacts = result.get("artifacts") or []
    data_part = next((a.get("data") for a in artifacts if a.get("data")), {}) or {}
    text_part = next((a.get("text") for a in artifacts if a.get("text")), "") or ""
    ok = state == "TASK_STATE_COMPLETED" and bool(data_part.get("root_cause"))

    return DelegateResult(
        ok=ok,
        root_cause=data_part.get("root_cause"),
        text=str(text_part)[:600],
        data=data_part,
        state=state,
        degraded=None if ok else "a2a_failed",
        elapsed_ms=_ms(started),
        card_name=str(card.get("name")),
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
