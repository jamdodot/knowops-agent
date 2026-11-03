"""知识域的工具实现。

检索后端可插拔：`retrieval.build_retriever()` 按环境返回 MemoryRetriever（无 PG）
或 PgRetriever（有 PG）。默认灌入内置种子语料 DOCS，也支持从 `KW_KNOWLEDGE_CORPUS`
指向的目录解析真实文档灌入（见 `scripts/kw.py demo`）。

权限语义有一条铁律：`_visible()` 的判定（public / 部门祖先链 / 角色 / 审批例外 /
共享组）与 `docs/05` §2.3 的 SQL 语义**必须一致**。PG 侧是 `permitted` CTE 下推，
内存侧是这里的 Python 实现——两边用同一组用例对照，差一点就是"冒烟过但线上越权"。

为什么工具要自己取 subject 而不接受模型传参：如果 `subject` 能由模型填，
提示注入就能改权限。所以工具从运行上下文（ContextVar）里读，模型无从干预。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from runtime import current_context
from runtime.registry import ToolRegistry
from runtime import tracing

_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]{2}")

#: M0 夹具：3 份文档，覆盖 public / 部门祖先链 / 审批例外 / 不可见 四路
DOCS: list[dict[str, Any]] = [
    {
        "doc_id": "doc_881",
        "title": "费用报销管理办法",
        "acl_mode": "dept",
        "dept_paths": ["D10"],
        "roles": [],
        "groups": [],
        "acl_epoch": 17,
        "chunks": [
            {
                "chunk_id": "c1",
                "page_no": 7,
                "section_path": "第3章 审批权限",
                "content": "费用报销审批流程：单笔金额 5 万元以下的，由部门负责人审批；超过 5 万元的，需财务总监审批。",
            },
            {
                "chunk_id": "c2",
                "page_no": 11,
                "section_path": "第5章 资金调拨",
                "content": "跨部门资金调拨：调拨金额超过 10 万元的，需总经理审批；10 万元以下由财务负责人审批。",
            },
        ],
    },
    {
        "doc_id": "doc_902",
        "title": "采购付款管理规定",
        "acl_mode": "approval",
        "dept_paths": ["D20"],
        "roles": [],
        "groups": [],
        "acl_epoch": 9,
        "chunks": [
            {
                "chunk_id": "c1",
                "page_no": 3,
                "section_path": "第2章 付款时限",
                "content": "采购付款需在合同签订后 30 日内提交付款申请，逾期需附情况说明。",
            }
        ],
    },
    {
        "doc_id": "doc_915",
        "title": "薪酬保密制度",
        "acl_mode": "dept",
        "dept_paths": ["D10.9"],
        "roles": [],
        "groups": [],
        "acl_epoch": 4,
        "chunks": [
            {
                "chunk_id": "c1",
                "page_no": 2,
                "section_path": "第1章 保密范围",
                "content": "薪酬数据属保密信息，仅薪酬专员与直属上级可见，禁止跨部门传播。",
            }
        ],
    },
]


def _visible(doc: dict[str, Any], subject: Any) -> tuple[bool, str]:
    """返回 (是否可见, 命中哪一路)。`by` 直接进 `knowops.acl.verdicts`（14 §4.3）。

    四条判定与 05 §2.3 一一对应：public / 部门祖先链 / 角色 / 审批例外。
    顺序不能换：审计要能回答"是因为哪一路才看得见"。
    """
    if doc["acl_mode"] == "public":
        return True, "public"
    for dept in doc["dept_paths"]:
        if dept in subject.dept_paths:
            return True, f"dept:{dept}"
    for role in doc["roles"]:
        if role in subject.roles:
            return True, f"role:{role}"
    if doc["doc_id"] in (subject.approval_doc_ids or []):
        return True, "approval"
    for group in doc["groups"]:
        if group in (subject.groups or []):
            return True, f"group:{group}"
    return False, "none"


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


# ------------------------------------------------------------------ 检索后端（惰性单例）
_retriever: Any = None
_retriever_lock = threading.Lock()


def _get_retriever() -> Any:
    """惰性初始化检索后端，并灌入种子语料。

    初始化只做一次（线程安全）。种子语料 = 内置 DOCS + `KW_KNOWLEDGE_CORPUS` 目录
    （若有）里解析+分块后的真实文档。这样无 PG 时也有可演示的真实检索。
    """
    global _retriever
    if _retriever is not None:
        return _retriever
    with _retriever_lock:
        if _retriever is not None:
            return _retriever
        from retrieval import build_retriever
        from retrieval.memory_retriever import MemoryRetriever

        r = build_retriever()
        # 内置种子语料始终灌入（保证"开箱即能答"）
        if isinstance(r, MemoryRetriever):
            _seed_memory(r)
        # 外部语料目录（可选）：解析真实文档灌入
        corpus = Path(__import__("os").environ.get("KW_KNOWLEDGE_CORPUS", ""))
        if corpus.is_dir() and isinstance(r, MemoryRetriever):
            _seed_corpus(r, corpus)
        _retriever = r
        return r


def _seed_memory(r: Any) -> None:
    """把内置 DOCS 夹具灌入 MemoryRetriever，作为开箱语料。"""
    for doc in DOCS:
        acl = {
            "v": "public" if doc["acl_mode"] == "public" else "scoped",
            "depts": doc["dept_paths"],
            "roles": doc["roles"],
            "groups": doc["groups"],
            "acl_epoch": doc["acl_epoch"],
        }
        chunks = [
            {
                "chunk_id": c["chunk_id"],
                "content": c["content"],
                "page_no": c["page_no"],
                "section_path": c["section_path"],
            }
            for c in doc["chunks"]
        ]
        r.ingest(doc["doc_id"], doc["title"], chunks, acl)


def _seed_corpus(r: Any, corpus: Path) -> None:
    """解析 `KW_KNOWLEDGE_CORPUS` 目录下的文档，分块后灌入。

    每份文档一个 ACL 元文件 `<doc>.acl.yaml`（可选），缺省 public。样例：
        v: scoped
        depts: [D10]
        roles: []
        groups: []
        acl_epoch: 1
    """
    import yaml

    from parse.pipeline import ParsePipeline
    from parse.chunker import SemanticChunker, ChunkConfig

    pipe = ParsePipeline()
    chunker = SemanticChunker(ChunkConfig())
    for p in sorted(corpus.iterdir()):
        if p.suffix.lower() not in (".md", ".txt", ".pdf"):
            continue
        acl_path = p.with_suffix(".acl.yaml")
        acl: dict[str, Any] = {"v": "public", "depts": [], "roles": [], "groups": [], "acl_epoch": 1}
        if acl_path.is_file():
            try:
                acl = {**acl, **(yaml.safe_load(acl_path.read_text(encoding="utf-8")) or {})}
            except Exception:
                pass
        try:
            doc, _report = pipe.parse(p, doc_id=p.stem)
            result = chunker.chunk(doc)
        except Exception:
            continue  # 单文档失败不阻塞整体灌入
        chunks = [
            {
                "chunk_id": f"{p.stem}_{c.ord}",
                "content": c.content,
                "page_no": c.page_no,
                "section_path": c.section_path,
            }
            for c in result.children
        ]
        if chunks:
            r.ingest(p.stem, p.stem, chunks, acl)


def register(registry: ToolRegistry) -> None:
    @registry.register(
        name="hybrid_retrieve",
        description="混合检索制度文档片段，返回带 eid 的证据块。权限条件在检索内部生效，调用方无需也无法传入可见范围。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        timeout_ms=5_000,
        idempotent=True,
        cost_hint="向量 + BM25 双路（内存或 PG 下推）",
    )
    def hybrid_retrieve(query: str, top_k: int = 5) -> list[dict[str, Any]]:
        ctx = current_context()
        if ctx.subject is None:  # 防御：没有主体就没有权限，宁可不召回
            return []
        r = _get_retriever()
        with tracing.span(
            "shared.acl.build_filter",
            {
                "knowops.acl.dept_paths_n": len(ctx.subject.dept_paths),
                "knowops.acl.approval_n": len(ctx.subject.approval_doc_ids or []),
                "knowops.acl.filter_hash": ctx.acl_fingerprint,
                "knowops.retrieval.backend": type(r).__name__,
            },
        ) as sp:
            evs = r.hybrid_retrieve(query, ctx.subject, top_k=top_k)
            tracing.set_attrs(
                sp, **{"knowops.retrieval.hits": len(evs), "knowops.retrieval.backend": type(r).__name__}
            )
        return [e.to_row() for e in evs]

    @registry.register(
        name="doc_fetch",
        description="按 doc_id 取回文档正文（父块）。用于 small-to-big 的回填，返回前会再次校验权限。",
        input_schema={
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
            "additionalProperties": False,
        },
        timeout_ms=2_000,
        idempotent=True,
    )
    def doc_fetch(doc_id: str) -> dict[str, Any]:
        ctx = current_context()
        r = _get_retriever()
        return r.doc_fetch(doc_id, ctx.subject)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
