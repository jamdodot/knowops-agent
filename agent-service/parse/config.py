"""解析配置加载：把 config/parse.yaml 读成强类型对象。

决策依据：docs/09-文档解析设计.md §5.4「路由与闸门全部写在配置里，不写死在代码」。
契约：config/parse.yaml（探测阈值、四路路由、质量闸门、表格链、清洗规则、资源约束）。

原则：阈值/开关全部来自 YAML，代码里不出现魔法数字。这样调参只改配置，
评测回归时能对"改了哪个阈值带来什么变化"做归因。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PARSE_YAML = REPO_ROOT / "config" / "parse.yaml"


class ProbeConfig(BaseModel):
    text_coverage_high: float = 0.90
    text_coverage_low: float = 0.40
    layout_complexity_high: float = 0.70
    sample_pages: int = 3
    per_page: bool = True


class RouteConfig(BaseModel):
    engine: str
    table: str | None = None
    backend: str | None = None
    ocr: bool | None = None
    ocr_fallback: bool | None = None
    strip_running_headers: bool | None = None
    deploy: str | None = None
    concurrency: int | None = None


class TableConfig(BaseModel):
    strategy: str = "cascade"
    chain: list[str] = Field(default_factory=lambda: ["camelot", "pdfplumber", "docling_tableformer"])
    output_format: Literal["html", "markdown"] = "html"
    min_cells: int = 4
    merge_cross_page: bool = True


class QualityGateConfig(BaseModel):
    min_table_integrity: float = 0.85
    min_reading_order_conf: float = 0.70
    max_header_footer_leak: float = 0.05
    on_fail: str = "rescue_then_review"


class CleanConfig(BaseModel):
    strip_header_footer: bool = True
    header_footer_repeat_ratio: float = 0.6
    strip_watermark: bool = True
    strip_page_number: bool = True
    merge_hyphenation: bool = True
    normalize_whitespace: bool = True
    drop_toc: bool = True
    drop_cover: bool = True


class IrConfig(BaseModel):
    block_types: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    coordinate_system: str = "pdf_points"
    persist: str = "jsonb"
    version: str = "v1"


class ReportConfig(BaseModel):
    fields: list[str] = Field(default_factory=list)
    emit_to: list[str] = Field(default_factory=list)


class ResourcesConfig(BaseModel):
    max_concurrent_docs: int = 2
    mineru_concurrent: int = 1
    cpu_threads_per_doc: int = 4
    max_file_size_mb: int = 200
    max_pages: int = 800
    schedule_hint: str = "offline"
    peak_local_mb: int = 5120


class ParseConfig(BaseModel):
    """config/parse.yaml 的强类型映射。"""

    version: int = 1
    parse_mode: Literal["local", "hybrid"] = "local"
    probe: ProbeConfig = Field(default_factory=ProbeConfig)
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    table: TableConfig = Field(default_factory=TableConfig)
    quality_gate: QualityGateConfig = Field(default_factory=QualityGateConfig)
    clean: CleanConfig = Field(default_factory=CleanConfig)
    ir: IrConfig = Field(default_factory=IrConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ParseConfig":
        p = Path(path) if path else DEFAULT_PARSE_YAML
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        return cls.model_validate(raw)


__all__ = [
    "ParseConfig",
    "ProbeConfig",
    "RouteConfig",
    "TableConfig",
    "QualityGateConfig",
    "CleanConfig",
    "IrConfig",
    "ReportConfig",
    "ResourcesConfig",
    "DEFAULT_PARSE_YAML",
]
