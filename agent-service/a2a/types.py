"""A2A 协议的最小实现（JSON-RPC 2.0 绑定，对齐 A2A v1.0）。

为什么自己写而不引官方 SDK：这里只需要三个方法（SendMessage / GetTask / CancelTask）
和一种传输（HTTP+JSON）。引 SDK 会把整棵依赖树拖进来，而真正需要被验证的是
**两个域之间的边界**——谁不该拿到谁的权限、对端挂了怎么办——这些 SDK 不管。

两个 v1.0 的破坏性变更，踩过才知道（docs/01 §9.2 已记）：
1. `Part` 不再有 `kind` 字段，改由携带的字段自我标识（text / url / raw / data）。
   按 v0.3 写 `{"kind": "text", "text": "..."}` 会在严格实现里被拒。
2. 状态枚举是全大写常量：`TASK_STATE_COMPLETED`，不是 `completed`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 状态机。与 REST 绑定共用一套语义，只是序列化形式不同（docs/13 §8）。
TASK_STATE_SUBMITTED = "TASK_STATE_SUBMITTED"
TASK_STATE_WORKING = "TASK_STATE_WORKING"
TASK_STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
TASK_STATE_FAILED = "TASK_STATE_FAILED"
TASK_STATE_CANCELED = "TASK_STATE_CANCELED"

TERMINAL = {TASK_STATE_COMPLETED, TASK_STATE_FAILED, TASK_STATE_CANCELED}

#: 域标识。用于 A2A 委派链（scope.chain）与回环检测。
SOURCE_KNOWLEDGE = "knowledge"
SOURCE_FULFILLMENT = "fulfillment"

#: 一条委派链最多几跳。两跳足够（知识→排障→知识就算回环了），
#: 放更大只会让环跑得更久，不会让它变得合理。
MAX_HOPS = 2

#: scope 里存委派链的键。放进 scope 而不是 metadata 顶层，
#: 是因为 scope 本来就是"最小授权 + 调用约束"的载体，链是约束不是数据。
CHAIN_KEY = "chain"


def chain_of(scope: dict[str, Any] | None) -> list[str]:
    """从 scope 里读委派链，做防御性清洗。

    不清洗的后果：对端可以传 `{"chain": "knowledge"}` 这种字符串，
    于是 `"knowledge" in "knowledge"` 为真、`len()` 却按字符算——
    校验看起来生效了，实际行为取决于传的是什么类型。
    """
    raw = (scope or {}).get(CHAIN_KEY)
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(x) for x in raw if isinstance(x, (str, int))]


def extend_scope(scope: dict[str, Any] | None, *, domain: str) -> dict[str, Any]:
    """委派时把本域追加到链上。

    只在**出站**调用里用：发起方声明"我是谁"，对端据此判断还能不能再往外委派。
    """
    out = dict(scope or {})
    out[CHAIN_KEY] = [*chain_of(out), domain]
    return out


def may_delegate(scope: dict[str, Any] | None, *, domain: str) -> tuple[bool, str]:
    """本域还能不能继续往外委派。返回 (可否, 不可的原因)。

    两条拒绝理由，对应两种不同的故障：
    - 链里已有自己 → **回环**。知识→排障→知识，靠预算耗尽才停，trace 上是一串套娃 span。
    - 链太长 → **扇出**。每次委派都带一次完整子图执行，跳数不封顶等于放大器。
    """
    chain = chain_of(scope)
    if domain in chain:
        return False, f"a2a_loop:{domain}"
    if len(chain) >= MAX_HOPS:
        return False, f"a2a_hops_exceeded:{len(chain)}"
    return True, ""


@dataclass
class Part:
    """A2A 的载荷单元。**不带 kind**：有 text 就是文本，有 data 就是结构化数据。"""

    text: str | None = None
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.text is not None:
            out["text"] = self.text
        if self.data is not None:
            out["data"] = self.data
        return out


@dataclass
class Skill:
    id: str
    name: str
    description: str
    input_modes: list[str] = field(default_factory=lambda: ["text"])
    output_modes: list[str] = field(default_factory=lambda: ["text", "data"])


@dataclass
class AgentCard:
    """`/.well-known/agent-card.json` 的内容。

    对等发现的意义不是"自动找服务"，而是**把能力边界写成机器可读的声明**：
    知识域读了这张卡才知道排障域吃什么样的输入，而不是靠人记文档。
    """

    name: str
    description: str
    url: str
    version: str
    skills: list[Skill]
    #: 域标识，用于委派链与回环判定。空则由 name 推断（"fulfillment-agent" → "fulfillment"）。
    #: 不直接拿 name 比较：name 是给人看的展示名，随时可能改，链上留着旧名就永远匹配不上。
    id: str = ""
    capabilities: dict[str, bool] = field(default_factory=lambda: {"streaming": True, "pushNotifications": False})

    def domain_id(self) -> str:
        return self.id or self.name.removesuffix("-agent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "protocolVersion": "1.0",
            "capabilities": self.capabilities,
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text", "data"],
            "skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "inputModes": s.input_modes,
                    "outputModes": s.output_modes,
                }
                for s in self.skills
            ],
        }


def task_payload(task_id: str, state: str, *, artifacts: list[Part] | None = None,
                 status_message: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": task_id,
        "status": {"state": state, "timestamp": _now()},
        "artifacts": [p.to_dict() for p in (artifacts or [])],
    }
    if status_message:
        body["status"]["message"] = {"role": "agent", "parts": [{"text": status_message}]}
    if metadata:
        body["metadata"] = metadata
    return body


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def rpc_ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_err(req_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}
