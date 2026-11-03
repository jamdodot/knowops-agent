"""统一 IR（Intermediate Representation）：四路解析引擎的唯一出口。

决策依据：docs/09-文档解析设计.md §6「输出不能只有 Markdown」。
契约：config/parse.yaml §6（ir.block_types / ir.fields / ir.version）。

设计约束（为什么要有 IR 而不直接输出 Markdown）：
- 页码    → 引用要能定位到"第 N 页"，用户才能核对
- bbox    → 前端高亮、原文溯源
- 块类型  → 分块策略要能区分"正文"和"表注"，差异化切块

任何引擎（L0 直取 / Docling / MinerU / 未来的 VLM）都必须收敛到这里的 `Block`。
下游 chunker 只认 IR，不认引擎——这是"换引擎不改下游"成立的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

# 与 config/parse.yaml §6 ir.block_types 对齐。
# heading 是 title 的规范化写法：统一 IR 里用 heading，避免"title 是标题还是文档标题"的歧义。
BlockType = Literal[
    "title",
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "caption",
    "formula",
    "code",
    "footnote",
]

# 坐标系：pdf_points（config/parse.yaml §6 ir.coordinate_system），渲染时再换算。
BBox = tuple[float, float, float, float]  # (x0, y0, x1, y1)


@dataclass
class Block:
    """IR 的基本单元：一段带类型、页码、坐标与层级关系的内容。

    content 的格式由 type 决定：
    - table    → HTML（保 rowspan/colspan，config/parse.yaml §3 output_format: html）
    - formula  → LaTeX
    - 其余     → Markdown 文本
    """

    id: str
    doc_id: str
    type: BlockType
    content: str
    page_no: int
    bbox: BBox
    level: int | None = None       # 标题层级（1~N），非标题块为 None
    parent_id: str | None = None   # 标题树：指向最近的祖先标题块 id
    order_index: int = 0           # 阅读顺序全局序号，供 chunker 恢复顺序

    # 清洗后才会填（清洗在 quality gate 之前跑，见 pipeline 编排）
    cleaned: bool = False


@dataclass
class ParsedDocument:
    """一份文档解析后的完整 IR，是 chunker 的输入。

    metadata 承载文档级信息（来源格式、页数、引擎、是否有扫描页等），
    与 Block 分开，避免每个块都重复携带。
    """

    doc_id: str
    source_format: str                 # 探测出的真实格式（pdf/docx/...）
    blocks: list[Block] = field(default_factory=list)
    engine: str | None = None          # 实际主力引擎（docling/pymupdf/...）
    route: str | None = None           # office/native/pipeline/scanned
    page_count: int = 0
    has_scanned_pages: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def with_block(self, block: Block) -> "ParsedDocument":
        """追加一个块并返回 self（便于链式构建）。"""
        self.blocks.append(block)
        return self

    def reindex(self) -> "ParsedDocument":
        """按 page_no 重排并重写 order_index，保证阅读顺序全局一致。"""
        self.blocks.sort(key=lambda b: (b.page_no, b.order_index))
        for i, b in enumerate(self.blocks):
            b.order_index = i
        return self

    def headings(self) -> list[Block]:
        return [b for b in self.blocks if b.type in ("heading", "title")]


__all__ = ["Block", "BlockType", "BBox", "ParsedDocument", "replace"]
