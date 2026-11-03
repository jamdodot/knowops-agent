"""真实 PDF 解析测试：L0 直取 + 标题启发式 + 路由。

依赖 pymupdf（已在 requirements.txt）。pymupdf 未安装时整文件 skip，
保证 CI 无该依赖也能跑其它测试。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

fitz = pytest.importorskip("fitz")  # pymupdf

from parse.engines import PyMuPDFEngine  # noqa: E402
from parse.router import Router, route_for  # noqa: E402


def _make_cn_pdf(tmp_path: Path) -> Path:
    """造一份中文 PDF（带标题字号差异 + 多页 + 足够文字密度触发 native 路）。"""
    p = tmp_path / "doc.pdf"
    doc = fitz.open()

    page1 = doc.new_page()
    page1.insert_text((72, 72), "员工考勤管理制度", fontsize=20, fontname="china-s")
    page1.insert_text((72, 120), "第一章 总则", fontsize=15, fontname="china-s")
    y = 150
    for i in range(6):
        page1.insert_text((72, y), f"第{i+1}条 为规范公司考勤管理，明确出勤请假加班标准，制定本制度。", fontsize=11, fontname="china-s")
        y += 20

    page2 = doc.new_page()
    page2.insert_text((72, 72), "第二章 工作时间", fontsize=15, fontname="china-s")
    y = 102
    for i in range(6):
        page2.insert_text((72, y), f"第{i+7}条 标准工时上午九点至十二点，下午一点至六点，每周五天。", fontsize=11, fontname="china-s")
        y += 20

    doc.save(str(p))
    doc.close()
    return p


def test_pymupdf_engine_extracts_cn_text(tmp_path: Path):
    pdf = _make_cn_pdf(tmp_path)
    engine = PyMuPDFEngine()
    doc = engine.parse(pdf, "d-cn-1")

    # 中文完整抽取
    texts = "".join(b.content for b in doc.blocks)
    assert "员工考勤管理制度" in texts
    assert "为规范公司考勤管理" in texts
    assert doc.page_count == 2


def test_pymupdf_engine_detects_headings_by_fontsize(tmp_path: Path):
    pdf = _make_cn_pdf(tmp_path)
    engine = PyMuPDFEngine()
    doc = engine.parse(pdf, "d-cn-1")

    headings = [b for b in doc.blocks if b.type == "heading"]
    # 20pt 标题 + 15pt 章节标题都应识别为 heading
    assert len(headings) >= 3
    assert any(b.content == "员工考勤管理制度" and b.level == 1 for b in headings)
    assert any(b.content == "第一章 总则" for b in headings)


def test_router_routes_cn_pdf_to_native(tmp_path: Path):
    pdf = _make_cn_pdf(tmp_path)
    router = Router()
    doc = router.parse(pdf, "d-cn-2")

    assert doc.route == "native"
    assert doc.engine == "pymupdf"
    assert doc.has_scanned_pages is False


def test_router_routes_empty_pdf_to_scanned(tmp_path: Path):
    """无文字层的 PDF 应路由到 scanned（兜底）。"""
    p = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page()  # 空白页，无文字
    doc.save(str(p))
    doc.close()

    router = Router()
    parsed = router.parse(p, "d-scan-1")
    assert parsed.route == "scanned"
    assert parsed.has_scanned_pages is True
