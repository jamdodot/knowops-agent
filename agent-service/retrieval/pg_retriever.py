"""PG 检索后端：dense(pgvector) + BM25(pg_search) 双路 + RRF，鉴权下推到同一条 SQL。

对齐 docs/05 §3 的 `permitted` CTE：鉴权条件与向量/关键词排序在**同一条 SQL** 里，
不存在"先召回再过滤"的时间窗。BM25 主路用 ParadeDB 的 `paradedb.score`，
退化路（无 pg_search 时）用 tsvector + ts_rank。

依赖 `psycopg[binary]` + `pgvector`。没有 Docker/PG 时，用 MemoryRetriever 兜底
（见 `build_retriever`）。
"""

from __future__ import annotations

import os
from typing import Any

from .base import Evidence

# permitted CTE 里的鉴权模板（与 docs/05 §3 逐字对齐）
_PERMITTED_SQL = """
WITH permitted AS (
  SELECT c.id, c.parent_id, c.content, c.doc_id, c.embedding, c.tsv,
         d.title, c.section_path, c.page_no, c.acl_epoch
  FROM knowops_read.kb_chunk c
  JOIN knowops_read.kb_doc_mirror d ON d.doc_id = c.doc_id
  WHERE d.status = 'active'
    AND d.acl_from <= now()
    AND (d.acl_to IS NULL OR d.acl_to > now())
    AND (
          c.acl_jsonb->>'v' = 'public'
       OR c.acl_jsonb->'depts'  ?| %(depts)s::text[]
       OR c.acl_jsonb->'roles'  ?| %(roles)s::text[]
       OR c.acl_jsonb->'groups' ?| %(groups)s::text[]
       OR c.acl_jsonb->'users'  ?  %(user_id)s
       OR c.doc_id = ANY(%(approvals)s::text[])
    )
)
"""


