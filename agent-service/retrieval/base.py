"""检索后端接口与证据数据结构。

`Evidence` 是检索结果的统一出口——下游（知识域节点）只认这个结构，
换 Memory / Pg 实现不改下游。字段对齐 `kb_chunk` 表 + 引用输出（docs/13 §4.3）：

- `eid`：引用编号（`e_{doc_id}_{chunk_id}`），模型只能引用本次证据集里的 eid；
- `doc_id/title/page_no/section_path`：引用定位（可定位到页）；
- `acl_epoch`：权限快照版本（引用输出要带，审计对齐用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Evidence:
    """一条检索证据（= 一个 chunk 的检索投影）。"""

    chunk_id: str
    doc_id: str
    title: str
    content: str
    page_no: int | None = None
    section_path: str | None = None
    acl_epoch: int = 0
    score: float = 0.0

    @property
    def eid(self) -> str:
        return f"e_{self.doc_id}_{self.chunk_id}"

    def to_row(self) -> dict[str, Any]:
        """转成知识域节点/工具期望的 dict 行（与旧内存夹具的字段一致）。"""
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "title": self.title,
            "content": self.content,
            "page_no": self.page_no,
            "section_path": self.section_path,
            "acl_epoch": self.acl_epoch,
            "eid": self.eid,
            "score": self.score,
        }


class Retriever(Protocol):
    """混合检索后端。权限在检索内部生效，调用方不传可见范围（docs/05 核心命题）。"""

    def hybrid_retrieve(
        self, query: str, subject: Any, *, top_k: int = 5
    ) -> list[Evidence]: ...

    def doc_fetch(self, doc_id: str, subject: Any) -> dict[str, Any]: ...

    def ingest(self, doc_id: str, title: str, chunks: list[dict[str, Any]], acl: dict[str, Any]) -> None:
        """灌入一份文档的 chunk（解析+分块后调用）。"""
        ...

    def index_size(self) -> int:
        """已索引 chunk 数（演示/自检用）。"""
        ...
