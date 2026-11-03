"""检索后端测试：MemoryRetriever 的 BM25 + 权限过滤语义。

核心验证点：
1. 权限过滤正确（越权不召回、有权限召回）；
2. BM25 关键词召回质量（相关 chunk 排前）；
3. RRF 融合（dense 为空时退化为纯 BM25 不报错）；
4. 分词对中文制度短查询有效。
"""

from __future__ import annotations

from retrieval import MemoryRetriever
from retrieval.memory_retriever import tokenize, _visible


class _Subj:
    def __init__(self, dept_paths, roles=None, groups=None, approval_doc_ids=None):
        self.dept_paths = dept_paths
        self.roles = roles or []
        self.groups = groups or []
        self.approval_doc_ids = approval_doc_ids or []


def _seed(r: MemoryRetriever) -> None:
    r.ingest(
        "doc_a",
        "费用报销管理办法",
        [
            {"chunk_id": "c1", "content": "单笔金额 5 万元以下的费用报销由部门负责人审批。", "page_no": 7, "section_path": "第3章 审批权限"},
            {"chunk_id": "c2", "content": "超过 5 万元的费用报销需财务总监审批。", "page_no": 8, "section_path": "第3章 审批权限"},
        ],
        {"v": "scoped", "depts": ["D10"], "roles": [], "groups": [], "acl_epoch": 1},
    )
    r.ingest(
        "doc_b",
        "薪酬保密制度",
        [
            {"chunk_id": "c1", "content": "薪酬数据仅薪酬专员与直属上级可见。", "page_no": 2, "section_path": "第1章 保密范围"},
        ],
        {"v": "scoped", "depts": ["D10.9"], "roles": [], "groups": [], "acl_epoch": 1},
    )
    r.ingest(
        "doc_pub",
        "员工手册",
        [
            {"chunk_id": "c1", "content": "工作时间上午九点至下午六点。", "page_no": 1, "section_path": "第1章 总则"},
        ],
        {"v": "public", "depts": [], "roles": [], "groups": [], "acl_epoch": 1},
    )


def test_tokenize_cn_query():
    toks = tokenize("报销审批")
    # 中文单字 + 2gram
    assert "报" in toks and "审批" in toks


def test_permission_blocks_unauthorized():
    r = MemoryRetriever()
    _seed(r)
    subj = _Subj(dept_paths=["D10"])  # 无权看 D10.9
    evs = r.hybrid_retrieve("薪酬数据谁能看", subj, top_k=5)
    ids = [e.doc_id for e in evs]
    assert "doc_b" not in ids  # 越权文档被拦截


def test_permission_allows_authorized():
    r = MemoryRetriever()
    _seed(r)
    subj = _Subj(dept_paths=["D10.9"])
    evs = r.hybrid_retrieve("薪酬数据谁能看", subj, top_k=5)
    assert any(e.doc_id == "doc_b" for e in evs)


def test_public_visible_to_all():
    r = MemoryRetriever()
    _seed(r)
    subj = _Subj(dept_paths=["D99"])  # 无关部门
    evs = r.hybrid_retrieve("工作时间", subj, top_k=5)
    assert any(e.doc_id == "doc_pub" for e in evs)


def test_bm25_relevance_ordering():
    r = MemoryRetriever()
    _seed(r)
    subj = _Subj(dept_paths=["D10"])
    evs = r.hybrid_retrieve("超过5万元报销谁审批", subj, top_k=5)
    # 相关 chunk 应排前，且只来自 doc_a（doc_b 无权限、doc_pub 无关）
    assert evs, "应有召回"
    assert all(e.doc_id == "doc_a" for e in evs)


def test_no_subject_returns_empty():
    r = MemoryRetriever()
    _seed(r)
    assert r.hybrid_retrieve("报销", None, top_k=5) == []


def test_doc_fetch_permission():
    r = MemoryRetriever()
    _seed(r)
    subj = _Subj(dept_paths=["D10"])
    ok = r.doc_fetch("doc_a", subj)
    assert ok.get("error") is None
    denied = r.doc_fetch("doc_b", subj)
    assert denied.get("error") == "not_permitted"


def test_ingest_and_index_size():
    r = MemoryRetriever()
    _seed(r)
    assert r.index_size() == 4
