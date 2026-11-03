"""解析流水线编排 + parse_report 上报。

决策依据：docs/09-文档解析设计.md §7（质量度量）+ config/parse.yaml §9（report）。

流程：探测 → 路由 → 引擎解析 → 清洗 → 质量闸门 → 上报 parse_report。
低分文档进人工复核队列（review_required），不阻塞其它文档入库。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .clean import CleanReport, QualityReport, clean_document, evaluate_quality
from .config import ParseConfig
from .engines import EngineError, UnsupportedFormatError
from .ir import ParsedDocument
from .router import Router


@dataclass
class ParseReport:
    """config/parse.yaml §9 report.fields 的可执行实现。"""

    doc_id: str
    route: str
    engine: str
    deploy: str = "local"
    text_coverage: float | None = None
    table_count: int = 0
    table_integrity: float = 1.0
    reading_order_conf: float = 1.0
    header_footer_leak: float = 0.0
    elapsed_ms: int = 0
    peak_memory_mb: int = 0
    rescued: bool = False
    review_required: bool = False
    status: str = "ok"                      # ok | review | error | unsupported
    error: str | None = None
    clean: CleanReport | None = None
    page_count: int = 0
    block_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.clean is not None:
            d["clean"] = asdict(self.clean)
        return d


class ParsePipeline:
    """解析流水线：Router → clean → gate → report 的一次性编排。"""

    def __init__(self, config: ParseConfig | None = None):
        self.config = config or ParseConfig.load()
        self.router = Router(self.config)

    def parse(self, path: Path | str, doc_id: str) -> tuple[ParsedDocument, ParseReport]:
        """解析一份文档，返回 (IR, report)。不抛异常——失败记入 report.status。"""
        start = time.perf_counter()
        p = Path(path)

        try:
            doc = self.router.parse(p, doc_id)
        except UnsupportedFormatError as exc:
            return (
                ParsedDocument(doc_id=doc_id, source_format="unknown"),
                ParseReport(doc_id=doc_id, route="", engine="", status="unsupported", error=str(exc)),
            )
        except EngineError as exc:
            return (
                ParsedDocument(doc_id=doc_id, source_format="unknown"),
                ParseReport(doc_id=doc_id, route="", engine="", status="error", error=str(exc)),
            )

        # 扫描件（M2 MinerU 未接入）：直接进复核队列，不阻塞
        if doc.has_scanned_pages or doc.metadata.get("pending_rescue"):
            return (
                doc,
                ParseReport(
                    doc_id=doc_id,
                    route="scanned",
                    engine="mineru",
                    text_coverage=doc.metadata.get("text_coverage"),  # type: ignore[arg-type]
                    page_count=doc.page_count,
                    block_count=len(doc.blocks),
                    status="review",
                    review_required=True,
                    rescued=True,
                ),
            )

        # 清洗
        clean_document(doc, self.config.clean)

        # 质量闸门
        quality = evaluate_quality(doc, self.config.quality_gate)

        elapsed = int((time.perf_counter() - start) * 1000)
        report = ParseReport(
            doc_id=doc_id,
            route=doc.route or "unknown",
            engine=doc.engine or "unknown",
            text_coverage=doc.metadata.get("text_coverage"),  # type: ignore[arg-type]
            table_count=sum(1 for b in doc.blocks if b.type == "table"),
            table_integrity=quality.table_integrity,
            reading_order_conf=quality.reading_order_conf,
            header_footer_leak=quality.header_footer_leak,
            elapsed_ms=elapsed,
            page_count=doc.page_count,
            block_count=len(doc.blocks),
            clean=doc.metadata.get("clean_report"),  # type: ignore[arg-type]
        )

        if not quality.passed:
            report.status = "review"
            report.review_required = True
            report.rescued = True
            report.error = quality.reason
            # 标记需要兜底，但 IR 仍返回——低分不阻塞，进复核队列
            doc.metadata["quality"] = quality

        return doc, report


__all__ = ["ParsePipeline", "ParseReport"]
