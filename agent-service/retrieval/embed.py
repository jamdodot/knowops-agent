"""embedding 客户端（硅基流动 BGE-M3）。

为什么单独一个模块：MemoryRetriever（演示）和 PgIngest（入库）、PgRetriever（查
query 向量）三处都要调 embedding。三份 httpx 调用一定会漂移（超时、批量、失败
语义各不相同），所以收在一处。

统一约定：
- 没配 key 或网络失败 → 返回空/None，**绝不抛异常**。dense 路是增强项，BM25 必须
  还能跑，这是 M1 的降级纪律（10 §5）。
- 向量维度默认 1024（BGE-M3），与 `kb_chunk.embedding vector(1024)` 对齐。
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_DIM = 1024

# 硅基流动 /embeddings 单请求 input 上限是 1024 条，但一次发太多在弱网下容易超时；
# 16 条一批是"吞吐够用 + 单批失败代价小"的折中。
BATCH_SIZE = 16


def embed_config() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model)。"""
    return (
        os.environ.get("SILICONFLOW_API_KEY", ""),
        (os.environ.get("EMBED_BASE") or DEFAULT_BASE).rstrip("/"),
        os.environ.get("EMBED_MODEL") or DEFAULT_MODEL,
    )


def embed_texts(texts: list[str], *, timeout: float = 60.0) -> list[list[float]]:
    """批量嵌入。返回的列表与输入等长；失败返回空列表（调用方据此跳过 dense 路）。"""
    if not texts:
        return []
    key, base, model = embed_config()
    if not key:
        return []

    import httpx  # 延迟导入：没配 key 时不碰网络栈

    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        try:
            resp = httpx.post(
                f"{base}/embeddings",
                json={"model": model, "input": batch},
                headers={"Authorization": f"Bearer {key}"},
                timeout=timeout,
            )
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json().get("data", [])
        except Exception:
            return []
        # 按 index 排序，别信返回顺序——服务端并行时可能乱序
        data.sort(key=lambda d: d.get("index", 0))
        if len(data) != len(batch):
            return []
        out.extend([d.get("embedding") or [] for d in data])
    return out


def embed_one(text: str, *, timeout: float = 30.0) -> list[float] | None:
    """单条嵌入（查 query 向量用）。失败返回 None。"""
    rows = embed_texts([text], timeout=timeout)
    return rows[0] if rows else None


def to_vector_literal(vec: list[float]) -> str:
    """转 pgvector 的文本字面量 `[0.1,0.2,...]`，配合 `%s::vector` 用。"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
