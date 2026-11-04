"""PG 检索后端：dense(pgvector) + BM25(pg_search) 双路 + RRF，鉴权下推到同一条 SQL。

对齐 docs/05 §3 的 `permitted` CTE：鉴权条件与向量/关键词排序在**同一条 SQL** 里，
不存在"先召回再过滤"的时间窗。

## 三条实跑出来的结论（别按直觉改）

1. **BM25 必须写在 `content` 列上**，`WHERE id @@@ '查询'` 匹配的是 id 那一列，
   中文查询永远 0 命中。之前就是这么写的，真库一跑就露馅。
2. **`permitted` 只 select id 反而更好**：ParadeDB 的 planner 会把「bm25 索引扫描 +
   CTE 里的 acl 条件」合并成一次 `ParadeDB Base Scan`，acl 条件变成 Tantivy 的
   `heap_filter` 下推进索引扫描里（EXPLAIN 里能看到）。所以鉴权确实是"检索的一部分"，
   不是召回后再 sieve——这条设计在真库上成立。
3. **默认分词器把整句中文当一个 token**（`paradedb.tokenize('default', ...)` 会返回
   一个 `(跨部门调拨需审批,0)`）。中文检索必须建索引时给 `chinese_compatible`
   分词器，见 `deploy/init/03_pg_search_index.sql`。
4. **dense 必须有相似度下限**（`KW_DENSE_MIN_SIM`，默认 0.55）。余弦永远有值，
   不加阈值时"越权拦截"会变成"召回一堆不相干文档"，演示和评测都会失真。
   实测 BGE-M3：命中片段 0.62~0.78，噪声 0.49~0.55，0.55 是这条语料的分界。

## 降级链

pg_search 缺失 → 中文 bigram + ILIKE 内存打分（能用，但只是演示级）；
embedding 缺失/失败 → 只走 BM25。两条路都断了才返回空，且空不代表"没权限"，
日志里会区分（见 `last_backend_notes`）。

依赖 `psycopg[binary]`。没有 Docker/PG 时由 `build_retriever` 回落 MemoryRetriever。
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import replace
from typing import Any

from .base import Evidence
from .embed import embed_one, to_vector_literal

# permitted CTE 里的鉴权模板（与 docs/05 §3 逐字对齐；只取 id，正文按主键回表）
_PERMITTED_SQL = """
WITH permitted AS (
  SELECT c.id
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

# 两个检索分支共用的选取列
# title 只能从 kb_document（真相表）取：kb_doc_mirror 只存状态与 epoch，没有标题。
# 跨 schema 读一个展示字段是允许的——鉴权条件仍然全部落在 mirror + chunk 上，
# 真相表不参与可见性判断。要彻底解耦就给 mirror 加 title 列（需迁移），M1 先不做。
_SELECT_COLS = """
SELECT c.id, c.doc_id, c.content, c.section_path, c.page_no, c.acl_epoch, doc.title, %s AS score
FROM knowops_read.kb_chunk c
JOIN permitted p ON p.id = c.id
JOIN knowops_read.kb_doc_mirror d ON d.doc_id = c.doc_id
LEFT JOIN knowops_write.kb_document doc ON doc.id = c.doc_id
"""

_BM25_SQL = _PERMITTED_SQL + _SELECT_COLS % "paradedb.score(c.id)" + """
WHERE c.content @@@ %(query)s
ORDER BY score DESC
LIMIT %(limit)s;
"""

_DENSE_SQL = _PERMITTED_SQL + _SELECT_COLS % "(1 - (c.embedding <=> %(query_vec)s::vector))" + """
WHERE c.embedding IS NOT NULL
  AND 1 - (c.embedding <=> %(query_vec)s::vector) >= %(min_sim)s
ORDER BY c.embedding <=> %(query_vec)s::vector
LIMIT %(limit)s;
"""

# 退路：没有 pg_search 时的候选召回（打分在 Python 里做）
_LIKE_SQL = _PERMITTED_SQL + _SELECT_COLS % "0.0" + """
WHERE c.content ILIKE ANY(%(pats)s)
LIMIT %(limit)s;
"""

_RRF_K = 60


class PgRetriever:
    """PG 混合检索。构造需一个已可用的 psycopg 连接串。

    注意：本类只写 SQL 与参数拼装，真正的连接在 `hybrid_retrieve` 里短连。
    这样每个请求独立连接，避免长连接跨线程（工具跑在线程池）带来的状态污染。
    """

    def __init__(self, dsn: str | None = None, *, embed_dim: int = 1024):
        from .dsn import pg_dsn

        self._dsn = dsn or pg_dsn() or ""
        self._embed_dim = embed_dim
        # BM25 能力探测结果：None=未探测，'pg_search'=主路可用，'like'=退路
        self._bm25_mode: str | None = None
        # 上一次检索的后端说明，给演示/排障看（不参与业务逻辑）
        self.last_backend_notes: dict[str, Any] = {}

    # ------------------------------------------------------------------ 检索
    def hybrid_retrieve(self, query: str, subject: Any, *, top_k: int = 5) -> list[Evidence]:
        if not self._dsn or subject is None:
            return []
        import psycopg

        limit = max(top_k * 2, top_k)  # 融合前多取一点，融合后再截
        params: dict[str, Any] = {
            "user_id": subject.user_id,
            "depts": list(subject.dept_paths or []),
            "roles": list(subject.roles or []),
            "groups": list(subject.groups or []),
            "approvals": list(subject.approval_doc_ids or []),
            "query": query,
            "limit": limit,
        }
        vec = embed_one(query)
        if vec:
            params["query_vec"] = to_vector_literal(vec)
            params["min_sim"] = float(os.environ.get("KW_DENSE_MIN_SIM", "0.55"))

        bm25_rows: list[Evidence] = []
        dense_rows: list[Evidence] = []
        notes: dict[str, Any] = {"dense": bool(vec), "bm25": None}

        with psycopg.connect(self._dsn, connect_timeout=5) as conn:
            # 读路径自动提交：BM25 失败后要立刻试退路，事务里失败会把连接打进
            # InFailedSqlTransaction，后续所有语句都被拒——降级链就断了。
            conn.autocommit = True
            bm25_rows, mode = self._bm25(conn, query, params)
            notes["bm25"] = mode
            if vec:
                dense_rows = self._dense(conn, params, notes)

        self.last_backend_notes = notes
        return _rrf(bm25_rows, dense_rows)[:top_k]

    # ------------------------------------------------------------------ 分支
    def _bm25(
        self, conn: Any, query: str, params: dict[str, Any]
    ) -> tuple[list[Evidence], str]:
        """BM25 路。pg_search 不可用时退到 bigram ILIKE（演示级）。"""
        if self._bm25_mode != "like":
            try:
                rows = _fetch(conn, _BM25_SQL, params)
                self._bm25_mode = "pg_search"
                return rows, "pg_search"
            except Exception as exc:  # 扩展缺失 / 索引未建 / 参数不兼容
                if self._bm25_mode == "pg_search":
                    # 之前能用现在不能用 = 真故障，别静默降级成"看起来没权限"
                    raise
                self._bm25_mode = "like"
                self.last_backend_notes["bm25_error"] = str(exc).splitlines()[0][:200]

        terms = _bigram_terms(query)
        if not terms:
            return [], "like"
        like_params = {**params, "pats": [f"%{t}%" for t in terms]}
        rows = _fetch(conn, _LIKE_SQL, like_params)
        # 命中 bigram 数 / 总 bigram 数 当分数：够排序用，不作为 BM25 质量的替代。
        # Evidence 是 frozen 的，必须 replace 而不是原地赋值。
        scored = [
            replace(r, score=sum(1 for t in terms if t in r.content) / len(terms)) for r in rows
        ]
        return sorted(scored, key=lambda e: e.score, reverse=True), "like"

    def _dense(self, conn: Any, params: dict[str, Any], notes: dict[str, Any]) -> list[Evidence]:
        try:
            return _fetch(conn, _DENSE_SQL, params)
        except Exception as exc:
            notes["dense"] = False
            notes["dense_error"] = str(exc).splitlines()[0][:200]
            return []

    # ------------------------------------------------------------------ 回填
    def doc_fetch(self, doc_id: str, subject: Any) -> dict[str, Any]:
        """按 doc_id 取父块正文（small-to-big 回填），取前再校验一次权限。"""
        if not self._dsn:
            return {"doc_id": doc_id, "error": "not_configured"}
        import psycopg

        params = {
            "user_id": subject.user_id,
            "depts": list(subject.dept_paths or []),
            "roles": list(subject.roles or []),
            "groups": list(subject.groups or []),
            "approvals": list(subject.approval_doc_ids or []),
            "doc_id": doc_id,
        }
        sql = _PERMITTED_SQL + """
SELECT doc.title, c.acl_epoch, count(*) AS n
FROM permitted p
JOIN knowops_read.kb_chunk c ON c.id = p.id
JOIN knowops_read.kb_doc_mirror d ON d.doc_id = c.doc_id
LEFT JOIN knowops_write.kb_document doc ON doc.id = c.doc_id
WHERE c.doc_id = %(doc_id)s
GROUP BY doc.title, c.acl_epoch
"""
        with psycopg.connect(self._dsn, connect_timeout=5) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql, params)
                head = cur.fetchone()
            if not head:
                # 区分"不存在"与"不可见"要查一次主表，但对外统一 not_permitted 更安全
                return {"doc_id": doc_id, "error": "not_found_or_not_permitted"}
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT content FROM knowops_read.kb_parent_chunk
                    WHERE doc_id = %s ORDER BY page_no NULLS LAST, id
                    """,
                    (doc_id,),
                )
                parts = [r[0] for r in cur.fetchall()]
        return {
            "doc_id": doc_id,
            "title": head[0],
            "acl_epoch": head[1],
            "content": "\n\n".join(parts) if parts else "",
        }

    # ------------------------------------------------------------------ 写入
    def ingest(
        self, doc_id: str, title: str, chunks: list[dict[str, Any]], acl: dict[str, Any]
    ) -> None:
        """灌入一份文档的 chunk（无父块版；带父块的完整链路见 `retrieval.pg_ingest`）。"""
        from .pg_ingest import upsert_document, write_chunks

        if not self._dsn:
            raise RuntimeError("PgRetriever 未配置 KW_PG_DSN")
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            upsert_document(conn, doc_id=doc_id, title=title, acl=acl)
            write_chunks(conn, doc_id, parents=[], children=chunks, acl=acl)
            conn.commit()

    def index_size(self) -> int:
        if not self._dsn:
            return 0
        import psycopg

        try:
            with psycopg.connect(self._dsn, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM knowops_read.kb_chunk")
                    return int(cur.fetchone()[0])
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _fetch(conn: Any, sql: str, params: dict[str, Any]) -> list[Evidence]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Evidence(
            chunk_id=r[0],
            doc_id=r[1],
            content=r[2],
            section_path=r[3],
            page_no=r[4],
            acl_epoch=r[5] or 0,
            title=r[6] or "",
            score=float(r[7] or 0.0),
        )
        for r in rows
    ]


_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")


def _bigram_terms(query: str) -> list[str]:
    """退路用的召回词：英文/数字按词，中文取 2-gram（单字太泛，整句太长）。"""
    terms: list[str] = []
    for w in _TOKEN.findall(query.lower()):
        if re.fullmatch(r"[a-z0-9]+", w):
            terms.append(w)
        elif len(w) == 1:
            terms.append(w)
        else:
            terms.extend(w[i : i + 2] for i in range(len(w) - 1))
    # 去重但保序
    seen: set[str] = set()
    return [t for t in terms if not (t in seen or seen.add(t))]


def _rrf(a: list[Evidence], b: list[Evidence]) -> list[Evidence]:
    """RRF 融合。任一路为空时退化为另一路的排序。"""
    score: dict[str, float] = defaultdict(float)
    by_id: dict[str, Evidence] = {}
    for ranked in (a, b):
        for rank, e in enumerate(ranked, start=1):
            score[e.chunk_id] += 1.0 / (_RRF_K + rank)
            by_id.setdefault(e.chunk_id, e)
    ordered = sorted(score.items(), key=lambda x: x[1], reverse=True)
    return [replace(by_id[cid], score=s) for cid, s in ordered]


def build_retriever() -> Any:
    """按环境决定检索后端。PG 可用则 PgRetriever，否则 MemoryRetriever。

    这层是唯一的切换点，域代码不感知后端类型。
    """
    from .dsn import pg_dsn

    dsn = pg_dsn() or ""
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
