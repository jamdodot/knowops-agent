"""rerank 客户端（硅基流动 BAAI/bge-reranker-v2-m3）。

为什么必须独立建模、不能塞进 embed：是 `docs/08` §2 那条——**rerank 没有统一标准**。
chat/embedding 都能套 OpenAI 规范，rerank 各家路径完全不同（硅基流动 `/v1/rerank`、
Cohere `/rerank`、Jina 又一套参数）。所以这里单独一个适配器，换供应商只改这一处。

## 两个实跑出来的结论（别按直觉改）

1. **rerank 分数不能当相关性阈值用**。实测「调拨审批要谁签字」对金标片段只有
   **0.419**，噪声片段掉到 0.009 / 0.000018。它是 sigmoid 输出、绝对值集中在低区间，
   拿 0.5 当门槛会把正确结果全滤掉。**只用于排序，不用于过滤**——
   过滤靠 dense 的余弦下限（`KW_DENSE_MIN_SIM`），两者不是一回事。

2. **降级必须显式，不能静默**（`docs/08` §3.5）。rerank 不可用时跳过、直接用 RRF 结果，
   效果掉几个点但不阻塞。代价是：如果不把这件事记进 `notes`，评测时会出现
   "这次分数低"却查不出原因——因为配置里明明写了 rerank。

## 契约

失败一律返回 `None`，绝不抛异常。调用方拿到 `None` 就保留 RRF 顺序，
并在 `notes["rerank"] = "skipped: <原因>"` 里留下痕迹（上层会转成响应里的 `degraded`）。
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Sequence

DEFAULT_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

# 文档 02 §122：50 条候选，CPU 自托管 300~600ms；走 API 加上网络往返约 1~2s。
# 给 8s 是留足弱网余量，同时不至于让一次检索把 15s 的整体 deadline 吃穿。
DEFAULT_TIMEOUT = 8.0


def rerank_config() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model)。与 embedding 共用 SILICONFLOW_API_KEY。"""
    return (
        os.environ.get("SILICONFLOW_API_KEY", ""),
        (os.environ.get("RERANK_BASE") or DEFAULT_BASE).rstrip("/"),
        os.environ.get("RERANK_MODEL") or DEFAULT_MODEL,
    )


def rerank_available() -> bool:
    key, _, _ = rerank_config()
    return bool(key) and os.environ.get("KW_RERANK", "1") not in ("0", "false", "off")


def rerank(
    query: str,
    candidates: Sequence[Any],
    *,
    top_n: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[list[Any] | None, str]:
    """对候选重排。返回 `(重排后的列表, 状态说明)`；状态说明用于降级上报。

    第一个返回值为 `None` 表示"rerank 没生效"，调用方必须保留原顺序。
    返回空列表表示"rerank 生效但没有任何候选"——这两者不能混为一谈，
    混了就会出现"检索坏了"和"确实没东西"在日志里长得一样。
    """
    if not candidates:
        return [], "empty"
    if not rerank_available():
        return None, "skipped:disabled"

    key, base, model = rerank_config()
    docs = [getattr(c, "content", "") or "" for c in candidates]
    if not any(d.strip() for d in docs):
        return None, "skipped:no_content"

    # 不传 top_n：硅基流动返回全部候选的分数，让调用方自己截——
    # 这样"rerank 后 top_k"和"rerank 前 top_50"可以在同一份结果上算指标。
    payload: dict[str, Any] = {
        "model": model,
        "query": query,
        "documents": docs,
        "return_documents": False,
    }
    if top_n:
        payload["top_n"] = top_n

    try:
        import httpx  # 延迟导入：没配 key 时不碰网络栈

        resp = httpx.post(
            f"{base}/rerank",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        results: list[dict[str, Any]] = resp.json().get("results") or []
    except Exception as exc:  # 网络/配额/模型下线 → 一律降级，不阻塞主链路
        return None, f"skipped:{type(exc).__name__}:{str(exc).splitlines()[0][:120]}"

    if not results:
        return None, "skipped:empty_response"

    # 按服务端给的 relevance_score 降序。不信任 results 的顺序，
    # 也不信任 index 全部合法（防御一下，越界的直接丢）。
    ordered: list[tuple[int, float]] = []
    for r in results:
        idx = r.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
            continue
        ordered.append((idx, float(r.get("relevance_score") or 0.0)))
    if not ordered:
        return None, "skipped:bad_index"
    ordered.sort(key=lambda x: x[1], reverse=True)

    out = [replace(candidates[i], score=s) for i, s in ordered]
    return out, f"ok:{model}"
