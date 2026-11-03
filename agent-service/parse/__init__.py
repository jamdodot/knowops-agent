"""文档解析：M1 窄链路的入口能力（域无关，与 runtime 平级）。

决策依据：docs/09-文档解析设计.md；契约：config/parse.yaml。

对外形态：
- `ParsePipeline`：一次编排（探测→路由→引擎→清洗→质量闸门→report）
- `ParsedDocument` / `Block`：统一 IR，下游 chunker 只认它
- `ParseConfig`：配置强类型

M1 落地范围（docs/09 §5.5）：
- L0 直取（PyMuPDF）+ Docling（PDF/Word/PPT/MD），约覆盖 80% 企业文档
- 质量闸门 + parse_report 指标
M2 再接入 MinerU 兜底（scanned 路）；VLM 直出不排期（只写预研）。
"""

from .clean import CleanReport, QualityReport, clean_document, evaluate_quality
from .config import ParseConfig
from .engines import (
    DoclingEngine,
    EngineError,
    MinerUEngine,
    ParseEngine,
    PyMuPDFEngine,
    UnsupportedFormatError,
    detect_format,
)
from .ir import Block, BlockType, BBox, ParsedDocument
from .pipeline import ParsePipeline, ParseReport
from .router import Router, route_for

__all__ = [
    "ParsePipeline",
    "ParseReport",
    "ParsedDocument",
    "Block",
    "BlockType",
    "BBox",
    "ParseConfig",
    "Router",
    "route_for",
    "ParseEngine",
    "PyMuPDFEngine",
    "DoclingEngine",
    "MinerUEngine",
    "detect_format",
    "clean_document",
    "evaluate_quality",
    "CleanReport",
    "QualityReport",
    "UnsupportedFormatError",
    "EngineError",
]
