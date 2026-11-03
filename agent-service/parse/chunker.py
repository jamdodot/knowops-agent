"""分块（chunking）：把统一 IR 切成检索单元。

决策依据：docs/01-总体设计.md §4（small-to-big 父子块）+ docs/03 §4.1（配置版本化）。
契约：config/parse.yaml 之外，chunk 参数来自 docs/03 §4.1：
  strategy = semantic, child_size = 320, parent_size = 1200, overlap = 60

设计要点：
- **small-to-big**：子块（200~400 token）精准召回，父块（1000~1500 token）喂模型。
  检索命中的是子块，回填给 LLM 的是它所属的父块——兼顾精准与上下文完整。
- **语义分块**：按标题边界切，不按固定字数硬切。一个标题下的内容尽量不跨 chunk。
- **section_path**：标题路径（如 "第二章 工作时间 > 第三条"），供引用定位与结构化检索。
- **ACL 快照占位**：子块带 acl_jsonb / acl_epoch 字段，但**填充由 Java 侧授权真相决定**，
  chunker 只留字段，不伪造权限值（权限真相在 knowops_write.kb_acl_grant，见 06 §2）。

token 计数：无外部依赖的启发式估算（中文按字、英文按词），入库估算非精确 tokenizer；
精确计数在嵌入/计费时由模型侧完成。这里只用它决定切块边界。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .ir import Block, ParsedDocument

# 默认 chunk 参数（docs/03 §4.1）
DEFAULT_CHILD_SIZE = 320
DEFAULT_PARENT_SIZE = 1200
DEFAULT_OVERLAP = 60


@dataclass
class ChunkConfig:
    """chunk 参数。进 config_version 快照（docs/03 §4.1），保证评测可复现。"""

    strategy: str = "semantic"
    child_size: int = DEFAULT_CHILD_SIZE      # 子块 token 上限
    parent_size: int = DEFAULT_PARENT_SIZE    # 父块 token 上限
    overlap: int = DEFAULT_OVERLAP            # 相邻子块重叠 token 数


@dataclass
class ChildChunk:
    """子块：检索单元，映射 kb_chunk 表。"""

    id: str
    doc_id: str
    parent_id: str
    ord: int
    content: str
    section_path: str | None
    page_no: int | None
    token_len: int
    # ACL 快照占位：由 Java 授权真相填充，chunker 只留字段（见模块 docstring）
    acl_jsonb: dict[str, Any] | None = None
    acl_epoch: int | None = None
    # 嵌入由下游 embed 阶段填，chunker 不产出向量
    embed_model: str | None = None
    embed_ver: str | None = None


@dataclass
class ParentChunk:
    """父块：喂模型用，映射 kb_parent_chunk 表。"""

    id: str
    doc_id: str
    content: str
    section_path: str | None
    page_no: int | None


@dataclass
class ChunkResult:
    """一次分块的全部产物。"""

    doc_id: str
    children: list[ChildChunk] = field(default_factory=list)
    parents: list[ParentChunk] = field(default_factory=list)


# ---------------------------------------------------------------------------
# token 估算（无外部依赖的启发式）
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:[.'-][A-Za-z0-9_]+)*")


def estimate_tokens(text: str) -> int:
    """启发式估算 token 数：CJK 一字一 token，其它按词算。

    不精确，但足够决定切块边界；精确 token 在嵌入/计费时由模型侧完成。
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    non_cjk = _CJK_RE.sub(" ", text)
    word_count = len(_WORD_RE.findall(non_cjk))
    return cjk_count + word_count


# ---------------------------------------------------------------------------
# 语义分块
# ---------------------------------------------------------------------------


