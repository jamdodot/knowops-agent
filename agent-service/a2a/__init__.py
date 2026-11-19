"""A2A：本项目唯一的跨域调用通道。

两个方向都用同一套协议与同一套降级语义：
- knowledge → fulfillment：`delegate_diagnosis`，问"这一单为什么卡住"
- fulfillment → knowledge：`ask_knowledge`，问"这种情况制度/条款怎么规定"

**双向必须带链**。单向时"谁会调谁"是写死的，不可能成环；一旦双向，
知识域问句里带订单号、排障域又问条款，就是 A→B→A 的天然环。
所以每个出站调用都往 `scope.chain` 追加自己，对端看到链里有自己就拒。
这不是可选的加固，是双向能成立的前提。

模块划分刻意把"协议"和"业务"分开：
- `types` 只有数据结构与链判定，不知道有 graph 存在；
- `server` 只做 JSON-RPC ↔ 回调的翻译，不建图、不碰数据库；
- `client` 只管发请求与降级，不知道对端内部怎么排障。

这样 A2A 这一层可以单独测：喂一个假 handler 就能验证协议，
不需要起真图、不需要模型 key。
"""

from __future__ import annotations

from .client import (
    ANSWER_SKILL,
    DIAGNOSE_SKILL,
    DEFAULT_TIMEOUT_MS,
    PARENT_RESERVE_MS,
    DelegateResult,
    PeerReply,
    ask_knowledge,
    delegate_diagnosis,
    send_message,
)
from .server import mount
from .types import (
    CHAIN_KEY,
    MAX_HOPS,
    SOURCE_FULFILLMENT,
    SOURCE_KNOWLEDGE,
    AgentCard,
    Part,
    Skill,
    chain_of,
    extend_scope,
    may_delegate,
    task_payload,
)

__all__ = [
    "ANSWER_SKILL",
    "AgentCard",
    "CHAIN_KEY",
    "DEFAULT_TIMEOUT_MS",
    "DIAGNOSE_SKILL",
    "DelegateResult",
    "MAX_HOPS",
    "PARENT_RESERVE_MS",
    "PeerReply",
    "Part",
    "SOURCE_FULFILLMENT",
    "SOURCE_KNOWLEDGE",
    "Skill",
    "ask_knowledge",
    "chain_of",
    "delegate_diagnosis",
    "extend_scope",
    "may_delegate",
    "mount",
    "send_message",
    "task_payload",
]
