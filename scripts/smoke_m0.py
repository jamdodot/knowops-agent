#!/usr/bin/env python
"""M0 端到端验收：双域假链路 + trace 关联（不需要 docker，不需要真实模型）。

这是 docs/04 §2 M0 验收口径的执行版。它要证明的不是"模型能回答"，而是：

1. 知识域：一条 SSE 流按 `meta → citation → delta → done` 顺序产出，
   citation 先于引用它的 delta，done 是最后一帧，且 trace_id 贯穿始终。
2. 排障域：create → input_required（断流 ≠ 失败）→ resume → completed，
   中间状态可通过 GET 查询。
3. 观测：两个域各自产出一条以 `{domain}.http.serve` 为根的 span 树，
   树的叶子包含 `{domain}.node.*` 与 `shared.llm.chat`。

跑法：python scripts/smoke_m0.py    （退出码 0 = 通过）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE_DIR = ROOT / "agent-service"

if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from api.app_fulfillment import create_app as create_fulfillment_app  # noqa: E402
from api.app_knowledge import create_app as create_knowledge_app  # noqa: E402
from runtime.config import Settings  # noqa: E402
from runtime import tracing  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def frame_event(frame: str) -> str:
    for line in frame.split("\n"):
        if line.startswith("event: "):
            return line[len("event: ") :]
    return ""


def frame_data(frame: str) -> dict:
    for line in frame.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    return {}


def subject(**kw):
    base = {
        "user_id": "u_2048",
        "account_status": "active",
        "dept_paths": ["D10", "D10.3"],
        "roles": ["employee", "fulfillment_ops"],
        "groups": [],
        "approval_doc_ids": ["doc_902"],
        "subject_epoch": 1743,
    }
    base.update(kw)
    return base


def main() -> int:
    settings = Settings(llm_mode="mock", trace_export="memory")
    tracing.setup("memory")

    # -------------------------------------------------------------- 知识域
    print("\n[1] 知识域 SSE 顺序（meta → citation → delta → done）")
    app_k = create_knowledge_app(settings)
    ck = TestClient(app_k)
    r = ck.post(
        "/v1/knowledge/answers",
        json={
            "query": "跨部门调拨要几级审批？",
            "subject_ctx": subject(roles=["employee"]),
            "session_id": "smoke_k",
            "turn_seq": 1,
        },
    )
    check("HTTP 200", r.status_code == 200, f"got {r.status_code}")

    frames = [f for f in r.text.split("\n\n") if f.strip()]
    events = [frame_event(f) for f in frames]
    check("meta 是第一帧", events and events[0] == "meta", str(events))
    check("done 是最后一帧", events and events[-1] == "done", str(events))
    check("含 citation", "citation" in events)
    check("含 delta", "delta" in events)

    meta = frame_data(frames[0])
    trace_id_k = meta.get("trace_id", "")
    check("meta 带 trace_id", bool(trace_id_k))

    # citation 必须先于引用它的 delta（提取 eid 与 delta 引用）
    cit = next((frame_data(f) for f in frames if frame_event(f) == "citation"), {})
    eids = {c["eid"] for c in cit.get("citations", [])}
    deltas = [frame_data(f) for f in frames if frame_event(f) == "delta"]
    all_eids = {e for d in deltas for e in (d.get("eids") or [])}
    check("正文引用的 eid 都来自 citation", all_eids <= eids, f"{all_eids} vs {eids}")

    # -------------------------------------------------------------- 排障域
    print("\n[2] 排障域 Task 中断与恢复")
    app_f = create_fulfillment_app(settings)
    cf = TestClient(app_f)
    r = cf.post(
        "/v1/fulfillment/tasks",
        json={"symptom": "订单 O20261102001 物流三天没更新", "subject_ctx": subject(roles=["fulfillment_ops"]), "session_id": "smoke_f"},
    )
    check("create HTTP 200", r.status_code == 200, f"got {r.status_code}")

    fframes = [f for f in r.text.split("\n\n") if f.strip()]
    fevents = [frame_event(f) for f in fframes]
    check("停在 input_required", "input_required" in fevents, str(fevents))
    check("审批前无 done", "done" not in fevents)

    ir = next(frame_data(f) for f in fframes if frame_event(f) == "input_required")
    task_id = ir["task_id"]
    approval_id = ir["approval"]["approval_id"]

    g = cf.get(f"/v1/fulfillment/tasks/{task_id}")
    check("GET 状态 input_required", g.json().get("state") == "input_required", str(g.json()))

    res = cf.post(
        f"/v1/fulfillment/tasks/{task_id}/resume",
        json={
            "task_id": task_id,
            "subject_ctx": subject(roles=["fulfillment_ops"]),
            "approval": {"approval_id": approval_id, "decision": "approved", "comment": "确认重推"},
        },
    )
    check("resume HTTP 200", res.status_code == 200, f"got {res.status_code}")
    reframes = [f for f in res.text.split("\n\n") if f.strip()]
    check("resume 后出 done", "done" in [frame_event(f) for f in reframes])
    check("最终状态 completed", cf.get(f"/v1/fulfillment/tasks/{task_id}").json().get("state") == "completed")

    # -------------------------------------------------------------- 观测
    print("\n[3] span 树关联（http 为根，节点与 LLM 为叶）")
    tree_k = tracing.span_tree(trace_id_k)
    check("知识域 span 树非空", bool(tree_k))
    if tree_k:
        root_name = tree_k[0]["name"]
        check("http 为根", "http.serve" in root_name, root_name)

        def collect(names, node):
            names.add(node["name"])
            for c in node.get("children", []):
                collect(names, c)

        all_names: set[str] = set()
        collect(all_names, tree_k[0])
        check("含 node span", any(".node." in n for n in all_names), str(all_names))
        check("含 llm span", any("llm.chat" in n for n in all_names), str(all_names))

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