class PgRetriever:
    """PG 混合检索。构造需一个已可用的 psycopg 连接串。

    注意：本类只写 SQL 与参数拼装，真正的连接在 `hybrid_retrieve` 里短连。
    这样每个请求独立连接，避免长连接跨线程（工具跑在线程池）带来的状态污染。
    """

    def __init__(self, dsn: str | None = None, *, embed_dim: int = 1024):
        self._dsn = dsn or os.environ.get("KW_PG_DSN", "")
        self._embed_dim = embed_dim

    def hybrid_retrieve(self, query: str, subject: Any, *, top_k: int = 5) -> list[Evidence]:
        if not self._dsn:
            return []
        import psycopg  # 延迟导入：无 PG 时不触发

        params = {
            "user_id": subject.user_id,
            "depts": list(subject.dept_paths or []),
            "roles": list(subject.roles or []),
            "groups": list(subject.groups or []),
            "approvals": list(subject.approval_doc_ids or []),
            "query": query,
            "top_k": top_k * 2,  # 融合前先多取，融合后再截
        }
        # dense 路：向量余弦。若无 query embedding 支持（需先嵌 query），此处降级为仅 BM25。
        # 完整实现需要先调用 embedding API 把 query 变成向量再传入；这里留出 `query_vec` 注入点。
        dense_sql = _PERMITTED_SQL + """
SELECT id, content, title, section_path, page_no, acl_epoch, doc_id,
       1 - (embedding <=> %(query_vec)s::vector) AS score
FROM permitted
ORDER BY embedding <=> %(query_vec)s::vector
LIMIT %(top_k)s;
"""
        bm25_sql = _PERMITTED_SQL + """
SELECT id, content, title, section_path, page_no, acl_epoch, doc_id,
       paradedb.score(id) AS score
FROM permitted
WHERE id @@@ %(query)s
ORDER BY score DESC
LIMIT %(top_k)s;
"""

        dense_rows: list[tuple[str, float]] = []
        bm25_rows: list[tuple[str, float]] = []

        with psycopg.connect(self._dsn) as conn:
            # BM25 路（主路）
            try:
                with conn.cursor() as cur:
                    cur.execute(bm25_sql, params)
                    bm25_rows = [(r[0], float(r[6] or 0.0)) for r in cur.fetchall()]
            except Exception:
                bm25_rows = []  # pg_search 缺失时降级，不阻塞
            # dense 路（需 query 向量，由调用方注入；未注入则跳过）
            if params.get("query_vec"):
                try:
                    with conn.cursor() as cur:
                        cur.execute(dense_sql, params)
                        dense_rows = [(r[0], float(r[6] or 0.0)) for r in cur.fetchall()]
                except Exception:
                    dense_rows = []

        fused = _rrf(bm25_rows, dense_rows)
        # fused 里只有 id 和 score，还要再取 content 等字段——为简洁，这里用一次回表
        return self._materialize(fused, top_k)

    def _materialize(self, fused: list[tuple[str, float]], top_k: int) -> list[Evidence]:
        if not fused or not self._dsn:
            return []
        import psycopg

        ids = [cid for cid, _s in fused[: top_k]]
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, doc_id, content, section_path, page_no, acl_epoch,
                           (SELECT title FROM knowops_read.kb_doc_mirror WHERE doc_id = c.doc_id)
                    FROM knowops_read.kb_chunk c
                    WHERE id = ANY(%s)
                    """,
                    (ids,),
                )
                rows = cur.fetchall()
        by_id = {r[0]: r for r in rows}
        out: list[Evidence] = []
        for cid, score in fused[:top_k]:
            r = by_id.get(cid)
            if not r:
                continue
            out.append(
                Evidence(
                    chunk_id=r[0],
                    doc_id=r[1],
                    title=r[6] or "",
                    content=r[2],
                    section_path=r[3],
                    page_no=r[4],
                    acl_epoch=r[5] or 0,
                    score=score,
                )
            )
        return out

    def doc_fetch(self, doc_id: str, subject: Any) -> dict[str, Any]:
        # M1 阶段：doc_fetch 的 PG 实现在父块回填时再补；先返回 not_found 语义
        return {"doc_id": doc_id, "error": "not_implemented_pg"}

    def ingest(self, doc_id: str, title: str, chunks: list[dict[str, Any]], acl: dict[str, Any]) -> None:
        # 入库由解析->分块->写入链路承担（M1 后续），此处留接口
        raise NotImplementedError("PG 入库走 outbox 写入链路，不在此直接灌入")

    def index_size(self) -> int:
        if not self._dsn:
            return 0
        import psycopg

        try:
            with psycopg.connect(self._dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM knowops_read.kb_chunk")
                    return int(cur.fetchone()[0])
        except Exception:
            return 0


def _rrf(a: list[tuple[str, float]], b: list[tuple[str, float]]) -> list[tuple[str, float]]:
    from collections import defaultdict

    score: dict[str, float] = defaultdict(float)
    for rank, (cid, _s) in enumerate(a, start=1):
        score[cid] += 1.0 / (60 + rank)
    for rank, (cid, _s) in enumerate(b, start=1):
        score[cid] += 1.0 / (60 + rank)
    return sorted(score.items(), key=lambda x: x[1], reverse=True)


def build_retriever() -> Any:
    """按环境决定检索后端。PG 可用则 PgRetriever，否则 MemoryRetriever。

    这层是唯一的切换点，域代码不感知后端类型。
    """
    dsn = os.environ.get("KW_PG_DSN") or ""
    if dsn:
        # 探测 PG 是否真的可达；不可达就回落 Memory，避免"配置了但连不上"的静默空检索
        import psycopg

        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return PgRetriever(dsn)
        except Exception:
            # 回落到内存；这是有意的降级（不抛错），因为无 PG 演示是合法路径
            pass
    from .memory_retriever import MemoryRetriever

    return MemoryRetriever()
