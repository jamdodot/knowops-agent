#!/usr/bin/env python
"""PG 真库自检：扩展 / 索引 / 数据量 / 检索路 / 鉴权对比，一次跑完。

用法：
    python scripts/verify_pg.py            # 环境 + 单身份检索
    python scripts/verify_pg.py --acl      # 额外跑两身份权限对比

它比 demo 更"工程"：不做 LLM 生成，只验证"PG 里到底有没有东西、哪条检索路活着、
权限是不是真的拦住了"。面试时也可以直接拿这段输出讲。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "agent-service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

from runtime.config import load_dotenv  # noqa: E402


class Subject:
    """与 runtime 的 SubjectCtx 同构的最小替身：本脚本只关心鉴权字段。"""

    def __init__(self, user_id: str, dept_paths: list[str], roles: list[str]):
        self.user_id = user_id
        self.dept_paths = dept_paths
        self.roles = roles
        self.groups: list[str] = []
        self.approval_doc_ids: list[str] = []


def main() -> int:
    load_dotenv()
    from retrieval.dsn import pg_dsn

    dsn = pg_dsn()
    if not dsn:
        print("✗ 没配数据库连接（config/.env 的 POSTGRES_*）")
        return 2

    import psycopg

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        print("== 环境 ==")
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            print("  PG:", cur.fetchone()[0].split(",")[0])
            cur.execute(
                "SELECT extname || ' ' || extversion FROM pg_extension ORDER BY extname"
            )
            print("  扩展:", ", ".join(r[0] for r in cur.fetchall()))
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'kb_chunk' ORDER BY 1"
            )
            print("  索引:", ", ".join(r[0] for r in cur.fetchall()))
            cur.execute(
                """
                SELECT count(*), count(embedding), count(DISTINCT doc_id)
                FROM knowops_read.kb_chunk
                """
            )
            n, n_emb, n_doc = cur.fetchone()
            print(f"  数据: chunk={n}（有向量 {n_emb}），文档={n_doc}")

    from retrieval.pg_retriever import PgRetriever

    r = PgRetriever(dsn)
    print(f"\n== 检索后端：{type(r).__name__}，索引 {r.index_size()} 条 ==")

    queries = ["费用报销超过5万元需要谁审批？", "薪酬数据谁能查看？", "跨部门调拨谁审批？"]
    subject = Subject("u_1001", ["D10"], ["employee"])
    print("\n== 单身份检索（u_1001 / D10 / employee）==")
    for q in queries:
        evs = r.hybrid_retrieve(q, subject, top_k=3)
        mode = r.last_backend_notes.get("bm25")
        dense = "dense✅" if r.last_backend_notes.get("dense") else "dense✗"
        print(f"\n  「{q}」  路={mode} {dense}")
        if not evs:
            print("    （无召回）")
        for e in evs:
            print(f"    {e.score:.4f}  [{e.title}] {e.section_path or '-'} :: {e.content[:36]}…")

    if "--acl" in sys.argv:
        payroll = Subject("u_9001", ["D10", "D10.9"], ["payroll"])
        print("\n== 权限对比：同一问题，两种身份 ==")
        for s, label in ((subject, "普通员工 D10"), (payroll, "薪酬专员 D10.9")):
            evs = r.hybrid_retrieve("薪酬数据谁能查看？", s, top_k=5)
            titles = sorted({e.title for e in evs})
            blocked = not any("薪酬" in t for t in titles)
            print(f"  {label}: 召回 {len(evs)} 条 → {titles} {'🚫 拦截' if blocked else '✅ 可见'}")

    print("\n== doc_fetch（small-to-big 回填）==")
    for doc_id in ("费用报销管理办法", "薪酬保密制度"):
        got = r.doc_fetch(doc_id, subject)
        if got.get("error"):
            print(f"  {doc_id}: {got['error']}")
        else:
            print(f"  {doc_id}: 父块正文 {len(got['content'])} 字，acl_epoch={got['acl_epoch']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
