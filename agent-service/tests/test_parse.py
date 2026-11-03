"""解析链路单测：IR / 路由 / 清洗 / 质量闸门 / pipeline 编排。

只测不依赖外部解析器（PyMuPDF/Docling）的路径：
- IR 数据模型与 reindex
- route_for 纯函数判定
- md/txt 原生直取端到端（无需任何解析器）
- 清洗规则（页眉页脚/页码/水印/TOC/封面/连字符）
- 质量闸门与 parse_report

PDF 路径依赖 PyMuPDF/Docling，需在装有依赖的环境跑，这里不强制。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from parse.clean import clean_document, evaluate_quality
from parse.config import CleanConfig, ParseConfig, QualityGateConfig
from parse.engines import detect_format, UnsupportedFormatError
from parse.ir import Block, ParsedDocument
from parse.pipeline import ParsePipeline
from parse.router import route_for, Router


# ---------------------------------------------------------------------------
# 统一 IR
# ---------------------------------------------------------------------------


def test_block_fields_match_ir_spec():
    b = Block(
        id="doc1-b0",
        doc_id="doc1",
        type="heading",
        content="第一章 概述",
        page_no=1,
        bbox=(0, 0, 100, 20),
        level=1,
        order_index=0,
    )
    assert b.id == "doc1-b0"
    assert b.type == "heading"
    assert b.level == 1
    assert b.cleaned is False


def test_parsed_document_reindex_sorts_by_page_then_order():
    doc = ParsedDocument(doc_id="d", source_format="pdf")
    doc.with_block(Block("b2", "d", "paragraph", "x", 2, (0, 0, 0, 0), order_index=0))
    doc.with_block(Block("b1", "d", "paragraph", "y", 1, (0, 0, 0, 0), order_index=5))
    doc.reindex()
    assert [b.id for b in doc.blocks] == ["b1", "b2"]
    assert doc.blocks[0].order_index == 0
    assert doc.blocks[1].order_index == 1


def test_headings_filter():
    doc = ParsedDocument(doc_id="d", source_format="md")
    doc.with_block(Block("h", "d", "heading", "标题", 1, (0, 0, 0, 0), level=1))
    doc.with_block(Block("p", "d", "paragraph", "正文", 1, (0, 0, 0, 0)))
    assert [b.id for b in doc.headings()] == ["h"]


# ---------------------------------------------------------------------------
# 格式探测与路由
# ---------------------------------------------------------------------------


def test_detect_format():
    assert detect_format("a.pdf") == "pdf"
    assert detect_format("a.docx") == "docx"
    assert detect_format("a.md") == "markdown"
    assert detect_format("a.PPTX") == "pptx"


def test_detect_format_unsupported():
    with pytest.raises(UnsupportedFormatError):
        detect_format("a.xyz")


def test_route_for_office_and_text():
    assert route_for("docx") == "office"
    assert route_for("markdown") == "office"
    assert route_for("text") == "office"


def test_route_for_pdf_by_coverage():
    assert route_for("pdf", text_coverage=0.95) == "native"
    assert route_for("pdf", text_coverage=0.60) == "pipeline"
    assert route_for("pdf", text_coverage=0.10) == "scanned"


def test_route_for_pdf_requires_coverage():
    with pytest.raises(ValueError):
        route_for("pdf")


# ---------------------------------------------------------------------------
# md 原生直取端到端（无需解析器依赖）
# ---------------------------------------------------------------------------


def test_router_parses_markdown(tmp_path: Path):
    md = tmp_path / "doc.md"
    md.write_text(
        "# 第一章 总则\n\n"
        "这是第一段正文。\n\n"
        "## 1.1 背景\n\n"
        "- 要点一\n- 要点二\n\n"
        "第二段正文。\n",
        encoding="utf-8",
    )
    router = Router()
    doc = router.parse(md, "doc-md-1")

    assert doc.source_format == "markdown"
    assert doc.route == "office"
    headings = doc.headings()
    assert [h.content for h in headings] == ["第一章 总则", "1.1 背景"]
    types = [b.type for b in doc.blocks]
    assert "list" in types
    assert "paragraph" in types


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------


def _mk_block(id_, content, page_no, type_="paragraph"):
    return Block(id_, "d", type_, content, page_no, (0, 0, 0, 0), order_index=0)


def test_clean_strips_header_footer_repeated_lines():
    cfg = CleanConfig(strip_header_footer=True, header_footer_repeat_ratio=0.6)
    doc = ParsedDocument(doc_id="d", source_format="md", page_count=3)
    for page in (1, 2, 3):
        doc.with_block(_mk_block(f"h{page}", "内部资料 保密", page))  # 3/3 页重复
        doc.with_block(_mk_block(f"p{page}", f"第{page}页正文", page))
    clean_document(doc, cfg)
    contents = [b.content for b in doc.blocks]
    assert "内部资料 保密" not in contents
    assert any("正文" in c for c in contents)


def test_clean_strips_page_number():
    cfg = CleanConfig(strip_page_number=True, strip_header_footer=False)
    doc = ParsedDocument(doc_id="d", source_format="md")
    doc.with_block(_mk_block("p1", "3", 1))
    doc.with_block(_mk_block("p2", "正文内容", 1))
    clean_document(doc, cfg)
    assert [b.content for b in doc.blocks] == ["正文内容"]


def test_clean_strips_watermark():
    cfg = CleanConfig(strip_watermark=True, strip_header_footer=False)
    doc = ParsedDocument(doc_id="d", source_format="md")
    doc.with_block(_mk_block("w", "CONFIDENTIAL", 1))
    doc.with_block(_mk_block("p", "正文", 1))
    clean_document(doc, cfg)
    assert [b.content for b in doc.blocks] == ["正文"]


def test_clean_drops_toc_page():
    cfg = CleanConfig(drop_toc=True, strip_header_footer=False)
    doc = ParsedDocument(doc_id="d", source_format="md", page_count=3)
    doc.with_block(_mk_block("toc", "目录", 1))
    doc.with_block(_mk_block("nav", "第一章 .... 3", 1))
    doc.with_block(_mk_block("body", "真正正文", 2))
    clean_document(doc, cfg)
    assert all(b.page_no != 1 for b in doc.blocks)
    assert any(b.content == "真正正文" for b in doc.blocks)


def test_clean_merges_hyphenation():
    cfg = CleanConfig(merge_hyphenation=True, strip_header_footer=False)
    doc = ParsedDocument(doc_id="d", source_format="md")
    doc.with_block(_mk_block("h", "国际-\n贸易", 1))
    clean_document(doc, cfg)
    assert doc.blocks[0].content == "国际贸易"


# ---------------------------------------------------------------------------
# 质量闸门
# ---------------------------------------------------------------------------


def test_quality_gate_passes_clean_doc():
    cfg = QualityGateConfig()
    doc = ParsedDocument(doc_id="d", source_format="md")
    doc.with_block(_mk_block("p1", "段落一", 1))
    doc.with_block(_mk_block("p2", "段落二", 2))
    doc.reindex()
    q = evaluate_quality(doc, cfg)
    assert q.passed is True
    assert q.table_integrity == 1.0
    assert q.reading_order_conf == 1.0


def test_quality_gate_detects_bad_reading_order():
    cfg = QualityGateConfig()
    doc = ParsedDocument(doc_id="d", source_format="md")
    doc.with_block(_mk_block("p2", "后页", 2))
    doc.with_block(_mk_block("p1", "前页", 1))
    # 不 reindex，故意让 order 错乱（order_index 都是 0，page 2 在 page 1 前）
    q = evaluate_quality(doc, cfg)
    assert q.passed is False
    assert "reading_order" in (q.reason or "")


# ---------------------------------------------------------------------------
# pipeline 编排 + parse_report
# ---------------------------------------------------------------------------


def test_pipeline_parses_markdown_and_reports(tmp_path: Path):
    md = tmp_path / "ok.md"
    md.write_text("# 标题\n\n这是正文。\n", encoding="utf-8")
    pipeline = ParsePipeline()
    doc, report = pipeline.parse(md, "doc-1")

    assert report.status == "ok"
    assert report.route == "office"
    assert report.block_count > 0
    assert report.review_required is False
    assert report.to_dict()["doc_id"] == "doc-1"


def test_pipeline_unsupported_format(tmp_path: Path):
    f = tmp_path / "bad.xyz"
    f.write_text("data", encoding="utf-8")
    pipeline = ParsePipeline()
    doc, report = pipeline.parse(f, "doc-2")
    assert report.status == "unsupported"
    assert report.review_required is False


def test_parse_config_loads():
    cfg = ParseConfig.load()
    assert cfg.probe.text_coverage_high == 0.90
    assert cfg.probe.text_coverage_low == 0.40
    assert cfg.quality_gate.min_table_integrity == 0.85
    assert cfg.clean.strip_header_footer is True
    assert cfg.routes["office"].engine == "docling"
    assert cfg.routes["native"].engine == "pymupdf"
