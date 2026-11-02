"""幂等重放缓存（docs/13 §5.3）。

规则很简单，但漏掉任何一条都会造成真实损失：
- 原请求已完成 → 重放原流，**不重新推理**（否则重复计费，且两次答案可能不同）
- 原请求进行中 → `409 DUPLICATE_INFLIGHT`，让调用方去重连原流
- 原请求失败 → 允许重跑（失败的不算"同一件事已完成"）

M0 用内存实现，24h 窗口。M1 换 Redis 时只换存储，语义不变。
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field

from api.schemas import AnswerRequest, Code, DegradedEnum
from runtime.errors import KwError

TTL_S = 24 * 3600


def idempotency_key(req: AnswerRequest, explicit: str | None) -> str:
    """客户端给了就用客户端的；没给就按 13 §5.3 的公式派生。

    公式里必须有 `subject_epoch`：权限变了还重放旧答案，等于用旧权限回答新问题。
    """
    if explicit:
        return f"client:{explicit}"
    material = f"{req.session_id}|{req.turn_seq}|{req.query}|{req.subject_ctx.subject_epoch}"
    return "derived:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class Entry:
    frames: list[str] = field(default_factory=list)
    done: bool = False
    at: float = field(default_factory=time.time)
    degraded: list[DegradedEnum] = field(default_factory=list)


class ReplayCache:
    def __init__(self, ttl_s: int = TTL_S) -> None:
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._entries: dict[str, Entry] = {}

    def begin(self, key: str) -> Entry | None:
        """占位。返回已完成条目（可重放），或 None（本次由我执行）。"""
        self._evict()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = Entry()
                return None
            if not entry.done:
                raise KwError(
                    Code.DUPLICATE_INFLIGHT,
                    "同一幂等键的请求仍在进行中，请重连原流",
                )
            return entry

    def commit(self, key: str, frames: list[str], degraded: list[DegradedEnum] | None = None) -> None:
        with self._lock:
            entry = self._entries.setdefault(key, Entry())
            entry.frames = list(frames)
            entry.done = True
            entry.at = time.time()
            entry.degraded = list(degraded or [])

    def release(self, key: str) -> None:
        """失败时释放占位，允许调用方重试。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and not entry.done:
                self._entries.pop(key, None)

    def _evict(self) -> None:
        now = time.time()
        with self._lock:
            stale = [k for k, e in self._entries.items() if now - e.at > self._ttl]
            for k in stale:
                self._entries.pop(k, None)
