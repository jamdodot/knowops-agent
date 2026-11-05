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
    capabilities: dict[str, bool] = field(default_factory=lambda: {"streaming": True, "pushNotifications": False})

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
