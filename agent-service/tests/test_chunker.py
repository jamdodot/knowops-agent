"""分块单测：语义分块 / 父子块 / section_path / token 估算 / ord 顺序。"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from parse.chunker import (
    ChunkConfig,
    SemanticChunker,
    estimate_tokens,
)
from parse.ir import Block, ParsedDocument


def _doc(doc_id="d1"):
    return ParsedDocument(doc_id=doc_id, source_format="md")


def _b(id_, type_, content, page_no=1, level=None):
    return Block(id_, "d1", type_, content, page_no, (0, 0, 0, 0), level=level, order_index=0)


# ---------------------------------------------------------------------------
# token 估算
# ---------------------------------------------------------------------------


def test_estimate_tokens_chinese_char():
    assert estimate_tokens("员工考勤管理制度") == 8  # 8 个汉字


def test_estimate_tokens_english_words():
    assert estimate_tokens("hello world foo") == 3


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# 语义分块：标题边界
# ---------------------------------------------------------------------------


def test_chunker_splits_on_headings():
    doc = _doc()
    doc.with_block(_b("h1", "heading", "第一章 总则", level=1))
    doc.with_block(_b("p1", "paragraph", "第一条 内容A。" * 50))
    doc.with_block(_b("h2", "heading", "第二章 工时", level=1))
    doc.with_block(_b("p2", "paragraph", "第三条 内容B。" * 50))
    doc.reindex()

    chunker = SemanticChunker(ChunkConfig(child_size=100, parent_size=400, overlap=0))
    result = chunker.chunk(doc)

    # 标题处强制切 → 至少 2 个子块
    assert len(result.children) >= 2
    # 每个子块 content 非空
    assert all(c.content for c in result.children)
    # ord 连续
    assert [c.ord for c in result.children] == list(range(len(result.children)))


def test_chunker_preserves_section_path():
    doc = _doc()
    doc.with_block(_b("h1", "heading", "第二章 工作时间", level=1))
    doc.with_block(_b("p1", "paragraph", "第三条 员工应按时上下班。"))
    doc.reindex()

    chunker = SemanticChunker(ChunkConfig(child_size=100, parent_size=400))
    result = chunker.chunk(doc)

    # 正文块应继承标题的 section_path
    paths = [c.section_path for c in result.children]
    assert any(p and "第二章 工作时间" in p for p in paths)


def test_chunker_empty_doc():
    doc = _doc()
    chunker = SemanticChunker()
    result = chunker.chunk(doc)
    assert result.children == []
    assert result.parents == []


# ---------------------------------------------------------------------------
# 父子块
# ---------------------------------------------------------------------------


def test_chunker_builds_parents_and_links():
    doc = _doc()
    doc.with_block(_b("h1", "heading", "第一章", level=1))
    for i in range(20):
        doc.with_block(_b(f"p{i}", "paragraph", f"第{i}条 这是一段足够长的正文内容用于分块测试。"))
    doc.reindex()

    chunker = SemanticChunker(ChunkConfig(child_size=60, parent_size=200, overlap=0))
    result = chunker.chunk(doc)

    assert result.parents, "应有父块"
    # 每个子块都关联了父块
    assert all(c.parent_id for c in result.children)
    # parent_id 指向真实存在的父块
    parent_ids = {p.id for p in result.parents}
    assert all(c.parent_id in parent_ids for c in result.children)


def test_chunker_parent_larger_than_child():
    """父块 token 应 >= 多数子块 token（父块是上下文容器）。"""
    doc = _doc()
    doc.with_block(_b("h1", "heading", "章节", level=1))
    for i in range(10):
        doc.with_block(_b(f"p{i}", "paragraph", f"段落{i} " + "测试内容 " * 20))
    doc.reindex()

    chunker = SemanticChunker(ChunkConfig(child_size=50, parent_size=300, overlap=0))
    result = chunker.chunk(doc)

    max_child = max((c.token_len for c in result.children), default=0)
    max_parent = max((estimate_tokens(p.content) for p in result.parents), default=0)
    # 父块上限 300 > 子块上限 50，所以父块能装更多内容
    assert max_parent >= max_child


# ---------------------------------------------------------------------------
# 与 pipeline 的衔接
# ---------------------------------------------------------------------------


def test_chunker_accepts_pipeline_output(tmp_path: Path):
    """解析产出 → chunker 的全链路：md 解析后直接分块。"""
    from parse.router import Router

    md = tmp_path / "doc.md"
    md.write_text(
        "# 第一章 总则\n\n第一条 内容A。\n\n第二条 内容B。\n\n## 1.1 背景\n\n第三条 内容C。\n",
        encoding="utf-8",
    )
    doc = Router().parse(md, "d-1")

    chunker = SemanticChunker(ChunkConfig(child_size=100, parent_size=400))
    result = chunker.chunk(doc)

    assert result.doc_id == "d-1"
    assert result.children, "解析出的文档应能切出子块"
    assert all(c.content for c in result.children)
