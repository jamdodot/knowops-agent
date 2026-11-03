"""清洗与质量闸门：解析产物的后处理与质量量化。

决策依据：docs/09-文档解析设计.md §4.3（页眉页脚清洗）+ §7（质量度量）。

清洗对检索质量的影响可能大于识别准确率：200 页报告的页眉重复 200 次，
会在向量空间形成 200 个几乎相同的点，查询时挤掉真正该召回的内容。
这属于"没洗掉"而不是"识别错"，所以特别难查。

质量闸门是"有条件兜底"的判据（docs/09 §5.2 关键点 3）：
不是 Docling 跑完就完事，而是产出 IR 后检查指标，不达标才调 MinerU 兜底。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import CleanConfig, QualityGateConfig
from .ir import Block, ParsedDocument

# 页码模式：独立的数字（可能带 -、页、Page 等）
_PAGE_NUMBER_RE = re.compile(r"^\s*(?:第\s*)?\d{1,4}(?:\s*页|\s*/\s*\d{1,4})?\s*$")
# 水印/横排干扰常见模式（纯装饰性重复行）
_WATERMARK_RE = re.compile(r"^(confidential|secret|draft|internal|机密|内部|草稿|仅供|样例|sample)", re.IGNORECASE)


@dataclass
class CleanReport:
    dropped_header_footer: int = 0
    dropped_page_number: int = 0
    dropped_watermark: int = 0
    dropped_toc: int = 0
    dropped_cover: int = 0
    merged_hyphenation: int = 0


def _normalize_ws(text: str) -> str:
    """压缩连续空白为单个空格（不合并换行，保留段落结构）。"""
    return re.sub(r"[ \t]+", " ", text).strip()


def clean_document(doc: ParsedDocument, cfg: CleanConfig) -> ParsedDocument:
    """就地清洗 doc.blocks，返回 doc（带 cleaned 标记）。"""
    report = CleanReport()
    blocks = doc.blocks

    # 1. 页眉页脚：同一行文本在 >60% 页面出现 → 判为页眉页脚，全删
    if cfg.strip_header_footer:
        text_to_pages: dict[str, set[int]] = {}
        for b in blocks:
            if b.type in ("paragraph", "heading", "list"):
                text_to_pages.setdefault(b.content.strip(), set()).add(b.page_no)
        total_pages = max((doc.page_count or 1), 1)
        header_footer = {
            t
            for t, pages in text_to_pages.items()
            # 两个条件都要满足：比例达标 + 至少出现在 2 个页面。
            # 否则单页文档里"出现 1 次"的行会因 1/1 >= 0.6 被误判成页眉页脚。
            if len(pages) / total_pages >= cfg.header_footer_repeat_ratio and len(pages) >= 2
        }
        if header_footer:
            blocks = [b for b in blocks if b.content.strip() not in header_footer]
            report.dropped_header_footer = len(doc.blocks) - len(blocks)

    # 2. 页码
    if cfg.strip_page_number:
        kept: list[Block] = []
        for b in blocks:
            if _PAGE_NUMBER_RE.match(b.content):
                report.dropped_page_number += 1
                continue
            kept.append(b)
        blocks = kept

    # 3. 水印
    if cfg.strip_watermark:
        kept = []
        for b in blocks:
            if _WATERMARK_RE.match(b.content):
                report.dropped_watermark += 1
                continue
            kept.append(b)
        blocks = kept

    # 4. 目录页：标题为"目录/Contents"的块，其所在页的所有导航性块删除
    if cfg.drop_toc:
        toc_pages = {b.page_no for b in blocks if b.content.strip().lower() in ("目录", "contents", "table of contents")}
        if toc_pages:
            kept = [b for b in blocks if b.page_no not in toc_pages]
            report.dropped_toc = len(blocks) - len(kept)
            blocks = kept

    # 5. 封面：第一页若是孤立标题块（无正文），整页删除
    if cfg.drop_cover and doc.page_count > 1 and blocks:
        first_page = blocks[0].page_no if blocks else 1
        first_page_blocks = [b for b in blocks if b.page_no == first_page]
        has_body = any(b.type in ("paragraph", "list", "table") for b in first_page_blocks)
        if not has_body and first_page_blocks:
            kept = [b for b in blocks if b.page_no != first_page]
            report.dropped_cover = len(blocks) - len(kept)
            blocks = kept

    # 6. 连字符复原（跨行断词）
    if cfg.merge_hyphenation:
        for b in blocks:
            if b.type in ("paragraph", "heading", "list") and "-\n" in b.content:
                b.content = b.content.replace("-\n", "")
                report.merged_hyphenation += 1

    # 7. 空白归一化
    if cfg.normalize_whitespace:
        for b in blocks:
            b.content = _normalize_ws(b.content)
            b.cleaned = True

    doc.blocks = [b for b in blocks if b.content.strip()]
    doc.reindex()
    doc.metadata["clean_report"] = report
    return doc


# ---------------------------------------------------------------------------
# 质量闸门
# ---------------------------------------------------------------------------


@dataclass
class QualityReport:
    table_integrity: float = 1.0
    reading_order_conf: float = 1.0
    header_footer_leak: float = 0.0
    passed: bool = True
    reason: str | None = None

    @property
    def needs_rescue(self) -> bool:
        return not self.passed


def _table_integrity(blocks: list[Block]) -> float:
    """表格完整率：简化估计。HTML 表格应含 <tr>/<td> 结构；无表格时默认 1.0。"""
    tables = [b for b in blocks if b.type == "table"]
    if not tables:
        return 1.0
    score = 0.0
    for t in tables:
        if "<tr" in t.content and "<td" in t.content:
            score += 1.0
        else:
            score += 0.5  # 有内容但结构不完整
    return score / len(tables)


def _reading_order_conf(blocks: list[Block]) -> float:
    """阅读顺序置信度：检查 order_index 是否随 page_no 单调递增。"""
    if not blocks:
        return 1.0
    ordered = all(
        blocks[i].page_no <= blocks[i + 1].page_no for i in range(len(blocks) - 1)
    )
    return 1.0 if ordered else 0.5


def _header_footer_leak(blocks: list[Block], repeat_threshold: int = 3) -> float:
    """页眉页脚残留率：清洗后仍高频重复（出现 ≥repeat_threshold 次）的行占比。

    清洗阶段已把"在 >60% 页面重复"的行删掉，这里度量的是**残留**——
    出现次数不够高、没被洗掉、但仍在多页重复的干扰行。
    """
    if not blocks:
        return 0.0
    from collections import Counter

    text_counts = Counter(b.content.strip() for b in blocks if b.type in ("paragraph", "heading", "list"))
    leak_lines = sum(cnt for cnt in text_counts.values() if cnt >= repeat_threshold)
    return min(1.0, leak_lines / len(blocks))


def evaluate_quality(doc: ParsedDocument, cfg: QualityGateConfig) -> QualityReport:
    """质量闸门：产出 QualityReport，判定是否要兜底/复核。"""
    blocks = doc.blocks
    report = QualityReport(
        table_integrity=_table_integrity(blocks),
        reading_order_conf=_reading_order_conf(blocks),
        header_footer_leak=_header_footer_leak(blocks),
    )

    failures: list[str] = []
    if report.table_integrity < cfg.min_table_integrity:
        failures.append(f"table_integrity {report.table_integrity:.2f} < {cfg.min_table_integrity}")
    if report.reading_order_conf < cfg.min_reading_order_conf:
        failures.append(f"reading_order_conf {report.reading_order_conf:.2f} < {cfg.min_reading_order_conf}")
    if report.header_footer_leak > cfg.max_header_footer_leak:
        failures.append(f"header_footer_leak {report.header_footer_leak:.2f} > {cfg.max_header_footer_leak}")

    report.passed = not failures
    report.reason = "; ".join(failures) if failures else None
    return report


__all__ = ["clean_document", "evaluate_quality", "CleanReport", "QualityReport"]
