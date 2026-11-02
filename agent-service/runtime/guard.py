"""护栏。

三类护栏，三种失败语义，不能混：

| 护栏 | 命中时 | 为什么不能混 |
|---|---|---|
| 主体校验 | `403 SUBJECT_DISABLED` | 离职/停用一票否决（ADR-0001），优先于所有授权求值 |
| 输入注入 | `422 INJECTION_BLOCKED` | 这是**正常防护**，不是 bug；要和 5xx 分开计数，否则看板会误判"系统不稳" |
| 动作白名单 | 丢弃提议 + 降级 | 不是错误：Agent 本来就不该产出越界动作，丢弃后走 escalate |

M0 的注入检测是**基线正则**，不是分类器。这一点必须诚实：
它只能拦住"忽略以上指令"这类直白变体，挡不住语义改写。
M1 会换成分类器 + 证据块隔离（docs/12 §4.3 的 `evidence_block`），
但现在就把接口留好，避免到时候改各个域。
"""

from __future__ import annotations

import re

from api.schemas import Code, DegradedEnum, RiskLevel, SubjectContext

from .config import GuardCfg
from .errors import KwError

#: 软上限：超过就降级（docs/13 §3.1 / ADR-0001 的缓解措施）
MAX_DEPT_DEPTH = 6
MAX_APPROVALS = 50

#: 直白注入模式（M0 基线，M1 换分类器）
_INJECTION = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"忽略(以上|之前|上面|前面)(所有)?(的)?(指令|要求|规则)",
        r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions",
        r"(输出|告诉|展示|打印|重复).{0,8}(系统)?(提示词|prompt|system\s*prompt)",
        r"你现在是.{0,12}(不受|没有).{0,6}限制",
        r"</?\s*(system|assistant)\s*>",
        r"(把|将).{0,10}(密钥|密码|token|api[_-]?key).{0,10}(发|告诉|输出)",
    )
]

#: 输出脱敏（05 §7 列级脱敏的兜底；真正的脱敏发生在出库前，这里是最后一道）
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


class Guard:
    def __init__(self, cfg: GuardCfg) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------ 主体
    def check_subject(self, subject: SubjectContext) -> list[DegradedEnum]:
        """主体级红线与软上限。

        返回需要降级的标记：软上限超了不拒绝请求（拒绝会把"少数重审批用户"挡在门外），
        而是标记降级，让下游走强一致 join（ADR-0001）。
        """
        if subject.account_status != "active":
            raise KwError(
                Code.SUBJECT_DISABLED,
                "账号状态不可用，请联系管理员",
                internal=f"account_status={subject.account_status} user={subject.user_id}",
            )
        degraded: list[DegradedEnum] = []
        if len(subject.dept_paths) > MAX_DEPT_DEPTH:
            degraded.append(DegradedEnum.acl)
        if len(subject.approval_doc_ids) > MAX_APPROVALS:
            degraded.append(DegradedEnum.acl)
        return degraded

    # ------------------------------------------------------------------ 输入
    def check_input(self, text: str) -> None:
        if not self.cfg.injection_check:
            return
        for pattern in _INJECTION:
            if pattern.search(text):
                raise KwError(
                    Code.INJECTION_BLOCKED,
                    "请求包含疑似指令覆盖内容，已阻断",
                    internal=f"pattern={pattern.pattern}",
                )

    # ------------------------------------------------------------------ 动作
    def action_verdict(self, action_type: str) -> tuple[bool, str | None]:
        """动作白名单判定。**只判定，不执行**——写操作永远走 Java 审批（01 §4）。"""
        if action_type in self.cfg.forbidden_actions:
            return False, f"forbidden_action:{action_type}"
        if self.cfg.action_whitelist and action_type not in self.cfg.action_whitelist:
            return False, f"not_in_whitelist:{action_type}"
        return True, None

    # ------------------------------------------------------------------ 输出
    def desensitize(self, text: str) -> str:
        text = _PHONE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], text)
        text = _ID_CARD.sub(lambda m: m.group(0)[:6] + "********" + m.group(0)[-4:], text)
        text = _EMAIL.sub(lambda m: m.group(1) + "***" + m.group(2), text)
        return text


def risk_of(action_type: str) -> RiskLevel:
    """动作风险分级。M0 用名字前缀判定；M1 改为配置表驱动（`config/actions.yaml`）。"""
    if any(k in action_type for k in ("refund", "write_off", "cancel", "modify")):
        return RiskLevel.high
    if any(k in action_type for k in ("retry", "push", "notify")):
        return RiskLevel.medium
    return RiskLevel.low
