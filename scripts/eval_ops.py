"""排障域回放评测：Root Cause Top-1 / Top-3。

和知识域评测的差别（不只是数据集不同）：

- 知识域问"答案对不对"，排障域问"**有没有收敛到那个根因**"。所以判分不看正文措辞，
  只看 result.root_cause 是否命中该单的根因别名词表。
- 排障域必然经过一次 HITL 审批。评测里把它自动批掉（`approved`）——**这不是在测审批**，
  测的是"审批前的诊断结论对不对"。审批本身的行为由 test_hitl 覆盖。
- 失败要分两类：`escalated`（没收敛，是能力问题）和 `wrong_cause`（收敛到错的根因，
  是判断问题）。这两类修法完全不同，混成一个"错误率"就看不出该改哪里。

用法：
    python scripts/eval_ops.py --tag ops-v1
    python scripts/eval_ops.py --tag ops-v1 --limit 5     # 冒烟
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-service"))
sys.path.insert(0, str(ROOT / "scripts"))

DATASET = ROOT / "eval" / "datasets" / "replay_ops_v1.jsonl"
OUT_DIR = ROOT / "eval" / "runs"

_TASK_ID = re.compile(r"tsk_[0-9a-f]{6,}")
_DATA = re.compile(r"^data:\s*(\{.*\})\s*$", re.MULTILINE)


def load_cases(limit: int = 0) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def _frames(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in _DATA.finditer(text):
        try:
            out.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return out


def _hit(text: str, aliases: list[str]) -> bool:
    """根因别名命中。去空白比对——"不可抗力"与"不可 抗力"是同一件事。"""
    flat = re.sub(r"\s+", "", text or "")
    return any(re.sub(r"\s+", "", a) in flat for a in aliases)


def run(cases: list[dict[str, Any]]) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from api.app_fulfillment import create_app
    from eval_run import SUBJECTS
    from runtime.config import Settings

    app = create_app(Settings.from_env())
    client = TestClient(app)
    subject = SUBJECTS["employee"]
    headers = {"X-Contract-Version": "1.0.0"}
    rows: list[dict[str, Any]] = []

    for case in cases:
        body = {
            "symptom": case["symptom"],
            "subject_ctx": {
                "user_id": subject["user_id"],
                "account_status": "active",
                "dept_paths": subject["dept_paths"],
                "roles": subject["roles"],
                "groups": subject["groups"],
                "approval_doc_ids": subject["approval_doc_ids"],
                "subject_epoch": 1,
            },
            "session_id": f"ops-{case['id']}",
        }
        started = time.perf_counter()
        first = client.post("/v1/fulfillment/tasks", json=body, headers=headers)
        frames = _frames(first.text)
        task_id = _pick_task_id(first.text, frames)

        # 排障域必然停在 input_required：可能先要澄清（缺订单号），再要审批（动作）。
        # 评测里两种都自动满足——测的是**审批前诊断得对不对**，不是审批行为本身。
        # 最多循环 3 次：超过 3 次说明状态机在打转，那该暴露出来而不是掩盖。
        resumed = 0
        for _ in range(3):
            if not task_id:
                break
            snap = client.get(f"/v1/fulfillment/tasks/{task_id}", headers=headers)
            if snap.status_code != 200:
                break
            payload = snap.json()
            if payload.get("state") != "input_required":
                break
            pending = payload.get("pending") or {}
            resume_body: dict[str, Any] = {"task_id": task_id, "subject_ctx": body["subject_ctx"]}
            approval = pending.get("approval") or {}
            if approval.get("approval_id"):
                resume_body["approval"] = {"approval_id": approval["approval_id"], "decision": "approved"}
            resume_body["clarification"] = {"fields": {"order_id": case["order_id"]}}
            client.post(f"/v1/fulfillment/tasks/{task_id}/resume", json=resume_body, headers=headers)
            resumed += 1

        elapsed = (time.perf_counter() - started) * 1000
        final = client.get(f"/v1/fulfillment/tasks/{task_id}", headers=headers) if task_id else None
        payload = (final.json() if final is not None and final.status_code == 200 else {}) or {}
        result = payload.get("result") or {}
        root_cause = str(result.get("root_cause") or "")
        summary = str(result.get("summary") or "")
        state = str(payload.get("state") or "")

        hit1 = _hit(root_cause, case["aliases"])
        # Top-3 用证据条目近似：每条 evidence 对应一个被验证过的假设，
        # 取前 3 条看有没有命中。这比"前 3 个候选"更严——它要求候选确实被取证过。
        evidences = result.get("evidence") or []
        top3_text = " ".join(str(e.get("summary") or "") for e in evidences[:3])
        hit3 = hit1 or _hit(top3_text, case["aliases"])

        rows.append(
            {
                "id": case["id"],
                "order_id": case["order_id"],
                "expect": case["expect"],
                "root_cause": root_cause,
                "state": state,
                "resumed": resumed,
                "top1": hit1,
                "top3": hit3,
                "confidence": result.get("confidence"),
                "latency_ms": round(elapsed, 1),
                "summary": summary[:200],
                "attribute": "" if hit1 else ("escalated" if state != "completed" or not root_cause else "wrong_cause"),
            }
        )
        mark = "✓" if hit1 else ("~" if hit3 else "✗")
        print(f"  {mark} {case['id']} {case['expect']:18s} → {root_cause[:34] or '(空)'}")

    return {"cases": rows, "summary": summarize(rows)}


def _pick_task_id(text: str, frames: list[dict[str, Any]]) -> str:
    for frame in frames:
        for key in ("task_id", "taskId"):
            value = frame.get(key) or (frame.get("task") or {}).get(key)
            if value:
                return str(value)
    found = _TASK_ID.search(text)
    return found.group(0) if found else ""


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1
    by_cause: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_cause.setdefault(row["expect"], []).append(row)
    lat = sorted(r["latency_ms"] for r in rows)
    return {
        "n": len(rows),
        "top1": sum(1 for r in rows if r["top1"]) / n,
        "top3": sum(1 for r in rows if r["top3"]) / n,
        "escalated": sum(1 for r in rows if r["attribute"] == "escalated") / n,
        "wrong_cause": sum(1 for r in rows if r["attribute"] == "wrong_cause") / n,
        "resumed": sum(int(r["resumed"]) for r in rows),
        "p50_ms": lat[len(lat) // 2] if lat else 0,
        "p95_ms": lat[int(len(lat) * 0.95)] if lat else 0,
        "by_cause": {
            k: {
                "n": len(v),
                "top1": sum(1 for r in v if r["top1"]) / len(v),
            }
            for k, v in sorted(by_cause.items())
        },
    }


def print_report(tag: str, res: dict[str, Any]) -> None:
    s = res["summary"]
    print(f"\n=== 排障域回放 [{tag}] ===")
    print(f"  n={s['n']}  Root Cause Top-1 {s['top1']:.1%}  Top-3 {s['top3']:.1%}")
    print(f"  未收敛 {s['escalated']:.1%}  收敛到错根因 {s['wrong_cause']:.1%}")
    print(f"  经过审批中断 {s['resumed']}/{s['n']} 次")
    print(f"  延迟 p50={s['p50_ms']:.0f}ms p95={s['p95_ms']:.0f}ms")
    print("  分根因：")
    for cause, item in s["by_cause"].items():
        print(f"    {cause:20s} n={item['n']:2d} top1={item['top1']:.0%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="ops-v1")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from runtime.config import load_dotenv

    load_dotenv()
    cases = load_cases(args.limit)
    print(f"[ops] {len(cases)} 条回放用例 …")
    res = run(cases)
    print_report(args.tag, res)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.tag}.json"
    out.write_text(json.dumps({args.tag: res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果已写入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
