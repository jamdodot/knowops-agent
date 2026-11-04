"""评测跑批：检索层指标 + 端到端对比。

## 为什么检索层和端到端分开跑

检索层（`--mode retrieval`）不经图、不调模型，所以 **无 rerank 对照必须在这一层做**——
端到端掺了生成噪声，rerank 带来的 3~5 个点会被 prompt 波动盖掉，看不出因果。
端到端（`--mode e2e`）才回答"这套链路最终答对没有"。

## 三种口径不能混

| 数据集 | 判对的依据 | 为什么这样定 |
|---|---|---|
| golden | 答案含 `must_contain` 任一 且 finish=answered | 制度题的关键是数值/主体落地，不是文风 |
| no_answer | finish=abstained | 该拒不拒比答错更危险（编造制度条款） |
| unauthorized | 不出现 `forbidden_terms` 且引用里没有 `forbidden_doc_ids` | **看内容泄漏，不看有没有返回** |

越权集必须带 `control`（同题换有权主体），否则「泄漏率 0」可能只是「检索坏了」。

## 用法

    python scripts/eval_run.py --mode retrieval --rerank 0 1
    python scripts/eval_run.py --mode e2e --graph graph_pipeline --tag b0-pipeline
    python scripts/eval_run.py --mode e2e --graph graph      --tag b2-agentic

结果落 `eval/runs/<tag>.json`；同 tag 重跑会覆盖（跑批结果要能重放，不能靠追加）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "agent-service"
EVAL_DIR = ROOT / "eval"
DATASETS = EVAL_DIR / "datasets"

# 主体定义：与 scripts/demo_knowledge.py 对齐。
# dept_paths 是**祖先链**（Java 侧 ACL 服务算好传入），不是单个部门。
SUBJECTS: dict[str, dict[str, Any]] = {
    "employee": {"user_id": "u_1001", "dept_paths": ["D10", "D10.3"], "roles": ["employee"], "groups": [], "approval_doc_ids": []},
    "payroll": {"user_id": "u_9001", "dept_paths": ["D10", "D10.9"], "roles": ["payroll"], "groups": [], "approval_doc_ids": []},
    "outsider": {"user_id": "u_7000", "dept_paths": ["D20"], "roles": ["employee"], "groups": [], "approval_doc_ids": []},
    "auditor": {"user_id": "u_8000", "dept_paths": ["D10"], "roles": ["employee"], "groups": [], "approval_doc_ids": ["doc_902"]},
}

# 归因分类（docs/03 §5.1）。只覆盖能自动判的 5 类，其余留人工。
ATTR = {
    "E3": "召回未命中",
    "E4": "排序失当",
    "E6": "生成幻觉",
    "E9": "越权泄漏",
    "E10": "拒答不当",
}


# --------------------------------------------------------------------- 数据集
def load_dataset(name: str, root: Path = DATASETS) -> list[dict[str, Any]]:
    files = sorted(root.glob(f"{name}*.jsonl"))
    cases: list[dict[str, Any]] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


# --------------------------------------------------------------------- 指标
def recall_at(gold: list[str], retrieved: list[str], k: int) -> float | None:
    """金标 chunk 被召回的比例。金标为空（无答案集）返回 None，不参与均值。"""
    if not gold:
        return None
    return len(set(gold) & set(retrieved[:k])) / len(gold)


def mrr_at(gold: list[str], retrieved: list[str], k: int) -> float | None:
    if not gold:
        return None
    for rank, cid in enumerate(retrieved[:k], start=1):
        if cid in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at(gold: list[str], retrieved: list[str], k: int) -> float | None:
    """二值增益 nDCG。多跳题金标 2 条时，两条都靠前才算高分。"""
    if not gold:
        return None
    gset = set(gold)
    dcg = sum(1.0 / math.log2(i + 1) for i, cid in enumerate(retrieved[:k], start=1) if cid in gset)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gset), k) + 1))
    return dcg / ideal if ideal else 0.0


def _mean(xs: list[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(statistics.fmean(vals), 4) if vals else None


# --------------------------------------------------------------------- 检索层
def run_retrieval(cases: list[dict[str, Any]], *, use_rerank: bool, top_k: int = 50) -> dict[str, Any]:
    from retrieval import build_retriever

    retriever = build_retriever()
    if type(retriever).__name__ != "PgRetriever":
        raise SystemExit("✗ 检索后端不是 PgRetriever：先 python scripts/kw.py up 并确认 Docker 在跑")

    rows: list[dict[str, Any]] = []
    for case in cases:
        subj = SUBJECTS[case["subject"]]
        # 评测要 Recall@50，所以这里必须真取 50；hybrid_retrieve 的 recall_n 默认也是 50
        t0 = time.perf_counter()
        evs = retriever.hybrid_retrieve(case["query"], _Ns(subj), top_k=top_k, use_rerank=use_rerank)
        elapsed = (time.perf_counter() - t0) * 1000
        ids = [e.chunk_id for e in evs]
        gold = case.get("gold_chunk_ids") or []
        row = {
            "id": case["id"],
            "query": case["query"],
            "query_type": case.get("query_type"),
            "dataset": case.get("dataset"),
            "gold": gold,
            "retrieved": ids[:10],
            "n_retrieved": len(ids),
            "recall@5": recall_at(gold, ids, 5),
            "recall@10": recall_at(gold, ids, 10),
            "recall@50": recall_at(gold, ids, 50),
            "mrr@10": mrr_at(gold, ids, 10),
            "ndcg@10": ndcg_at(gold, ids, 10),
            "latency_ms": round(elapsed, 1),
            "notes": dict(getattr(retriever, "last_backend_notes", {}) or {}),
        }
        # 自动归因：金标完全没进候选 → E3；进了候选但没进 top10 → E4
        if gold and row["recall@10"] != 1.0:
            row["attribute"] = "E3" if row["recall@50"] == 0 else "E4"
        rows.append(row)

    return {"cases": rows, "summary": _summarize_retrieval(rows)}


def _summarize_retrieval(rows: list[dict[str, Any]]) -> dict[str, Any]:
    graded = [r for r in rows if r["gold"]]
    by_type: dict[str, dict[str, Any]] = {}
    for r in graded:
        by_type.setdefault(r["query_type"] or "?", []).append(r)

    def agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(rs),
            "recall@5": _mean([r["recall@5"] for r in rs]),
            "recall@10": _mean([r["recall@10"] for r in rs]),
            "recall@50": _mean([r["recall@50"] for r in rs]),
            "mrr@10": _mean([r["mrr@10"] for r in rs]),
            "ndcg@10": _mean([r["ndcg@10"] for r in rs]),
            "p50_ms": _median([r["latency_ms"] for r in rs]),
        }

    attrs: dict[str, int] = {}
    for r in rows:
        if r.get("attribute"):
            attrs[r["attribute"]] = attrs.get(r["attribute"], 0) + 1

    return {
        "overall": agg(graded),
        "by_query_type": {k: agg(v) for k, v in sorted(by_type.items())},
        "attribute_counts": attrs,
        "rerank_status": _count_status(rows),
    }


def _count_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    """统计每次检索里 rerank 到底生效没有。降级静默是评测里最难查的一类问题。"""
    out: dict[str, int] = {}
    for r in rows:
        st = str((r.get("notes") or {}).get("rerank") or "?")
        st = st.split(":")[0] if st.startswith("skipped") else st
        out[st] = out.get(st, 0) + 1
    return out


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 1) if xs else 0.0


# --------------------------------------------------------------------- 端到端
def run_e2e(cases: list[dict[str, Any]]) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from api.app_knowledge import create_app
    from runtime.config import Settings

    app = create_app(Settings.from_env())
    client = TestClient(app)
    rows: list[dict[str, Any]] = []

    for case in cases:
        subj = SUBJECTS[case["subject"]]
        body = {
            "query": case["query"],
            "subject_ctx": {
                "user_id": subj["user_id"],
                "account_status": "active",
                "dept_paths": subj["dept_paths"],
                "roles": subj["roles"],
                "groups": subj["groups"],
                "approval_doc_ids": subj["approval_doc_ids"],
                "subject_epoch": 1,
            },
            # 每个 case 独立 session：共用会让 checkpoint 串味，多轮改写行为漂移
            "session_id": f"eval-{case['id']}",
            "turn_seq": 1,
        }
        t0 = time.perf_counter()
        resp = client.post(
            "/v1/knowledge/answers", json=body, headers={"X-Contract-Version": "1.0.0"}
        )
        elapsed = (time.perf_counter() - t0) * 1000
        answer, cites, done, meta = _parse_sse(resp.text)
        rows.append(_judge(case, answer, cites, done, meta, elapsed))

    return {"cases": rows, "summary": _summarize_e2e(rows)}


def _parse_sse(text: str) -> tuple[str, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    answer: list[str] = []
    cites: list[dict[str, Any]] = []
    done: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        event, data = "", {}
        for line in frame.strip().split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    data = {}
        if event == "delta":
            answer.append(data.get("text", ""))
        elif event == "citation":
            cites = data.get("citations") or []
        elif event == "meta":
            meta = data
        elif event == "done":
            done = data
    return "".join(answer), cites, done, meta


def _judge(
    case: dict[str, Any],
    answer: str,
    cites: list[dict[str, Any]],
    done: dict[str, Any],
    meta: dict[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    ds = case.get("dataset")
    finish = done.get("finish_reason") or "?"
    cited_docs = {c.get("doc_id") for c in cites}
    row: dict[str, Any] = {
        "id": case["id"],
        "query": case["query"],
        "query_type": case.get("query_type"),
        "dataset": ds,
        "subject": case["subject"],
        "answer": answer,
        "n_citations": len(cites),
        "cited_docs": sorted(d for d in cited_docs if d),
        "finish_reason": finish,
        "latency_ms": round(elapsed, 1),
        "usage": done.get("usage") or {},
        "graph": meta.get("graph"),
        "degraded": done.get("degraded") or [],
    }

    if ds == "golden":
        must = case.get("must_contain") or []
        hit = any(m in answer for m in must) if must else None
        row["content_ok"] = hit
        # 答了但没命中关键词 → 有证据却答偏，算 E6；直接拒答 → E10
        row["correct"] = bool(finish == "answered" and hit)
        if not row["correct"]:
            row["attribute"] = "E10" if finish != "answered" else "E6"
    elif ds == "no_answer":
        # 该拒不拒 = 编造，比答错严重，单独归因。
        # 但"拒答"有两种形态，混算会冤枉模型：模型正确地说「现有资料未覆盖」之后
        # 顺手复述了相邻条款（有引用、finish=answered），这是**软拒答**，不是编造。
        # 只认 finish=abstained 会把这类判成 E10（2026-11-04 实测 n-006/n-008 即此情况）。
        soft = _is_soft_abstain(answer)
        row["soft_abstain"] = soft
        row["correct"] = finish == "abstained" or soft
        if not row["correct"]:
            row["attribute"] = "E10"
    elif ds == "unauthorized":
        forbidden = case.get("forbidden_terms") or []
        forbidden_docs = set(case.get("forbidden_doc_ids") or [])
        leaked_terms = [t for t in forbidden if t in answer]
        leaked_docs = sorted(forbidden_docs & cited_docs)
        row["leaked_terms"] = leaked_terms
        row["leaked_docs"] = leaked_docs
        row["leaked"] = bool(leaked_terms or leaked_docs)
        row["correct"] = not row["leaked"]
        if row["leaked"]:
            row["attribute"] = "E9"
    return row


def _summarize_e2e(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def by_ds(ds: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["dataset"] == ds]

    golden = by_ds("golden")
    noans = by_ds("no_answer")
    unauth = by_ds("unauthorized")

    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in golden:
        by_type.setdefault(r["query_type"] or "?", []).append(r)

    attrs: dict[str, int] = {}
    for r in rows:
        if r.get("attribute"):
            attrs[r["attribute"]] = attrs.get(r["attribute"], 0) + 1

    lat = sorted(r["latency_ms"] for r in rows)
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0.0
    tokens = sum(
        int((r["usage"] or {}).get("input_tokens", 0)) + int((r["usage"] or {}).get("output_tokens", 0))
        for r in rows
        if r.get("usage")
    )
    steps = [int((r["usage"] or {}).get("steps", 1)) for r in rows if r.get("usage")]

    def acc(rs: list[dict[str, Any]]) -> float | None:
        return round(sum(1 for r in rs if r.get("correct")) / len(rs), 4) if rs else None

    return {
        "golden": {
            "n": len(golden),
            "accuracy": acc(golden),
            "answered_rate": _rate(golden, lambda r: r["finish_reason"] == "answered"),
            "by_query_type": {k: {"n": len(v), "accuracy": acc(v)} for k, v in sorted(by_type.items())},
        },
        "no_answer": {
            "n": len(noans),
            "reject_accuracy": acc(noans),
            # 拆开报：硬拒答（finish=abstained）与软拒答（说了未覆盖但 finish=answered）。
            # 两者**互斥**：软拒答的定义里排除了 abstained，否则加起来会超过 1
            # （实测 b0 出现过 0.625+0.5=1.125，就是重叠算了一次）。
            "hard_abstain_rate": _rate(noans, lambda r: r["finish_reason"] == "abstained"),
            "soft_abstain_rate": _rate(
                noans, lambda r: r["finish_reason"] != "abstained" and bool(r.get("soft_abstain"))
            ),
        },
        "unauthorized": {
            "n": len(unauth),
            "leak_rate": _rate(unauth, lambda r: bool(r.get("leaked"))),
            "leaks": [r["id"] for r in unauth if r.get("leaked")],
        },
        "latency": {"p50_ms": _median([r["latency_ms"] for r in rows]), "p95_ms": round(p95, 1)},
        "cost": {
            "total_tokens": tokens,
            "avg_tokens_per_case": round(tokens / len(rows), 1) if rows else 0,
            "avg_steps": round(statistics.fmean(steps), 2) if steps else None,
        },
        "attribute_counts": attrs,
        "degraded_cases": [r["id"] for r in rows if r.get("degraded")],
    }


def _rate(rows: list[dict[str, Any]], pred: Any) -> float | None:
    return round(sum(1 for r in rows if pred(r)) / len(rows), 4) if rows else None


class _Ns:
    """把 dict 包成节点期望的属性访问形式（检索后端只读属性，不认 dict）。"""

    def __init__(self, d: dict[str, Any]) -> None:
        self.__dict__.update(d)


# 软拒答的判据：模型明确说了"没有规定"，且**没有继续往下编**。
# 只看前半段：真实的编造通常是"未覆盖。不过根据一般做法……"这种转折后长篇展开，
# 那种不算软拒答，必须仍判 E10。所以两个条件都要满足：标志词在前半段 + 全文不长。
_SOFT_ABSTAIN = (
    "未覆盖", "未规定", "没有规定", "未提及", "无相关", "未找到",
    "无法回答", "未明确", "制度中未", "资料中未", "没有相关",
)
_SOFT_MAX_LEN = 260


def _is_soft_abstain(answer: str) -> bool:
    text = (answer or "").strip()
    if not text or len(text) > _SOFT_MAX_LEN:
        return False
    # 看前 60 字而不是"前一半"：拒答句可能极短（"现有资料未覆盖。" 只有 8 字），
    # 按一半切会切到"现有资料"，标志词刚好落在切口外——实测 n-006 就是这么漏判的。
    head = text[:60]
    return any(k in head for k in _SOFT_ABSTAIN)


# --------------------------------------------------------------------- 输出
def print_retrieval(tag: str, res: dict[str, Any]) -> None:
    s = res["summary"]
    o = s["overall"]
    print(f"\n=== 检索层 [{tag}] ===")
    print(f"  样本 {o['n']} | Recall@5 {o['recall@5']} | Recall@10 {o['recall@10']} | "
          f"Recall@50 {o['recall@50']} | MRR@10 {o['mrr@10']} | nDCG@10 {o['ndcg@10']} | p50 {o['p50_ms']}ms")
    print("  分题型：")
    for k, v in s["by_query_type"].items():
        print(f"    {k:12} n={v['n']:3}  R@10={v['recall@10']}  MRR={v['mrr@10']}  nDCG={v['ndcg@10']}")
    print(f"  rerank 状态：{s['rerank_status']}")
    print(f"  归因：{s['attribute_counts'] or '（无失败）'}")
    misses = [c for c in res["cases"] if c["gold"] and c["recall@10"] != 1.0]
    if misses:
        print("  未满分 case：")
        for c in misses[:8]:
            print(f"    {c['id']} R@10={c['recall@10']} gold={c['gold']} got={c['retrieved'][:3]}")


def print_e2e(tag: str, res: dict[str, Any]) -> None:
    s = res["summary"]
    g, n, u = s["golden"], s["no_answer"], s["unauthorized"]
    print(f"\n=== 端到端 [{tag}] ===")
    print(f"  golden   n={g['n']:3} 正确率 {g['accuracy']}  作答率 {g['answered_rate']}")
    for k, v in g["by_query_type"].items():
        print(f"    {k:12} n={v['n']:3} 正确率 {v['accuracy']}")
    print(f"  no_answer n={n['n']:3} 拒答准确率 {n['reject_accuracy']} "
          f"(硬拒答 {n['hard_abstain_rate']} / 软拒答 {n['soft_abstain_rate']})")
    print(f"  unauthed  n={u['n']:3} 泄漏率 {u['leak_rate']}  泄漏 case={u['leaks']}")
    print(f"  延迟 p50={s['latency']['p50_ms']}ms p95={s['latency']['p95_ms']}ms | "
          f"tokens/次={s['cost']['avg_tokens_per_case']} | 平均步数={s['cost']['avg_steps']}")
    print(f"  归因：{s['attribute_counts'] or '（无失败）'}")
    fails = [c for c in res["cases"] if c.get("correct") is False]
    if fails:
        print("  失败 case：")
        for c in fails[:10]:
            print(f"    {c['id']} [{c['dataset']}/{c['query_type']}] finish={c['finish_reason']} "
                  f"归因={c.get('attribute')} answer={c['answer'][:40]!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="知识域评测跑批")
    ap.add_argument("--datasets", default="golden,no_answer,unauthorized")
    ap.add_argument("--mode", default="retrieval", choices=["retrieval", "e2e", "both"])
    ap.add_argument("--graph", default="graph", help="graph | graph_pipeline（仅 e2e 生效）")
    ap.add_argument("--rerank", default=["0", "1"], nargs="+", help="检索层跑哪些 rerank 档位")
    ap.add_argument("--tag", default="", help="输出文件名；留空则自动按时间戳")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    args = ap.parse_args()

    sys.path.insert(0, str(SERVICE))
    from runtime.config import load_dotenv

    load_dotenv()

    names = [n.strip() for n in args.datasets.split(",") if n.strip()]
    all_cases: list[dict[str, Any]] = []
    for n in names:
        got = load_dataset(n)
        if not got:
            print(f"✗ 数据集 {n} 为空或不存在（{DATASETS}）")
            return 2
        all_cases.extend(got)
    if args.limit:
        all_cases = all_cases[: args.limit]

    llm_mode = os.environ.get("KW_LLM_MODE", "mock")
    tag = args.tag or f"{args.mode}-{args.graph.replace('graph_', '')}-{datetime.now().strftime('%m%d%H%M')}"

    out: dict[str, Any] = {
        "tag": tag,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "config": {
            "datasets": names,
            "n_cases": len(all_cases),
            "graph": args.graph if args.mode != "retrieval" else None,
            "llm_mode": llm_mode,
            "rerank_model": os.environ.get("RERANK_MODEL") or "BAAI/bge-reranker-v2-m3",
            "dense_min_sim": os.environ.get("KW_DENSE_MIN_SIM", "0.55"),
            "recall_n": os.environ.get("KW_RECALL_N", "50"),
        },
    }

    if args.mode in ("retrieval", "both"):
        for flag in args.rerank:
            use = flag not in ("0", "false", "off")
            sub_tag = f"{tag}__rerank{'1' if use else '0'}"
            print(f"\n[检索层] rerank={'on' if use else 'off'} …")
            res = run_retrieval(all_cases, use_rerank=use)
            out[sub_tag] = res
            print_retrieval(sub_tag, res)

    if args.mode in ("e2e", "both"):
        if llm_mode == "mock":
            # mock 的 synthesize 不含 eid，必然被引用校验判 no_citation → 全部 abstained。
            # 拿它跑评测只会得到一堆假数据，所以直接拒绝。
            print("\n✗ KW_LLM_MODE=mock：mock 的合成结果不含引用，必然全量 abstain，不能用于评测。")
            print("  请用 KW_LLM_MODE=online 重跑（会真实调用模型，有 token 成本）。")
            return 3
        os.environ["KW_GRAPH_MODULE"] = args.graph
        print(f"\n[e2e] graph={args.graph} …")
        res = run_e2e(all_cases)
        out[tag] = res
        print_e2e(tag, res)

    RUNS = EVAL_DIR / "runs"
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{tag}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {path.relative_to(ROOT)}")
    return 0


def _git_sha() -> str:
    try:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
