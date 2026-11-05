"""A2A：本项目唯一的跨域调用通道（knowledge → fulfillment）。

对齐 A2A v1.0（JSON-RPC 2.0 over HTTP）。只做单向：
双向互调会让状态机复杂度翻倍，而演示价值几乎不变（docs/15 阶段 C）。

模块划分刻意把"协议"和"业务"分开：
- `types` 只有数据结构，不知道有 graph 存在；
- `server` 只做 JSON-RPC ↔ 回调的翻译，不建图、不碰数据库；
- `client` 只管发请求与降级，不知道对端内部怎么排障。

这样 A2A 这一层可以单独测：喂一个假的 run_symptom 就能验证协议，
不需要起真图、不需要模型 key。
"""

from __future__ import annotations

from .client import DelegateResult, delegate_diagnosis
from .server import mount
from .types import AgentCard, Part, Skill, task_payload

__all__ = [
    "AgentCard",
    "DelegateResult",
    "Part",
    "Skill",
    "delegate_diagnosis",
    "mount",
    "task_payload",
]
