"""解析引擎：探测 + 四路路由 + 引擎适配。

决策依据：docs/09-文档解析设计.md §5.2「四路路由（按页判断，不按文件）」。

引擎分工（M1 落地范围，见 docs/09 §5.5）：
- office    → Docling（.docx/.pptx/.xlsx/.html/.md 原生解析）
- native    → PyMuPDF L0 直取（有文字层的 PDF，毫秒级、比 OCR 准）
- pipeline  → Docling（覆盖率 40%~90% 的 PDF）
- scanned   → MinerU（<40% 纯扫描件）——**M2 才接入，M1 返回 NotImplemented 并进复核队列**

关键设计：**按页判断，不按文件**（config/parse.yaml §probe.per_page）。
一份 300 页报告可能前 10 页扫描件、后面全数字原生，整份一刀切必然有一半处理错。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol

from .config import ParseConfig
from .ir import Block, ParsedDocument

# ---------------------------------------------------------------------------
# 格式探测
# ---------------------------------------------------------------------------

# 扩展名 → 归一化格式名。office 一律走 Docling，md/txt 走原生直取。
_EXT_MAP = {
    ".docx": "docx",
    ".doc": "docx",
    ".pptx": "pptx",
    ".ppt": "pptx",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".pdf": "pdf",
}

# Docling 原生覆盖的格式（config/parse.yaml §routes.office）
OFFICE_FORMATS = {"docx", "pptx", "xlsx", "html", "markdown"}


def detect_format(path: Path | str) -> str:
    """按扩展名探测格式，未知格式抛 ValueError（config/parse.yaml §on_failure.unsupported_format）。"""
    p = Path(path)
    ext = p.suffix.lower()
    fmt = _EXT_MAP.get(ext)
    if fmt is None:
        raise UnsupportedFormatError(f"不支持的格式：{ext or '(无扩展名)'}")
    return fmt


class UnsupportedFormatError(ValueError):
    """未知格式。config/parse.yaml §on_failure.unsupported_format → reject_with_reason。"""


class EngineError(RuntimeError):
    """引擎内部错误。config/parse.yaml §on_failure.engine_error → retry_once_then_queue。"""


# ---------------------------------------------------------------------------
# 引擎接口
# ---------------------------------------------------------------------------


class ParseEngine(ABC):
    """解析引擎统一接口：输入文件，输出 ParsedDocument（IR）。

    任何引擎（PyMuPDF / Docling / MinerU / 未来 VLM）都实现这个协议，
    这样路由只依赖协议，不依赖具体引擎——换引擎不改路由。
    """

    name: str = "engine"

    @abstractmethod
    def parse(self, path: Path, doc_id: str) -> ParsedDocument:
        """把文件解析成统一 IR。"""


# ---------------------------------------------------------------------------
# L0 直取：PyMuPDF（native 路）
# ---------------------------------------------------------------------------


class PyMuPDFEngine(ParseEngine):
    """L0 数字原生直取：直接读 PDF 文字层，不跑 OCR。

    最快也最准的一层（docs/09 §1）：有文字层的 PDF 用 OCR 反而会引入识别错误。
    依赖 `pymupdf`（import fitz），未安装时抛 ImportError 并给出提示。
    """

    name = "pymupdf"

    def parse(self, path: Path, doc_id: str) -> ParsedDocument:
        try:
            import pymupdf  # noqa: F401  (fitz 旧名已废弃)
            import fitz
        except ImportError as exc:  # pragma: no cover - 依赖缺失提示
            raise EngineError(
                "L0 直取需要 pymupdf：pip install pymupdf"
            ) from exc

        doc = fitz.open(str(path))
        parsed = ParsedDocument(
            doc_id=doc_id, source_format="pdf", engine=self.name, route="native", page_count=doc.page_count
        )
        for page_no in range(doc.page_count):
            page = doc[page_no]
            # get_text("dict") 拿字号，用于标题启发式（正文小字号、标题大字号）
            d = page.get_text("dict")
            for bi, b in enumerate(d.get("blocks", [])):
                lines = b.get("lines", [])
                if not lines:
                    continue
                text_parts: list[str] = []
                max_size = 0.0
                for line in lines:
                    for span in line.get("spans", []):
                        s = span.get("text", "")
                        if s.strip():
                            text_parts.append(s.strip())
                        max_size = max(max_size, float(span.get("size", 0.0)))
                text = "".join(text_parts).strip()
                if not text:
                    continue
                bbox = b.get("bbox", (0, 0, 0, 0))
                # 标题启发式：字号明显大于正文（≥ 1.2 倍正文基准 11pt）
                btype = "heading" if max_size >= 13.0 else "paragraph"
                level = 1 if max_size >= 15.0 else (2 if btype == "heading" else None)
                parsed.with_block(
                    Block(
                        id=f"{doc_id}-p{page_no + 1}-b{bi}",
                        doc_id=doc_id,
                        type=btype,  # type: ignore[arg-type]
                        content=text,
                        page_no=page_no + 1,
                        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                        level=level,
                        order_index=len(parsed.blocks),
                    )
                )
        doc.close()
        return parsed.reindex()


# ---------------------------------------------------------------------------
# L1 主力：Docling（office + pipeline 路）
# ---------------------------------------------------------------------------


class DoclingEngine(ParseEngine):
    """Docling 主力引擎：Office 全格式 + PDF 语义解析（docs/09 §5.1）。

    依赖 `docling`，未安装时抛 EngineError 并给出提示。
    """

    name = "docling"

    def parse(self, path: Path, doc_id: str) -> ParsedDocument:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:  # pragma: no cover - 依赖缺失提示
            raise EngineError(
                "Docling 需要安装：pip install docling"
            ) from exc

        converter = DocumentConverter()
        result = converter.convert(str(path))
        doc = result.document

        fmt = detect_format(path)
        parsed = ParsedDocument(
            doc_id=doc_id,
            source_format=fmt,
            engine=self.name,
            route="office" if fmt in OFFICE_FORMATS else "pipeline",
            page_count=getattr(doc, "num_pages", 0) or 1,
        )

        # DoclingDocument 支持迭代 (item, level) 的结构化导出。
        # 这里走 Markdown 导出兜底，保证任何版本都能出可用 IR。
        for idx, (item, level) in enumerate(doc.iterate_items() if hasattr(doc, "iterate_items") else []):
            btype = _docling_type(item)
            content = _item_text(item)
            if not content.strip():
                continue
            parsed.with_block(
                Block(
                    id=f"{doc_id}-{idx}",
                    doc_id=doc_id,
                    type=btype,
                    content=content,
                    page_no=_item_page(item),
                    bbox=_item_bbox(item),
                    level=level if btype == "heading" else None,
                    order_index=idx,
                )
            )

        if not parsed.blocks:
            # iterate_items 不可用时的兜底：用导出的 Markdown 整体作为一个文本块
            try:
                md = doc.export_to_markdown()
            except Exception as exc:  # pragma: no cover
                raise EngineError(f"Docling 导出失败：{exc}") from exc
            if md.strip():
                parsed.with_block(
                    Block(
                        id=f"{doc_id}-md",
                        doc_id=doc_id,
                        type="paragraph",
                        content=md,
                        page_no=1,
                        bbox=(0.0, 0.0, 0.0, 0.0),
                        order_index=0,
                    )
                )

        return parsed.reindex()


def _docling_type(item: object) -> str:
    label = getattr(item, "label", None)
    name = str(label).lower() if label is not None else ""
    mapping = {
        "title": "title",
        "heading": "heading",
        "section_header": "heading",
        "paragraph": "paragraph",
        "text": "paragraph",
        "table": "table",
        "figure": "figure",
        "picture": "figure",
        "caption": "caption",
        "formula": "formula",
        "code": "code",
        "list_item": "list",
        "footnote": "footnote",
    }
    return mapping.get(name, "paragraph")


def _item_text(item: object) -> str:
    # Docling item 通常有 .text 或可 str()。稳健取值。
    text = getattr(item, "text", None)
    if text is not None:
        return str(text).strip()
    return str(item).strip()


def _item_page(item: object) -> int:
    prov = getattr(item, "prov", None)
    if prov is not None:
        pg = getattr(prov, "page_no", None)
        if pg is not None:
            return int(pg) + 1  # Docling 页码从 0 起
    return 1


def _item_bbox(item: object) -> tuple[float, float, float, float]:
    prov = getattr(item, "prov", None)
    if prov is not None:
        bbox = getattr(prov, "bbox", None)
        if bbox is not None and len(bbox) == 4:
            return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 兜底专家：MinerU（scanned 路）—— M2 接入
# ---------------------------------------------------------------------------


class MinerUEngine(ParseEngine):
    """MinerU 兜底专家（docs/09 §5.2）。**M2 才接入。**

    M1 阶段这里抛 NotImplemented，路由会把这类文档送进复核队列，
    不阻塞其它文档入库（docs/09 §7 与 config/parse.yaml §on_failure.low_confidence）。
    """

    name = "mineru"

    def parse(self, path: Path, doc_id: str) -> ParsedDocument:
        raise EngineError(
            "MinerU 兜底在 M2 接入（docs/09 §5.5）。当前阶段扫描件文档进入复核队列。"
        )


__all__ = [
    "ParseEngine",
    "PyMuPDFEngine",
    "DoclingEngine",
    "MinerUEngine",
    "detect_format",
    "OFFICE_FORMATS",
    "UnsupportedFormatError",
    "EngineError",
]
