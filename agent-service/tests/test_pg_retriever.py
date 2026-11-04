"""PG 检索后端的纯函数测试（不需要 Docker / 数据库）。

真库验证走 `scripts/kw.py verify-pg`，这里只钉住"不连库也能验证"的那部分——
今天在真库上踩的坑，大多都能量化成这类断言：

1. 中文 eid 必须被引用校验认出来（Regex 只认 ASCII 时，答对了也判无票作答）。
2. DSN 只能有一个来源（两份密码不一致时 psycopg 只会说"密码错"）。
3. RRF 融合对空路要退化，不能把空当"无权限"。
4. 退路分词要出 2-gram，否则 ILIKE 召回不到中文。
"""

from __future__ import annotations

from domains.knowledge.nodes import _EID
from retrieval.base import Evidence
from retrieval.dsn import mask, pg_dsn
from retrieval.pg_retriever import _bigram_terms, _rrf


# ---------------------------------------------------------------- eid 解析
def test_cn_eid_is_recognized():
    """中文 doc_id 拼出的 eid 必须能被引用校验匹配（回归：只认 ASCII 的旧正则）。"""
    text = "须报财务总监审批 {e_费用报销管理办法_费用报销管理办法-c3}。"
    assert _EID.findall(text) == ["e_费用报销管理办法_费用报销管理办法-c3"]


def test_ascii_eid_still_recognized():
    assert _EID.findall("见 {e_doc_881_c1}") == ["e_doc_881_c1"]


# ---------------------------------------------------------------- DSN
def test_dsn_derived_from_postgres_env(monkeypatch):
    monkeypatch.delenv("KW_PG_DSN", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "knowops")
    monkeypatch.setenv("POSTGRES_USER", "knowops")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss word/1")
    assert pg_dsn() == "postgresql://knowops:p%40ss%20word%2F1@localhost:5433/knowops"


def test_dsn_explicit_wins(monkeypatch):
    monkeypatch.setenv("KW_PG_DSN", "postgresql://u:p@h:1/db")
    monkeypatch.setenv("POSTGRES_HOST", "elsewhere")
    assert pg_dsn() == "postgresql://u:p@h:1/db"


def test_dsn_none_when_incomplete(monkeypatch):
    monkeypatch.delenv("KW_PG_DSN", raising=False)
    for k in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert pg_dsn() is None


def test_mask_hides_password():
    assert "secret" not in mask("postgresql://knowops:secret@localhost:5433/knowops")


# ---------------------------------------------------------------- 融合 / 分词
def _ev(cid: str, doc: str = "d", score: float = 0.0) -> Evidence:
    return Evidence(chunk_id=cid, doc_id=doc, title=doc, content=cid, score=score)


def test_rrf_merges_two_routes():
    fused = _rrf([_ev("a"), _ev("b")], [_ev("b"), _ev("c")])
    ids = [e.chunk_id for e in fused]
    # b 同时出现在两路，必须排第一
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


def test_rrf_degrades_when_dense_empty():
    """dense 路挂了不能变成"无结果"——BM25 那一路必须原样透出。"""
    fused = _rrf([_ev("a"), _ev("b")], [])
    assert [e.chunk_id for e in fused] == ["a", "b"]


def test_bigram_terms_for_cn():
    terms = _bigram_terms("薪酬数据谁能查看")
    assert "薪酬" in terms and "酬数" in terms
    assert all(len(t) >= 2 for t in terms)


def test_bigram_terms_mixed():
    terms = _bigram_terms("费用超过 5000 元")
    assert "5000" in terms
    assert "费用" in terms
