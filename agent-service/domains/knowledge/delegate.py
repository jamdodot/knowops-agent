"""知识域 → 排障域的 A2A 委派节点。

**为什么要这个节点**：知识域能答"制度怎么写的"，答不了"这一单为什么卡住"。
后者需要实时业务数据（订单/库存/物流/支付/日志），只有排障域有。
硬把两类问题揉进一个域，结果是知识域的检索库里塞满订单快照——那已经不是知识库了。

**两条保守的准入条件**，都是踩过的：

1. **只有带订单号才委派。** 没有订单号，排障域第一件事是追问单号（clarify），
   然后停在 input_required——上游没有审批/澄清界面，只能等到超时。
   "看起来像履约问题"不足以触发委派，得给得出可执行的入参。
2. **拿不到就降级，不重试。** 知识域的主职能是文档问答。为了一个加分项
   把主答案拖到超时，是本末倒置。降级时答案照出，只是标 `degraded=[a2a]`。

委派结果以**一条伪 evidence** 注入，而不是拼进正文：
拼正文会绕过引用校验（答案里出现没有出处的话），注入 evidence 则让它必须带引用
——哪怕是"排障域实时诊断"这个来源，也要在答案里标明出处。
"""

from __future__ import annotations

import os
import re
from typing import Any

from a2a.client import delegate_diagnosis
from api.schemas import DegradedEnum
from runtime import Runtime

#: 订单号是唯一可靠的准入条件（见模块注释第 1 条）
_ORDER_ID = re.compile(r"\bO\d{11}\b")

#: 伪 evidence 的 eid。真实文档是 e_{doc}_{chunk}，这里显式区分开，
#: 让下游一眼能看出"这条不是文档"，而不是靠 doc_id 猜。
_DELEGATE_EID = "e_a2a_fulfillment_0"
_DELEGATE_DOC = "a2a:fulfillment"


def maybe_delegate(state: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    base_url = (os.environ.get("KW_A2A_FULFILLMENT_URL") or "").strip()
    query = str(state.get("query") or "")
    if not base_url or not _ORDER_ID.search(query):
        return {}

    subject = _minimal_subject(rt)
    if subject is None:
        return {}

    result = delegate_diagnosis(
        query,
        subject=subject,
        # 最小 scope：只给委派必需的，不把本域的权限清单递过去（docs/13 §8）
        scope={"source": "knowledge", "with_order": True},
        base_url=base_url,
    )

    if not result.ok:
        # 降级必须可见：静默跳过会让"这次答案缺了实时数据"变成查不出来的差异
        rt.degrade(DegradedEnum.a2a)
        rt.progress(note=f"排障域不可用，降级为仅文档作答：{result.degraded}")
        return {"delegated": {"ok": False, "degraded": result.degraded}}

    rt.progress(note=f"排障域已返回根因：{result.root_cause}（{result.elapsed_ms:.0f}ms）")
    row = {
        "eid": _DELEGATE_EID,
        "doc_id": _DELEGATE_DOC,
        "title": "排障域实时诊断",
        "content": result.as_evidence(),
        "page_no": None,
        "section_path": "A2A",
        "acl_epoch": 0,
    }
    return {"delegated": {"ok": True, "root_cause": result.root_cause}, "evidence": [row] + list(state.get("evidence") or [])}


def _minimal_subject(rt: Runtime) -> dict[str, Any] | None:
    """只传求权必需的字段。

    把 `approval_doc_ids` 之类的授权清单递过去，等于让对端替我们做授权判断——
    而它并不掌握我们的审批状态。传得越少，越界面越小。
    """
    try:
        ctx = rt.current()
        subj = ctx.subject
    except Exception:  # noqa: BLE001
        return None
    return {
        "user_id": subj.user_id,
        "account_status": subj.account_status,
        "dept_paths": list(subj.dept_paths or []),
        "roles": list(subj.roles or []),
        "groups": list(subj.groups or []),
        "subject_epoch": int(subj.subject_epoch or 1),
    }
