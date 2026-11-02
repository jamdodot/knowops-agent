"""记忆子系统（工作记忆 / 语义记忆 / 情景记忆）。

设计依据 docs/07。这里只实现 M0 需要的部分，但**把三条纪律写进代码**：

1. **命名空间强制**：语义记忆按 `user:{id}` 隔离，召回必须带 namespace 过滤，
   否则 A 的偏好会被 B 命中（07 §1.5 第三个坑）。这条和权限是同一个原则，所以放在内核里，
   而不是让每个域自己记得加过滤。
2. **记忆永不进知识库索引**：所以 `MemoryStore` 和 `ToolRegistry` 是两套接口，
   检索工具不可能"顺手"查到记忆（07 §1.5 第一个坑）。
3. **只存成功的案例**：情景记忆的写入接口带 `success` 标记，False 时直接丢弃——
   失败案例会把错误先验带进下一次诊断（07 §1.3）。

M0 用内存实现；M1 换 PG 时只换实现，不动调用方。表已建好：
`knowops_mem.mem_semantic` / `mem_episodic`（deploy/init/01_schema.sql）。
"""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


@dataclass
class MemoryItem:
    id: str
    namespace: str
    kind: str  # semantic | episodic
    text: str
    score: float = 0.0
    meta: dict[str, object] = field(default_factory=dict)


class MemoryStore(Protocol):
    def recall(self, kind: str, namespace: str, query: str, k: int = 3) -> list[MemoryItem]: ...

    def write(
        self, kind: str, namespace: str, text: str, *, success: bool = True, meta: dict[str, object] | None = None
    ) -> MemoryItem | None: ...


class NullMemory:
    """未启用记忆时的空实现。返回空列表而不是抛错——"没开"不是故障。"""

    def recall(self, kind: str, namespace: str, query: str, k: int = 3) -> list[MemoryItem]:
        return []

    def write(
        self, kind: str, namespace: str, text: str, *, success: bool = True, meta: dict[str, object] | None = None
    ) -> MemoryItem | None:
        return None


class InMemoryMemory:
    """M0 内存实现：词面重合度打分（不是向量）。

    刻意不假装是向量召回：M0 断言的是"命名空间隔离 + 只存成功案例"这两条纪律，
    用 BM25 式的词面重合足够验证，而且结果可复现（向量召回没法在单测里断言顺序）。
    """

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], list[MemoryItem]] = defaultdict(list)
        self._seq = 0

    def recall(self, kind: str, namespace: str, query: str, k: int = 3) -> list[MemoryItem]:
        candidates = self._items.get((kind, namespace), [])
        if not candidates:
            return []
        q = _tokens(query)
        scored: list[MemoryItem] = []
        for item in candidates:
            t = _tokens(item.text)
            if not q or not t:
                continue
            overlap = len(q & t)
            if overlap == 0:
                continue
            # 余弦式归一化，避免长文本天然占优
            score = overlap / math.sqrt(len(q) * len(t))
            scored.append(MemoryItem(item.id, item.namespace, item.kind, item.text, round(score, 4), item.meta))
        scored.sort(key=lambda i: i.score, reverse=True)
        return scored[:k]

    def write(
        self, kind: str, namespace: str, text: str, *, success: bool = True, meta: dict[str, object] | None = None
    ) -> MemoryItem | None:
        if not success:
            # 情景记忆只存成功案例（07 §1.3）；失败案例会把错误先验带进新任务
            return None
        self._seq += 1
        item = MemoryItem(
            id=f"mem_{self._seq:06d}", namespace=namespace, kind=kind, text=text, meta=dict(meta or {})
        )
        self._items[(kind, namespace)].append(item)
        return item

    def all_of(self, kind: str, namespace: str) -> list[MemoryItem]:
        return list(self._items.get((kind, namespace), []))


class WorkingMemory:
    """工作记忆：最近 K 轮原文 + TTL。

    真正的工作记忆还有"当前图状态"，那部分在 checkpoint 里，不在这里（07 §1.1）——
    混在一起会导致 resume 后读到过期中间态。
    """

    def __init__(self, keep: int = 6, ttl_s: int = 2 * 3600) -> None:
        self._keep = keep
        self._ttl = ttl_s
        self._turns: dict[str, deque[dict[str, str]]] = {}
        self._touched: dict[str, float] = {}

    def append(self, thread_id: str, role: str, content: str) -> None:
        self._evict()
        self._turns.setdefault(thread_id, deque(maxlen=self._keep)).append(
            {"role": role, "content": content}
        )
        self._touched[thread_id] = time.time()

    def window(self, thread_id: str, k: int | None = None) -> list[dict[str, str]]:
        self._evict()
        turns = list(self._turns.get(thread_id, ()))
        return turns[-(k or self._keep) :]

    def _evict(self) -> None:
        now = time.time()
        expired = [t for t, ts in self._touched.items() if now - ts > self._ttl]
        for t in expired:
            self._turns.pop(t, None)
            self._touched.pop(t, None)


def namespace_for(user_id: str) -> str:
    """语义记忆的命名空间。集中在这里，避免某处写成 f"user_{id}" 导致隔离失效。"""
    return f"user:{user_id}"


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))
