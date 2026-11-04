#!/usr/bin/env python
"""把语料灌进 PG（ParadeDB）：解析 → 分块 → 写 write/read 两张拓扑。

前置：容器已起来（`python scripts/kw.py up`），config/.env 里有 KW_PG_DSN。

用法：
    python scripts/ingest_pg.py                      # 灌 deploy/sample-corpus
    python scripts/ingest_pg.py --corpus <目录>       # 灌指定目录
    python scripts/ingest_pg.py --no-embed            # 只写 BM25（不调 embedding，省额度）

为什么要"先删后写"：同一 doc_id 重跑必须幂等——分块参数或解析引擎升级后，
重跑一次就该是新结果，不该出现新旧块并存的脏数据（见 retrieval/pg_ingest.py）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "agent-service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

from runtime.config import load_dotenv  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "deploy" / "sample-corpus"))
    ap.add_argument("--no-embed", action="store_true", help="跳过 embedding（只写 BM25）")
    args = ap.parse_args()

    load_dotenv()
    from retrieval.dsn import mask, pg_dsn

    dsn = pg_dsn()
    if not dsn:
        print("✗ 没配数据库连接。先 python scripts/kw.py up 起容器，再在 config/.env 里填")
        print("    POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD")
        return 2

    from retrieval.pg_ingest import ingest_directory

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"✗ 语料目录不存在：{corpus}")
        return 2

    print(f"语料：{corpus}")
    print(f"DSN：{mask(dsn)}")
    reports = ingest_directory(dsn, corpus, embed=not args.no_embed)

    ok = 0
    for r in reports:
        print(r.line())
        ok += 0 if r.error else 1
    print(f"\n完成：{ok}/{len(reports)} 份文档入库")
    if not args.no_embed and not any(r.embedded for r in reports):
        print("⚠ 没有任何文档拿到 embedding（SILICONFLOW_API_KEY 没配或网络失败）；")
        print("  检索会只走 BM25 一路，功能可用但召回质量下降。")
    return 0 if ok == len(reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
