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

from eval_run import _is_soft_abstain, _norm, leaked_terms_in  # noqa: E402  判定口径必须与跑批一致

DATASETS = ROOT / "eval" / "datasets"


def load_labels() -> dict[str, dict[str, Any]]:
    """按 case id 读**当前**数据集标注。

    为什么不能直接用跑批 JSON 里存的 must_contain / forbidden_terms：
    那是跑批当时的快照。标注改过之后（扩语料会让"解除劳动合同"这种词失去区分度），
    快照重算出来的分数是旧的——这正是 `eval_compare` 说"能重算历史结果"却做不到的原因。
    以数据集为准，跑批结果只当 answer/finish_reason/retrieved 的原始记录。
    """
    labels: dict[str, dict[str, Any]] = {}
    for f in sorted(DATASETS.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                c = json.loads(line)
                labels[c["id"]] = c
    return labels


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
    labels = load_labels()

    def lab(c: dict[str, Any]) -> dict[str, Any]:
        return labels.get(c.get("id") or "", {})

    def ds(name: str) -> list[dict[str, Any]]:
        return [c for c in cases if c.get("dataset") == name]

    golden, noans, unauth = ds("golden"), ds("no_answer"), ds("unauthorized")

    def ok_golden(c: dict[str, Any]) -> bool:
        # degraded = 已交付但被预算截断（A5 之后的形态），内容对就算对，
        # 截断比例单独统计，不混进 answered
        if c.get("finish_reason") not in ("answered", "degraded"):
            return False
        must = lab(c).get("must_contain") or c.get("must_contain") or []
        return any(_norm(m) in _norm(c.get("answer") or "") for m in must) if must else True

    def ok_content(c: dict[str, Any]) -> bool:
        """只看答案内容对不对，**不看 finish_reason**。

        为什么要单独拆一列：agentic 在 deadline 被截断时，正文其实已经带引用发完了，
        但 `_finish_reason` 一律落成 abstained。此时 ok_golden=False 而 ok_content=True——
        两个数拉开的差距就是「协议层丢掉的正确答案」，不是模型答错了。
        2026-11-04 实测 82 条里差 22 条（98.8% vs 76.8%），改任一档配置都看不出来。
        """
        must = lab(c).get("must_contain") or c.get("must_contain") or []
        return any(_norm(m) in _norm(c.get("answer") or "") for m in must) if must else True

    def ok_noans(c: dict[str, Any]) -> bool:
        return c.get("finish_reason") == "abstained" or _is_soft_abstain(c.get("answer") or "")

    def leaked(c: dict[str, Any]) -> bool:
        terms = lab(c).get("forbidden_terms") or c.get("forbidden_terms") or []
        if leaked_terms_in(c.get("answer") or "", terms):
            return True
        forb = set(lab(c).get("forbidden_doc_ids") or c.get("forbidden_doc_ids") or [])
        return bool(forb & set(c.get("cited_docs") or []))

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
        "content": acc(golden, ok_content),
        "no_answer": acc(noans, ok_noans),
        "hard": acc(noans, lambda c: c.get("finish_reason") == "abstained"),
        "soft": acc(noans, lambda c: c.get("finish_reason") != "abstained" and _is_soft_abstain(c.get("answer") or "")),
        "leak": acc(unauth, leaked),
        # 已交付但被预算截断的比例。这个数 > 0 说明有答案是在收尾时被砍断的，
        # 正确率再高也要看这一列——它代表"用户拿到的答案是残的"。
        "truncated": acc(golden, lambda c: c.get("finish_reason") == "degraded"),
        "tokens": round(statistics.fmean(toks), 0) if toks else None,
        "steps": round(statistics.fmean(steps), 2) if steps else None,
        "p50": round(statistics.median(lats), 0) if lats else None,
        "p95": round(p95, 0),
        "by_type": {
            t: acc([c for c in golden if c.get("query_type") == t], ok_golden)
            for t in ("single_hop", "process", "multi_hop", "cross_doc")
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
            "| 配置 | golden n | 正确率 | 内容命中 | 截断率 | 拒答准确率 | 越权泄漏 | tokens/次 | 平均步数 | p50 | p95 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for k, r in e2e.items():
            lines.append(
                f"| `{k}` | {r['n']} | {fmt(r['golden'], True)} | {fmt(r.get('content'), True)} | "
                f"{fmt(r.get('truncated'), True)} | {fmt(r['no_answer'], True)} | "
                f"{fmt(r['leak'], True)} | {fmt(r['tokens'])} | {fmt(r['steps'])} | {fmt(r['p50'])}ms | {fmt(r['p95'])}ms |"
            )
        lines += ["", "> 正确率 = 协议 answered/degraded 且命中关键词；内容命中 = 只看答案命中关键词，不看 finish_reason。",
                  "> 两者拉开 = 答案已经发给用户了，却被协议判成拒答。",
                  "> 截断率 = finish_reason 为 degraded（答案已交付但被预算砍断）的占比。"]
        lines += ["", "### 分题型正确率（golden）", "", "| 配置 | 单跳 | 流程/数值 | 多跳 | 跨文档 |", "|---|---|---|---|---|"]
        for k, r in e2e.items():
            b = r["by_type"]
            lines.append(
                f"| `{k}` | {fmt(b['single_hop'], True)} | {fmt(b['process'], True)} "
                f"| {fmt(b['multi_hop'], True)} | {fmt(b.get('cross_doc'), True)} |"
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
