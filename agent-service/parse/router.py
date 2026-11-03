"""解析路由：探测 → 四路分派 → 引擎。

决策依据：docs/09-文档解析设计.md §5.2「四路路由（按页判断，不按文件）」。

路由规则（config/parse.yaml §probe / §routes）：
- markdown/text/txt → L0 原生直取（比 Docling 更直接，无依赖）
- office（docx/pptx/xlsx/html/md）→ Docling
- pdf → 按页探测文字覆盖率：
    > 90%   → native（PyMuPDF L0 直取）
    40~90%  → pipeline（Docling）
    < 40%   → scanned（MinerU，M2 接入；M1 送复核队列）
"""

from __future__ import annotations

from pathlib import Path

from .config import ParseConfig
from .engines import (
    OFFICE_FORMATS,
    DoclingEngine,
    EngineError,
    MinerUEngine,
    ParseEngine,
    PyMuPDFEngine,
    detect_format,
)
from .ir import Block, ParsedDocument


def route_for(fmt: str, *, text_coverage: float | None = None) -> str:
    """纯函数：给定格式与（PDF 的）文字覆盖率，判定走哪一路。

    拆成纯函数是为了单测——路由判定不依赖真实文件解析。
    """
    if fmt in OFFICE_FORMATS:
        return "office"
    if fmt in ("markdown", "text"):
        return "office"  # 归一化：md/txt 也算 office 原生直取这一类
    if fmt == "pdf":
        if text_coverage is None:
            raise ValueError("PDF 需要文字覆盖率才能路由")
        high = text_coverage >= 0.90
        low = text_coverage < 0.40
        if high:
            return "native"
        if low:
            return "scanned"
        return "pipeline"
    raise ValueError(f"无法为格式 {fmt} 判定路由")


def _pdf_text_coverage(path: Path, sample_pages: int) -> float:
    """估算 PDF 文字覆盖率：抽样页抽出的非空字符数 / 估算容量。

    用抽样（config/parse.yaml §probe.sample_pages）避免长文档全量遍历。
    返回 0~1 的覆盖率；无法打开 PDF 时返回 0（视为扫描件，交给兜底）。
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        return 0.0

    try:
        doc = fitz.open(str(path))
    except Exception:
        return 0.0

    if doc.page_count == 0:
        doc.close()
        return 0.0

    pages = list(range(doc.page_count))[: max(1, sample_pages)]
    total_chars = 0.0
    total_est = 0.0
    for pno in pages:
        page = doc[pno]
        text = page.get_text("text")
        total_chars += len(text.strip())
        # 估算容量：页面面积 × 经验字符密度（约每平方英寸 60 字符的保守估计）
        rect = page.rect
        area_in2 = (rect.width / 72.0) * (rect.height / 72.0)
        total_est += area_in2 * 60.0
    doc.close()
    if total_est <= 0:
        return 0.0
    return min(1.0, total_chars / total_est)


class Router:
    """解析路由器：持有配置与引擎实例，把文件解析成 IR。

    用 Router 而非散落函数，是为了让"按页路由 + 统一 IR"成为一个可注入、
    可替换的组件（chunker 和 pipeline 只依赖它）。
    """

    def __init__(self, config: ParseConfig | None = None):
        self.config = config or ParseConfig.load()
        self._engines: dict[str, ParseEngine] = {
            "pymupdf": PyMuPDFEngine(),
            "docling": DoclingEngine(),
            "mineru": MinerUEngine(),
        }

    def parse(self, path: Path | str, doc_id: str) -> ParsedDocument:
        p = Path(path)
        fmt = detect_format(p)

        # md/txt：直接原生直取，不需要 Docling 的版面模型
        if fmt in ("markdown", "text"):
            return self._parse_text(p, doc_id, fmt)

        if fmt in OFFICE_FORMATS:
            return self._engines["docling"].parse(p, doc_id)

        if fmt == "pdf":
            return self._parse_pdf(p, doc_id)

        raise EngineError(f"无法处理的格式：{fmt}")

    def _parse_pdf(self, p: Path, doc_id: str) -> ParsedDocument:
        cov = _pdf_text_coverage(p, self.config.probe.sample_pages)
        route = route_for("pdf", text_coverage=cov)
        if route == "native":
            parsed = self._engines["pymupdf"].parse(p, doc_id)
            parsed.route = "native"
            parsed.metadata["text_coverage"] = cov
            return parsed
        if route == "pipeline":
            parsed = self._engines["docling"].parse(p, doc_id)
            parsed.route = "pipeline"
            parsed.metadata["text_coverage"] = cov
            return parsed
        # scanned → MinerU（M2）。M1 记录 route 并标记，交由 pipeline 进复核队列。
        parsed = ParsedDocument(
            doc_id=doc_id,
            source_format="pdf",
            engine="mineru",
            route="scanned",
            has_scanned_pages=True,
            metadata={"text_coverage": cov, "pending_rescue": True},
        )
        return parsed

    def _parse_text(self, p: Path, doc_id: str, fmt: str) -> ParsedDocument:
        """md/txt 原生直取：按段落/标题拆块，无需任何解析器依赖。"""
        raw = p.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        parsed = ParsedDocument(
            doc_id=doc_id, source_format=fmt, engine="native-text", route="office", page_count=1
        )
        idx = 0
        buf: list[str] = []
        cur_type = "paragraph"
        cur_level: int | None = None

        def flush() -> None:
            nonlocal buf, cur_type, cur_level, idx
            if not buf:
                return
            content = "\n".join(buf).strip()
            if content:
                parsed.with_block(
                    Block(
                        id=f"{doc_id}-t{idx}",
                        doc_id=doc_id,
                        type=cur_type,  # type: ignore[arg-type]
                        content=content,
                        page_no=1,
                        bbox=(0.0, 0.0, 0.0, 0.0),
                        level=cur_level,
                        order_index=idx,
                    )
                )
                idx += 1
            buf = []
            cur_type = "paragraph"
            cur_level = None

        for line in lines:
            stripped = line.strip()
            if not stripped:
                flush()
                continue
            if stripped.startswith("#"):
                flush()
                level = len(stripped) - len(stripped.lstrip("#"))
                cur_type = "heading"
                cur_level = level
                buf = [stripped.lstrip("#").strip()]
                flush()
                continue
            if stripped.startswith(("- ", "* ", "+ ")) or (len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".、)"):
                if cur_type != "list":
                    flush()
                    cur_type = "list"
                buf.append(stripped)
                continue
            # 普通正文
            if cur_type in ("list",):
                flush()
            buf.append(stripped)
        flush()
        return parsed.reindex()


__all__ = ["Router", "route_for"]