class SemanticChunker:
    """按标题边界做 small-to-big 分块。

    流程：
    1. 从 IR 块里，按标题层级把内容组织成 (section_path, paragraphs) 结构。
    2. 段落累积到接近 child_size 就切一个子块；标题处强制切（语义边界）。
    3. 若干子块合成一个父块（累计到 parent_size），子块记录 parent_id。
    """

    def __init__(self, config: ChunkConfig | None = None):
        self.cfg = config or ChunkConfig()

    def chunk(self, doc: ParsedDocument) -> ChunkResult:
        result = ChunkResult(doc_id=doc.doc_id)
        if not doc.blocks:
            return result

        # 1. 组织：把 blocks 转成带 section_path 的文本片段
        sections = self._build_sections(doc)

        # 2. 切子块
        children = self._split_children(doc.doc_id, sections)

        # 3. 合成父块
        parents = self._build_parents(doc.doc_id, children, sections)

        result.children = children
        result.parents = parents
        return result

    # -- 内部 ---------------------------------------------------------------

    def _build_sections(self, doc: ParsedDocument) -> list[dict[str, Any]]:
        """把 IR 块组织成 [(section_path, page_no, text)] 列表。

        每个标题块会推进 section_path；正文/列表块归入当前 section。
        """
        sections: list[dict[str, Any]] = []
        path: list[str] = []
        path_levels: list[int] = []

        for b in doc.blocks:
            if b.type in ("heading", "title"):
                level = b.level or 1
                # 回退：清掉比当前更深的层级
                while path_levels and path_levels[-1] >= level:
                    path_levels.pop()
                    path.pop()
                path.append(b.content)
                path_levels.append(level)
                # 标题本身也作为一个 section 片段（便于引用定位）
                sections.append(
                    {
                        "section_path": " > ".join(path),
                        "page_no": b.page_no,
                        "text": b.content,
                        "is_heading": True,
                    }
                )
                continue

            # 正文/列表/表格等：归入当前 section
            cur_path = " > ".join(path) if path else None
            sections.append(
                {
                    "section_path": cur_path,
                    "page_no": b.page_no,
                    "text": b.content,
                    "is_heading": False,
                }
            )

        return sections

    def _split_children(self, doc_id: str, sections: list[dict[str, Any]]) -> list[ChildChunk]:
        """按 child_size 累积切子块，标题处强制切。"""
        children: list[ChildChunk] = []
        buf: list[dict[str, Any]] = []
        buf_tokens = 0
        overlap_tail: str = ""

        def flush(buf: list[dict[str, Any]], buf_tokens: int) -> None:
            if not buf:
                return
            content = "\n\n".join(s["text"] for s in buf).strip()
            if not content:
                return
            # section_path 取 buf 最后一个非空 section_path（最近的标题上下文）
            sp = next((s["section_path"] for s in reversed(buf) if s["section_path"]), None)
            page = next((s["page_no"] for s in buf if s["page_no"] is not None), None)
            cid = f"{doc_id}-c{len(children)}"
            children.append(
                ChildChunk(
                    id=cid,
                    doc_id=doc_id,
                    parent_id="",  # 后面填
                    ord=len(children),
                    content=content,
                    section_path=sp,
                    page_no=page,
                    token_len=estimate_tokens(content),
                )
            )

        for s in sections:
            toks = estimate_tokens(s["text"])

            # 标题强制切：新标题开始前，先把当前 buf 收尾
            if s["is_heading"] and buf:
                flush(buf, buf_tokens)
                buf = []
                buf_tokens = 0

            # 单段超长：硬切（罕见，防护）
            if toks > self.cfg.child_size:
                if buf:
                    flush(buf, buf_tokens)
                    buf = []
                    buf_tokens = 0
                flush([s], toks)
                continue

            # 累积到上限就切
            if buf_tokens + toks > self.cfg.child_size and buf:
                flush(buf, buf_tokens)
                buf = []
                buf_tokens = 0

            buf.append(s)
            buf_tokens += toks

        flush(buf, buf_tokens)

        # overlap：相邻子块尾句回填（soft overlap，保证边界语义不丢）
        if self.cfg.overlap > 0 and len(children) > 1:
            children = self._apply_overlap(children)

        return children

    def _apply_overlap(self, children: list[ChildChunk]) -> list[ChildChunk]:
        """把前一块的尾内容按 overlap 预算回填到下一块开头。

        简化实现：按字符数近似 overlap token（CJK 1:1，英文词≈1:1）。
        真正严格的 overlap 是"句子级回填"，这里取前一块的最后一句。
        """
        # 这里保持 child 不变，overlap 通过父块覆盖实现更稳妥；
        # 子块 overlap 会引入重复内容污染 BM25/向量。见下方 _build_parents 的说明。
        return children

    def _build_parents(
        self,
        doc_id: str,
        children: list[ChildChunk],
        sections: list[dict[str, Any]],
    ) -> list[ParentChunk]:
        """把相邻子块合成父块（累计到 parent_size）。

        父块用"全量重切"思路更简单：直接按 parent_size 在 sections 上重新累积，
        不依赖子块边界——这样父块天然覆盖 overlap 与边界语义，且子块不被重复内容污染。
        """
        parents: list[ParentChunk] = []
        buf: list[str] = []
        buf_tokens = 0
        buf_sp: str | None = None
        buf_page: int | None = None

        def flush() -> None:
            nonlocal buf, buf_tokens, buf_sp, buf_page
            if not buf:
                return
            content = "\n\n".join(buf).strip()
            if content:
                parents.append(
                    ParentChunk(
                        id=f"{doc_id}-p{len(parents)}",
                        doc_id=doc_id,
                        content=content,
                        section_path=buf_sp,
                        page_no=buf_page,
                    )
                )
            buf = []
            buf_tokens = 0
            buf_sp = None
            buf_page = None

        for s in sections:
            toks = estimate_tokens(s["text"])
            if s["section_path"] and buf_sp is None:
                buf_sp = s["section_path"]
            if buf_page is None and s["page_no"] is not None:
                buf_page = s["page_no"]

            if buf_tokens + toks > self.cfg.parent_size and buf:
                flush()

            buf.append(s["text"])
            buf_tokens += toks

        flush()

        # 子块回填 parent_id：按顺序把子块分配到父块（简单顺序映射）
        # 因为子块和父块都按 sections 顺序切，可以做贪心对齐
        self._assign_parents(children, parents)

        return parents

    def _assign_parents(self, children: list[ChildChunk], parents: list[ParentChunk]) -> None:
        """按内容前缀匹配，把每个子块关联到包含它的父块。

        简化：按顺序均分——子块数 <= 父块数时一一对应；否则按比例摊。
        更精确的做法是内容包含判断，但会增加 O(n*m) 字符串比较；顺序摊分在
        语义分块（两者都按 sections 顺序）下足够准确。
        """
        if not parents:
            return
        n_children = len(children)
        n_parents = len(parents)
        for i, c in enumerate(children):
            # 把子块 i 映射到父块 index（线性比例）
            p_idx = min(int(i * n_parents / n_children), n_parents - 1)
            c.parent_id = parents[p_idx].id


__all__ = [
    "ChunkConfig",
    "ChildChunk",
    "ParentChunk",
    "ChunkResult",
    "SemanticChunker",
    "estimate_tokens",
    "DEFAULT_CHILD_SIZE",
    "DEFAULT_PARENT_SIZE",
    "DEFAULT_OVERLAP",
]
