"""检索后端抽象层。

这是 M1 混合检索的落点，与 `parse` 平级、同属域无关基础设施（不进 runtime，
runtime 有域名零污染的硬约束，检索也不该进 runtime——它是知识域的专属能力，
排障域复用的是日志检索，不是这个）。

对外只有一个接口 `Retriever`，两个实现：

- `MemoryRetriever`：内存 BM25 倒排 + 可选真 embedding，无 Docker 也能跑。用于
  本地演示与 CI，也是"先出数据、再上重武器"节奏里的第一块垫脚石。
- `PgRetriever`：按 docs/05 §3 的 `permitted` CTE 把鉴权下推到同一条 SQL，
  dense(pgvector) + BM25(pg_search) 双路 + RRF。装了 Docker 后启用。

切换方式（环境变量，`config/.env.example` 里有说明）：
- `KW_PG_DSN` 非空 → 自动用 PgRetriever；
- 否则回落到 MemoryRetriever（从 `KW_KNOWLEDGE_CORPUS` 指定的目录解析+分块+灌入）。
"""

from .base import Evidence, Retriever
from .memory_retriever import MemoryRetriever
from .pg_retriever import PgRetriever, build_retriever

__all__ = ["Retriever", "Evidence", "MemoryRetriever", "PgRetriever", "build_retriever"]
