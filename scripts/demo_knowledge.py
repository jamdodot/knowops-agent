#!/usr/bin/env python
"""一键演示：解析语料 → 分块 → 灌入检索 → 检索层权限对比 + 端到端问答。

分两部分，各自独立：
1. **检索层权限对比**（不依赖 LLM）：同一问题，不同身份，看召回差异——这是
   项目核心卖点"权限是检索的一部分"，纯本地、断网也能演示。
2. **端到端问答**（可选真模型）：走完整 SSE 链路。mock 模式只能演示"链路通"
   （事件序列对），答案要真实须 `KW_LLM_MODE=online` + 配好 key。

用法：
    python scripts/demo_knowledge.py                     # 检索层对比 + mock 链路
    KW_LLM_MODE=online python scripts/demo_knowledge.py  # 端到端用真模型生成答案
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "agent-service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

from fastapi.testclient import TestClient  # noqa: E402

from api.app_knowledge import create_app  # noqa: E402
from runtime.config import Settings  # noqa: E402

CORPUS = ROOT / "deploy" / "sample-corpus"

EMPLOYEE = {"user_id": "u_1001", "dept_paths": ["D10"], "roles": ["employee"]}
PAYROLL = {"user_id": "u_9001", "dept_paths": ["D10.9"], "roles": ["payroll"]}


def _subject(ctx: dict) -> dict:
    return {
        "user_id": ctx["user_id"],
        "account_status": "active",
        "dept_paths": ctx["dept_paths"],
        "roles": ctx["roles"],
        "groups": [],
        "approval_doc_ids": [],
        "subject_epoch": 1,
    }


def part1_retrieval_acl(client: TestClient) -> None:
    """检索层权限对比：不经过 LLM，直接看召回。"""
    print("\n" + "=" * 64)
    print("第一部分：检索层权限对比（纯本地，不依赖 LLM）")
    print("=" * 64)

    cases = [
        ("费用报销超过5万元谁审批？", EMPLOYEE, "普通员工(D10)"),
        ("薪酬数据谁能查看？", EMPLOYEE, "普通员工(D10)"),
        ("薪酬数据谁能查看？", PAYROLL, "薪酬专员(D10.9)"),
    ]
    for query, ctx, label in cases:
        r = client.post(
            "/v1/knowledge/answers",
            json={"query": query, "subject_ctx": _subject(ctx), "session_id": f"s_{label}", "turn_seq": 1},
            headers={"X-Contract-Version": "1.0.0"},
        )
        cites = []
        for frame_str in r.text.split("\n\n"):
            if "event: citation" in frame_str:
                import json

                for line in frame_str.strip().split("\n"):
                    if line.startswith("data:"):
                        cites = json.loads(line[5:]).get("citations", [])
        titles = sorted({c["title"] for c in cites})
        # 关键信号：受限文档「薪酬保密制度」是否出现在召回里
        has_restricted = any("薪酬保密" in t for t in titles)
        flag = "🚫 已拦截受限文档" if not has_restricted else "✅ 可见受限文档"
        print(f"\n  【{label}】问：「{query}」")
        print(f"    召回 {len(cites)} 条，涉及文档：{titles}")
        print(f"    薪酬保密制度 → {flag}")


def part2_qa(client: TestClient) -> None:
    """端到端问答：走完整 SSE 链路。"""
    print("\n" + "=" * 64)
    print("第二部分：端到端问答（完整 SSE 链路）")
    print("=" * 64)

    for query, ctx, label in [
        ("费用报销超过5万元需要谁审批？", EMPLOYEE, "普通员工问报销"),
        ("薪酬数据谁能查看？", PAYROLL, "薪酬专员问薪酬"),
    ]:
        r = client.post(
            "/v1/knowledge/answers",
            json={"query": query, "subject_ctx": _subject(ctx), "session_id": f"s2_{label}", "turn_seq": 1},
            headers={"X-Contract-Version": "1.0.0"},
        )
        answer, cites, finish = [], [], "?"
        import json

        for frame_str in r.text.split("\n\n"):
            if not frame_str.strip():
                continue
            event, data = "", {}
            for line in frame_str.strip().split("\n"):
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        pass
            if event == "delta":
                answer.append(data.get("text", ""))
            elif event == "citation":
                cites = data.get("citations", [])
            elif event == "done":
                finish = data.get("finish_reason", "?")
        print(f"\n  【{label}】「{query}」")
        print(f"    答案：{''.join(answer) or '（无）'}")
        print(f"    引用 {len(cites)} 条，结束状态：{finish}")


def main() -> int:
    os.environ.setdefault("KW_KNOWLEDGE_CORPUS", str(CORPUS))
    settings = Settings.from_env()

    print(f"LLM 模式：{settings.llm_mode}")
    if settings.llm_mode != "online":
        print("  ⚠ 当前是 mock 回放：第一部分的检索/权限对比是真实的；")
        print("    第二部分的答案文字是录制的固定文本，只证明链路通。")
        print("    要看真实答案：填好 config/.env 的 DEEPSEEK_API_KEY 后，")
        print("    用 KW_LLM_MODE=online 重跑本脚本。")

    app = create_app(settings)
    client = TestClient(app)

    from domains.knowledge.tools import _get_retriever

    retriever = _get_retriever()
    print(f"语料：{CORPUS}")
    print(f"检索后端：{type(retriever).__name__}，已索引 chunk：{retriever.index_size()}")

    part1_retrieval_acl(client)
    part2_qa(client)

    print("\n" + "=" * 64)
    print("演示结束。核心看点（第一部分）：")
    print("  · 普通员工问薪酬 → 无召回（越权被拦截）")
    print("  · 薪酬专员问薪酬 → 命中薪酬保密制度（有权限）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
