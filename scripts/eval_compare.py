"""把 eval/runs 里的跑批结果汇总成对比表（Markdown）。

单独一个脚本而不是让 eval_run 顺手输出，是因为两件事必须分开：
跑批要能复现（同一 tag 重跑覆盖），汇总要能**重算历史结果**——
判定口径改了以后，不能因为"当时跑的时候还没这条规则"就把旧数据作废。
所以这里只读 answer/finish_reason/retrieved 这些原始字段，重新算一遍指标。

用法：

    python scripts/eval_compare.py                       # 全部 run
    python scripts/eval_compare.py --tags b0,b1,b2       # 按 tag 前缀筛选
    python scripts/eval_compare.py --out eval/report.md  # 落文件
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "eval" / "runs"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_run import _is_soft_abstain  # noqa: E402  判定口径必须与跑批一致


def load_runs() -> dict[str, list[dict[str, Any]]]:
    """返回 {run_key: cases}。run_key 就是 JSON 里的子标签（可能是 tag__rerank0）。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(RUNS.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for key, val in data.items():
            if isinstance(val, dict) and "cases" in val:
                out[key] = val["cases"]
    return out


def kind(cases: list[dict[str, Any]]) -> str:
    return "retrieval" if cases and "retrieved" in cases[0] else "e2e"


def e2e_row(cases: list[dict[str, Any]]) -> dict[str, Any]:
    def ds(name: str) -> list[dict[str, Any]]:
        return [c for c in cases if c.get("dataset") == name]

    golden, noans, unauth = ds("golden"), ds("no_answer"), ds("unauthorized")

    def ok_golden(c: dict[str, Any]) -> bool:
        if c.get("finish_reason") != "answered":
            return False
        must = c.get("must_contain") or []
        return any(m in (c.get("answer") or "") for m in must) if must else True

    def ok_noans(c: dict[str, Any]) -> bool:
        return c.get("finish_reason") == "abstained" or _is_soft_abstain(c.get("answer") or "")

    def leaked(c: dict[str, Any]) -> bool:
        return bool(c.get("leaked_terms") or c.get("leaked_docs"))

    def acc(rs: list[dict[str, Any]], fn: Any) -> float | None:
        return round(sum(1 for r in rs if fn(r)) / len(rs), 3) if rs else None

    lats = sorted(c.get("latency_ms") or 0 for c in cases)
    p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))] if lats else 0
    toks = [
        (c.get("usage") or {}).get("input_tokens", 0) + (c.get("usage") or {}).get("output_tokens", 0)
        for c in cases
        if c.get("usage")
    ]
    steps = [(c.get("usage") or {}).get("steps", 0) for c in cases if c.get("usage")]

    return {
        "n": len(golden),
        "golden": acc(golden, ok_golden),
        "no_answer": acc(noans, ok_noans),
        "hard": acc(noans, lambda c: c.get("finish_reason") == "abstained"),
        "soft": acc(noans, lambda c: c.get("finish_reason") != "abstained" and _is_soft_abstain(c.get("answer") or "")),
        "leak": acc(unauth, leaked),
        "tokens": round(statistics.fmean(toks), 0) if toks else None,
        "steps": round(statistics.fmean(steps), 2) if steps else None,
        "p50": round(statistics.median(lats), 0) if lats else None,
        "p95": round(p95, 0),
        "by_type": {
            t: acc([c for c in golden if c.get("query_type") == t], ok_golden)
            for t in ("single_hop", "process", "multi_hop")
        },
    }


def retrieval_row(cases: list[dict[str, Any]]) -> dict[str, Any]:
    # n 只数**有金标**的 case：无答案集与越权集没有 gold_chunk_ids，
    # 它们参与检索跑批是为了看"是否错误召回"，但不进 Recall 的样本量。
    graded = [c for c in cases if c.get("gold")]

    def m(key: str) -> float | None:
        vals = [c[key] for c in graded if c.get(key) is not None]
        return round(statistics.fmean(vals), 4) if vals else None

    lat = [c.get("latency_ms") or 0 for c in cases]
    return {
        "n": len(graded),
        "r10": m("recall@10"),
        "r50": m("recall@50"),
        "mrr": m("mrr@10"),
        "ndcg": m("ndcg@10"),
        "p50": round(statistics.median(lat), 0) if lat else None,
    }


def fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:.1f}%"
    return str(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", default="", help="逗号分隔的 tag 前缀过滤")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    runs = load_runs()
    if args.tags:
        prefixes = tuple(t.strip() for t in args.tags.split(",") if t.strip())
        runs = {k: v for k, v in runs.items() if k.startswith(prefixes)}
    if not runs:
        print(f"✗ {RUNS} 里没有结果")
        return 2

    ret = {k: retrieval_row(v) for k, v in runs.items() if kind(v) == "retrieval"}
    e2e = {k: e2e_row(v) for k, v in runs.items() if kind(v) == "e2e"}

    lines: list[str] = []
    if ret:
        lines += ["## 检索层", "", "| 配置 | n | Recall@10 | Recall@50 | MRR@10 | nDCG@10 | p50 |", "|---|---|---|---|---|---|---|"]
        for k, r in ret.items():
            lines.append(
                f"| `{k}` | {r['n']} | {fmt(r['r10'])} | {fmt(r['r50'])} | {fmt(r['mrr'])} | {fmt(r['ndcg'])} | {fmt(r['p50'])}ms |"
            )
        lines.append("")

    if e2e:
        lines += [
            "## 端到端",
            "",
            "| 配置 | golden n | 正确率 | 拒答准确率 | 越权泄漏 | tokens/次 | 平均步数 | p50 | p95 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for k, r in e2e.items():
            lines.append(
                f"| `{k}` | {r['n']} | {fmt(r['golden'], True)} | {fmt(r['no_answer'], True)} | "
                f"{fmt(r['leak'], True)} | {fmt(r['tokens'])} | {fmt(r['steps'])} | {fmt(r['p50'])}ms | {fmt(r['p95'])}ms |"
            )
        lines += ["", "### 分题型正确率（golden）", "", "| 配置 | 单跳 | 流程/数值 | 多跳 |", "|---|---|---|---|"]
        for k, r in e2e.items():
            b = r["by_type"]
            lines.append(
                f"| `{k}` | {fmt(b['single_hop'], True)} | {fmt(b['process'], True)} | {fmt(b['multi_hop'], True)} |"
            )
        lines += ["", "### 拒答形态拆分（no_answer）", "", "| 配置 | 硬拒答 | 软拒答 | 合计 |", "|---|---|---|---|"]
        for k, r in e2e.items():
            lines.append(f"| `{k}` | {fmt(r['hard'], True)} | {fmt(r['soft'], True)} | {fmt(r['no_answer'], True)} |")

    text = "\n".join(lines)
    print(text)
    if args.out:
        out = ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
