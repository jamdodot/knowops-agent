"""知识域的工具实现（M0 用内存夹具，M1 换 PG）。

这里有一条必须讲清的替换关系：
`_visible()` 现在是 Python 实现，**语义必须与 `docs/05` §9.1 的 SQL 完全一致**
（祖先链展开 + 审批集合 `= ANY()` + 共享组）。M1 换成 PG 的 `permitted` CTE 时，
用 `deploy/smoke/01_acl_smoke.sql` 的同一组用例对照——两边语义只要差一点，
表现就是"冒烟通过但线上越权"。

为什么工具要自己取 subject 而不接受模型传参：如果 `subject` 能由模型填，
提示注入就能改权限。所以工具从运行上下文（ContextVar）里读，模型无从干预。
"""

from __future__ import annotations

import re
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
        timeout_ms=3_000,
        idempotent=True,
        cost_hint="M1 起为向量 + BM25 双路 SQL",
    )
    def hybrid_retrieve(query: str, top_k: int = 5) -> list[dict[str, Any]]:
        ctx = current_context()
        if ctx.subject is None:  # 防御：没有主体就没有权限，宁可不召回
            return []

        q = _tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in DOCS:
            for chunk in doc["chunks"]:
                overlap = len(q & _tokens(chunk["content"] + doc["title"]))
                if overlap:
                    scored.append(
                        (
                            overlap,
                            {
                                "chunk_id": chunk["chunk_id"],
                                "doc_id": doc["doc_id"],
                                "title": doc["title"],
                                "page_no": chunk["page_no"],
                                "section_path": chunk["section_path"],
                                "content": chunk["content"],
                                "acl_epoch": doc["acl_epoch"],
                                "eid": f"e_{doc['doc_id']}_{chunk['chunk_id']}",
                                "_doc": doc,
                            },
                        )
                    )
        scored.sort(key=lambda x: x[0], reverse=True)

        # 先算"候选 top-k 涉及哪些文档"，再判可见性——顺序很重要：
        # 这样 E8（权限误过滤）才有据可查；如果先过滤再打分，就永远不知道被拦掉了什么
        candidates = scored[: top_k * 2]
        verdicts: dict[str, dict[str, Any]] = {}
        for _score, row in candidates:
            doc = row["_doc"]
            if doc["doc_id"] in verdicts:
                continue
            visible, by = _visible(doc, ctx.subject)
            verdicts[doc["doc_id"]] = {"visible": visible, "by": by}

        with tracing.span(
            "shared.acl.build_filter",
            {
                "knowops.acl.dept_paths_n": len(ctx.subject.dept_paths),
                "knowops.acl.approval_n": len(ctx.subject.approval_doc_ids or []),
                "knowops.acl.filter_hash": ctx.acl_fingerprint,
                "knowops.acl.verdicts": _json(verdicts),
            },
        ) as sp:
            kept = [row for _s, row in candidates if verdicts[row["doc_id"]]["visible"]]
            tracing.set_attrs(
                sp,
                **{"knowops.acl.rows_filtered": len(candidates) - len(kept), "knowops.retrieval.hits": len(kept)},
            )

        out = []
        for row in kept[:top_k]:
            row = dict(row)
            row.pop("_doc", None)
            out.append(row)
        return out

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
        for doc in DOCS:
            if doc["doc_id"] != doc_id:
                continue
            visible, by = _visible(doc, ctx.subject)
            if not visible:
                # 取回也要判权限：引用是数据，一旦能按 id 取全文，就等于开了后门
                return {"doc_id": doc_id, "error": "not_permitted", "by": by}
            return {
                "doc_id": doc_id,
                "title": doc["title"],
                "acl_epoch": doc["acl_epoch"],
                "content": "\n".join(c["content"] for c in doc["chunks"]),
            }
        return {"doc_id": doc_id, "error": "not_found"}


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
