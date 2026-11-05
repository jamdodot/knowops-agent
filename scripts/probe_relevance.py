#!/usr/bin/env python
"""量「相关性下限」该定在哪：把三套集的首名分数收一遍，再画阈值曲线。

为什么不能拍脑袋定一个 0.3：阈值的代价是双向的。
定高了，golden 集体拒答（比泄漏更伤，用户直接觉得系统坏了）；
定低了，越权题继续用模型常识作答（`u-105` 就是这条路）。

所以要先看分布，再看曲线，最后才决定要不要动。
输出 `eval/runs/relevance-dist.json`，可直接和不同模型/语料批对照。

用法：
    python scripts/probe_relevance.py
    python scripts/probe_relevance.py --no-rerank     # 关掉重排看分布差多少
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-service"))

DATASETS = (
    ("golden_v2.jsonl", "golden"),
    ("no_answer_v1.jsonl", "no_answer"),
    ("unauthorized_v2.jsonl", "unauthorized"),
)
OUT = ROOT / "eval" / "runs" / "relevance-dist.json"

#: 评测主体。必须和 `scripts/eval_run.py` 的 SUBJECTS 一致，否则分数没有可比性。
SUBJECTS: dict[str, dict[str, Any]] = {
    "employee": {"user_id": "u_2048", "dept_paths": ["D10", "D10.3"], "roles": ["employee"]},
    "payroll": {"user_id": "u_3100", "dept_paths": ["D10", "D10.9"], "roles": ["employee", "payroll"]},
    "outsider": {"user_id": "u_7000", "dept_paths": ["D20"], "roles": ["employee"]},
    "branch": {"user_id": "u_7100", "dept_paths": ["D20", "D20.1"], "roles": ["employee"]},
}


def collect(use_rerank: bool, limit: int = 0) -> list[dict[str, Any]]:
    from api.schemas import SubjectContext
    from retrieval.dsn import pg_dsn
    from retrieval.pg_retriever import PgRetriever

    retriever = PgRetriever(dsn=pg_dsn())
    rows: list[dict[str, Any]] = []
    for fname, ds in DATASETS:
        path = ROOT / "eval" / "datasets" / fname
        if not path.exists():
            print(f"跳过（不存在）：{fname}")
            continue
        cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for case in cases[: limit or len(cases)]:
            base = SUBJECTS.get(case.get("subject") or "employee", SUBJECTS["employee"])
            subject = SubjectContext(
                account_status="active", groups=[], approval_doc_ids=[], subject_epoch=1, **base
            )
            ev = retriever.hybrid_retrieve(case["query"], subject, top_k=5, use_rerank=use_rerank)
            rows.append(
                {
                    "id": case["id"],
                    "ds": ds,
                    "scores": [round(float(getattr(e, "score", 0) or 0), 4) for e in ev],
                    "docs": [e.doc_id for e in ev],
                }
            )
            print(f"  {case['id']:8s} {rows[-1]['scores'][:2]}")
    return rows


def _top1(row: dict[str, Any]) -> float:
    return max(row["scores"]) if row["scores"] else 0.0


def report(rows: list[dict[str, Any]]) -> None:
    for ds in ("golden", "no_answer", "unauthorized"):
        sub = [r for r in rows if r["ds"] == ds]
        if not sub:
            continue
        scores = sorted((_top1(r) for r in sub), reverse=True)
        print(
            f"  {ds:13s} n={len(sub):3d} min={scores[-1]:.3f} "
            f"p10={scores[min(len(scores) - 1, int(len(scores) * 0.9))]:.3f} "
            f"中位={scores[len(scores) // 2]:.3f} max={scores[0]:.3f}"
        )

    print("\n阈值  golden 被误拒  no_answer 被拒  unauthorized 被拒")
    for t in (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5):
        cells = []
        for ds in ("golden", "no_answer", "unauthorized"):
            sub = [r for r in rows if r["ds"] == ds]
            rate = sum(1 for r in sub if _top1(r) < t) / len(sub) if sub else 0.0
            cells.append(f"{rate:6.1%}")
        print(f"  {t:.2f}  {'  '.join(cells)}")

    low = sorted((r for r in rows if r["ds"] == "golden"), key=_top1)[:8]
    print("\n最可能被误伤的 golden（分数最低的 8 条）：")
    for row in low:
        print(f"  {row['id']} {_top1(row):.4f} {row['docs'][0] if row['docs'] else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-rerank", action="store_true", help="关掉重排（用于对比分布）")
    ap.add_argument("--limit", type=int, default=0, help="每套集只跑前 N 条（冒烟用）")
    args = ap.parse_args()

    from runtime.config import load_dotenv

    load_dotenv()
    rows = collect(use_rerank=not args.no_rerank, limit=args.limit)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 首名分数分布（rerank={'off' if args.no_rerank else 'on'}） ===")
    report(rows)
    print(f"\n结果已写入 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
