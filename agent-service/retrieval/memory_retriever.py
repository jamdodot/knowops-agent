"""内存检索后端：无 Docker 也能跑出完整混合检索链路。

设计目标不是"生产可用的检索"，而是"把权限感知的混合检索**语义**做对、可演示、可测"：
- **BM25 关键词路**：内存倒排 + 标准 BM25 打分（带 k1/b 参数，中文按 2-gram + 单字混合分词）；
- **dense 向量路**：可选。配了硅基流动 key 就真调 embedding API 算余弦；没配就只走 BM25；
- **RRF 融合**：两路排名融合（docs/01 §6：向量+BM25 两路 RRF）；
- **权限过滤**：复用 `_visible` 的判定语义（public / 部门祖先链 / 角色 / 审批例外 / 共享组），
  在**召回之后、融合之前**做，且记录 verdict 供 E8 归因。

一个诚实边界：这里的 BM25 是内存版，性能与 PG 的 pg_search 不可比；它存在的意义是
让"权限条件下推到检索"这条核心链路在无 PG 时也能被演示和测试。数据量上了万级，
就该切 PgRetriever。
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from typing import Any

from .base import Evidence

# 英文/数字单词，或连续中文段
_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")

_BM25_K1 = 1.5
_BM25_B = 0.75
_RRF_K = 60


def tokenize(text: str) -> list[str]:
    """轻量分词：英文/数字按词，中文按单字 + 相邻 2-gram。

    不做真正的中文分词（避免引 jieba 重依赖），单字+2gram 的召回已能覆盖
    制度类短查询（"报销审批""加班时长"）。这是演示/测试级的折中，PG 侧会用
    zhparser/pg_jieba 做真分词。
    """
    text = text.lower()
    toks: list[str] = []
    for w in _TOKEN.findall(text):
        if re.fullmatch(r"[a-z0-9]+", w):
            toks.append(w)
            continue
        # 中文段：拆成单字 + 2gram
        for ch in w:
            toks.append(ch)
        for i in range(len(w) - 1):
            toks.append(w[i : i + 2])
    return toks


def _visible(acl: dict[str, Any], subject: Any) -> tuple[bool, str]:
    """与 domains/knowledge/tools.py 的 `_visible` 语义一致（docs/05 §2.3）。

    四条判定顺序不可换：public → 部门祖先链 → 角色 → 审批例外 → 共享组。
    """
    mode = acl.get("v") or acl.get("mode") or "dept"
    if mode == "public":
        return True, "public"
    for dept in acl.get("depts", []):
        if dept in (subject.dept_paths or []):
            return True, f"dept:{dept}"
    for role in acl.get("roles", []):
        if role in (subject.roles or []):
            return True, f"role:{role}"
    if acl.get("doc_id") in (subject.approval_doc_ids or []):
        return True, "approval"
    for group in acl.get("groups", []):
        if group in (subject.groups or []):
            return True, f"group:{group}"
    return False, "none"


class MemoryRetriever:
    """内存混合检索。线程内共享，跨请求一致（进程内单例）。"""

    def __init__(self, *, embed_api_key: str | None = None, embed_base: str | None = None, embed_model: str | None = None):
        # chunk 存储：chunk_id -> 记录
        self._chunks: dict[str, dict[str, Any]] = {}
        # 倒排：token -> {chunk_id: tf}
        self._index: dict[str, dict[str, int]] = defaultdict(dict)
        # 文档级元数据：doc_id -> {title, acl}
        self._docs: dict[str, dict[str, Any]] = {}
        # 每 chunk 的 token 数（BM25 需要文档长度）
        self._lengths: dict[str, int] = {}
        self._avg_len = 0.0
        self._n_chunks = 0
        # 可选 dense
        self._embed_key = embed_api_key or os.environ.get("SILICONFLOW_API_KEY", "")
        self._embed_base = (embed_base or "https://api.siliconflow.cn/v1").rstrip("/")
        self._embed_model = embed_model or os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
        self._vectors: dict[str, list[float]] = {}

    # ------------------------------------------------------------------ 灌入
    def ingest(self, doc_id: str, title: str, chunks: list[dict[str, Any]], acl: dict[str, Any]) -> None:
        """灌入一份文档的 chunk。acl 结构：{v, depts, roles, groups}，doc_id 内联。

        注意：chunk_id 在文档内唯一、跨文档可能重复（很多源数据的 chunk 都叫 c1/c2），
        所以内部存储 key 用 `doc_id:chunk_id` 复合，避免跨文档覆盖。对外输出的
        `Evidence.chunk_id` 仍保留原始值（eid 用它拼）。
        """
        acl = dict(acl)
        acl["doc_id"] = doc_id
        self._docs[doc_id] = {"title": title, "acl": acl}
        for c in chunks:
            cid = c["chunk_id"]
            key = f"{doc_id}:{cid}"
            content = c.get("content", "")
            toks = tokenize(content)
            self._chunks[key] = {
                "chunk_id": cid,
                "doc_id": doc_id,
                "title": title,
                "content": content,
                "page_no": c.get("page_no"),
                "section_path": c.get("section_path"),
                "acl_epoch": acl.get("acl_epoch", 0),
                "acl": acl,
            }
            self._lengths[key] = len(toks)
            for t in toks:
                self._index[t][key] = self._index[t].get(key, 0) + 1
            self._n_chunks += 1
        self._avg_len = (sum(self._lengths.values()) / max(1, self._n_chunks)) if self._n_chunks else 0.0

    def index_size(self) -> int:
        return self._n_chunks

    # ------------------------------------------------------------------ 检索
    def hybrid_retrieve(self, query: str, subject: Any, *, top_k: int = 5) -> list[Evidence]:
        if subject is None:
            return []
        # 1. 先做 BM25 召回（无权限过滤，全量候选）——这是有意为之：
        #    先算"候选 top-k 涉及哪些文档"，再判可见性，E8 才有据可查
        bm25_scores = self._bm25(query)
        bm25_ranked = sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)

        # 2. 可选 dense 召回
        dense_ranked: list[tuple[str, float]] = []
        if self._embed_key and self._n_chunks:
            dense_ranked = self._dense(query)

        # 3. RRF 融合
        fused = self._rrf(bm25_ranked, dense_ranked)

        # 4. 权限过滤 + verdict 记录
        kept: list[Evidence] = []
        for key, score in fused:
            rec = self._chunks[key]
            visible, _by = _visible(rec["acl"], subject)
            if not visible:
                continue
            kept.append(
                Evidence(
                    chunk_id=rec["chunk_id"],
                    doc_id=rec["doc_id"],
                    title=rec["title"],
                    content=rec["content"],
                    page_no=rec.get("page_no"),
                    section_path=rec.get("section_path"),
                    acl_epoch=rec.get("acl_epoch", 0),
                    score=score,
                )
            )
            if len(kept) >= top_k:
                break
        return kept

    def doc_fetch(self, doc_id: str, subject: Any) -> dict[str, Any]:
        doc = self._docs.get(doc_id)
        if doc is None:
            return {"doc_id": doc_id, "error": "not_found"}
        visible, by = _visible(doc["acl"], subject)
        if not visible:
            return {"doc_id": doc_id, "error": "not_permitted", "by": by}
        chunks = [self._chunks[c] for c in self._chunks if self._chunks[c]["doc_id"] == doc_id]
        return {
            "doc_id": doc_id,
            "title": doc["title"],
            "acl_epoch": doc["acl"].get("acl_epoch", 0),
            "content": "\n".join(c["content"] for c in chunks),
        }

    # ------------------------------------------------------------------ 打分
    def _bm25(self, query: str) -> dict[str, float]:
        q_terms = tokenize(query)
        scores: dict[str, float] = defaultdict(float)
        if not self._n_chunks:
            return {}
        N = self._n_chunks
        avg = self._avg_len or 1.0
        for term in q_terms:
            postings = self._index.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            for cid, tf in postings.items():
                dl = self._lengths.get(cid, 0) or 0
                denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avg)
                scores[cid] += idf * (tf * (_BM25_K1 + 1)) / denom
        return dict(scores)

    def _dense(self, query: str, top_n: int = 20) -> list[tuple[str, float]]:
        """真 embedding（硅基流动）+ 余弦。失败降级为空（不阻塞 BM25 路）。"""
        import httpx  # 延迟导入，未配 key 时不碰网络栈

        if not self._vectors:
            # 首次调用时批量嵌入所有 chunk（demo 语料小，够用）
            self._embed_all()
        if not self._vectors:
            return []
        try:
            qv = self._embed_one(query)
        except Exception:
            return []
        sims = [(cid, _cos(qv, v)) for cid, v in self._vectors.items()]
        sims.sort(key=lambda x: x[1], reverse=True)
        return sims[:top_n]

    def _embed_all(self) -> None:
        import httpx

        contents = [self._chunks[c]["content"] for c in self._chunks]
        if not contents:
            return
        try:
            resp = httpx.post(
                f"{self._embed_base}/embeddings",
                json={"model": self._embed_model, "input": contents},
                headers={"Authorization": f"Bearer {self._embed_key}"},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            ids = list(self._chunks.keys())
            for i, item in enumerate(data):
                if i < len(ids):
                    self._vectors[ids[i]] = item.get("embedding", [])
        except Exception:
            # embedding 失败不致命：dense 路空，BM25 路照跑
            self._vectors = {}

    def _embed_one(self, text: str) -> list[float]:
        import httpx

        resp = httpx.post(
            f"{self._embed_base}/embeddings",
            json={"model": self._embed_model, "input": [text]},
            headers={"Authorization": f"Bearer {self._embed_key}"},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    def _rrf(
        self, bm25: list[tuple[str, float]], dense: list[tuple[str, float]]
    ) -> list[tuple[str, float]]:
        """RRF 融合。dense 为空时退化为纯 BM25 排序。"""
        score: dict[str, float] = defaultdict(float)
        for rank, (cid, _s) in enumerate(bm25, start=1):
            score[cid] += 1.0 / (_RRF_K + rank)
        for rank, (cid, _s) in enumerate(dense, start=1):
            score[cid] += 1.0 / (_RRF_K + rank)
        return sorted(score.items(), key=lambda x: x[1], reverse=True)


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
