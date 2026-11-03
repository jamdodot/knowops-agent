"""真实文档解析验证：造一份真实 PDF，跑解析→清洗→分块全链路。

用途：M1 解析验收的本地自检脚本（不依赖 Docker，不依赖网络）。
跑法：python scripts/verify_parse.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent / "agent-service"
sys.path.insert(0, str(SERVICE_ROOT))

from parse.chunker import ChunkConfig, SemanticChunker  # noqa: E402
from parse.pipeline import ParsePipeline  # noqa: E402


def _make_sample_pdf(out: Path) -> None:
    """用 pymupdf 造一份带文字层、标题、多页、中文的 PDF 样例。

    中文必须用内置 CJK 字体（fontname='china-s'），否则 insert_text 用默认
    helvetica 会抽出乱码点。真实中文 PDF（Word/WPS 导出）会嵌入 CJK 字体，
    不存在这个问题，这里只是样例生成需要显式指定。
    """
    import fitz

    doc = fitz.open()

    def add_page(text_lines: list[tuple[str, int]]) -> None:
        page = doc.new_page()
        y = 72
        for text, size in text_lines:
            page.insert_text((72, y), text, fontsize=size, fontname="china-s")
            y += size + 12

    add_page(
        [
            ("员工考勤管理制度", 20),
            ("第一章 总则", 15),
            ("第一条 为规范公司考勤管理，明确员工出勤、请假、加班等行为的统一标准，制定本制度。", 11),
            ("第二条 本制度适用于公司全体正式员工与实习生，外包人员参照执行。", 11),
            ("第三条 考勤数据以打卡记录为准，人工补卡须经部门负责人审批。", 11),
            ("第四条 员工应按时上下班，不得无故迟到、早退、旷工。", 11),
            ("第二章 工作时间", 15),
            ("第五条 标准工时上午九点至十二点，下午一点至六点，每周工作五天。", 11),
            ("第六条 因工作需要加班的，须提前在系统提交加班申请。", 11),
            ("第七条 加班时长以系统审批通过后的打卡区间计算。", 11),
        ]
    )
    add_page(
        [
            ("第三章 请假管理", 15),
            ("第八条 事假须提前一天申请，经直属上级审批后生效。", 11),
            ("第九条 病假须提供二级以上医院出具的证明方可计为病假。", 11),
            ("第十条 年假按员工司龄核算，具体天数见附表。", 11),
            ("第四章 附则", 15),
            ("第十一条 本制度自发布之日起施行，解释权归人力资源部。", 11),
            ("第十二条 此前与本制度冲突的规定同时废止。", 11),
        ]
    )

    doc.save(str(out))
    doc.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    pdf = tmp / "考勤制度.pdf"
    _make_sample_pdf(pdf)
    print(f"样例 PDF 已生成：{pdf} ({pdf.stat().st_size} bytes)\n")

    pipeline = ParsePipeline()
    doc, report = pipeline.parse(pdf, "doc-考勤-001")

    print("=== parse_report ===")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print()

    print("=== 解析出的 IR 块 ===")
    for b in doc.blocks:
        print(f"  [{b.type:<10}] p{b.page_no}  {b.content[:40]}")
    print()

    chunker = SemanticChunker(ChunkConfig(child_size=100, parent_size=400))
    result = chunker.chunk(doc)
    print(f"=== 分块结果：{len(result.children)} 子块 / {len(result.parents)} 父块 ===")
    for c in result.children:
        print(f"  [c{c.ord} -> {c.parent_id}] {c.section_path or '(无标题)'} :: {c.content[:30]}")

    return 0 if (report.status == "ok" and result.children) else 1


if __name__ == "__main__":
    raise SystemExit(main())
