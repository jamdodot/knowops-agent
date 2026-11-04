"""PG 入库链路：解析 → 分块 → 写 truth(write) + 派生(read)。

为什么单独一个模块而不是塞进 PgRetriever：检索是读路径、鉴权下推在那一条 SQL 里；
入库是写路径，要同时维护 write 的真相表和 read 的派生表。混在一起会让"只读派生"
这条硬约束在代码里失守。

## 写哪些表（对齐 docs/06 §3 的 CQRS 单向派生）

| 表 | schema | 作用 |
|---|---|---|
| kb_document | write | 真相：文档元数据 + acl_epoch |
| kb_acl_grant | write | 真相：授权来源（static / public） |
| kb_doc_mirror | read | 派生：检索 SQL 的 join 目标（状态 + 有效期） |
| kb_parent_chunk | read | 派生：small-to-big 的父块 |
| kb_chunk | read | 派生：检索单元（acl_jsonb 快照 + tsv + embedding） |

**acl_jsonb 是快照不是真相**：这里按入库时的授权算一次塞进去，权限变更由 ACL 链路
（改 epoch → 重算快照）负责重刷。所以 `acl_epoch` 必须跟着写，否则引用失效判断会错。

## 幂等

同一 doc_id 重跑 = 先删后写（delete kb_chunk/kb_parent_chunk where doc_id，再 upsert
文档与镜像）。分块参数或解析引擎升级后重跑即可，不用手工清库。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .embed import embed_texts, to_vector_literal

_MIME = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@dataclass
class IngestReport:
    doc_id: str
    title: str
    parents: int = 0
    children: int = 0
    embedded: bool = False
    acl: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def line(self) -> str:
        if self.error:
            return f"  ✗ {self.doc_id}: {self.error}"
        emb = "已嵌入" if self.embedded else "未嵌入(BM25 only)"
        return (
            f"  ✓ {self.doc_id}：父块 {self.parents} / 子块 {self.children}，{emb}，"
            f"acl={self.acl.get('v')}({','.join(self.acl.get('depts') or []) or '-'})"
        )


def upsert_document(
    conn: Any,
    *,
    doc_id: str,
    title: str,
    acl: dict[str, Any],
    file_key: str = "",
    mime: str | None = None,
    owner_id: str = "u_ingest",
    owner_dept: str | None = None,
) -> None:
    """写真相表：文档 + 授权来源。"""
    epoch = int(acl.get("acl_epoch") or 1)
    dept = owner_dept or (acl.get("depts") or ["D10"])[0]
    mode = "dynamic" if acl.get("rule_expr") else "static"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO knowops_write.kb_document
                (id, title, file_key, mime, version, owner_id, owner_dept,
                 acl_mode, status, acl_epoch, parsed_at, updated_at)
            VALUES (%s, %s, %s, %s, 1, %s, %s, %s, 'active', %s, now(), now())
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                file_key = EXCLUDED.file_key,
                mime = EXCLUDED.mime,
                acl_mode = EXCLUDED.acl_mode,
                status = 'active',
                acl_epoch = EXCLUDED.acl_epoch,
                parsed_at = now(),
                updated_at = now()
            """,
            (doc_id, title, file_key, mime, owner_id, dept, mode, epoch),
        )
        # 授权：先清再写（同一文档的 grant 由入库方全权维护）
        cur.execute("DELETE FROM knowops_write.kb_acl_grant WHERE doc_id = %s", (doc_id,))
        grants: list[tuple[str, str]] = []
        if acl.get("v") == "public":
            # 与 02_seed_m0.sql 保持一致：public 文档挂一条 source=public 的 role 授权
            grants.append(("role", "employee"))
        grants += [("dept", d) for d in acl.get("depts") or []]
        grants += [("role", r) for r in acl.get("roles") or []]
        for subject_type, subject_id in grants:
            cur.execute(
                """
                INSERT INTO knowops_write.kb_acl_grant
                    (doc_id, subject_type, subject_id, action, source, granted_by)
                VALUES (%s, %s, %s, 'read', %s, 'ingest')
                """,
                (doc_id, subject_type, subject_id, "public" if acl.get("v") == "public" else "static"),
            )


def write_chunks(
    conn: Any,
    doc_id: str,
    *,
    parents: list[Any],
    children: list[Any],
    acl: dict[str, Any],
    embed: bool = True,
    embed_model: str = "BAAI/bge-m3",
) -> bool:
    """写派生表：镜像 + 父块 + 子块。返回是否成功嵌入。"""
    epoch = int(acl.get("acl_epoch") or 1)
    acl_jsonb = json.dumps(
        {
            "v": acl.get("v") or "scoped",
            "depts": acl.get("depts") or [],
            "roles": acl.get("roles") or [],
            "groups": acl.get("groups") or [],
            "users": acl.get("users") or [],
        },
        ensure_ascii=False,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO knowops_read.kb_doc_mirror (doc_id, status, acl_from, acl_to, acl_epoch, version)
            VALUES (%s, 'active', now(), NULL, %s, 1)
            ON CONFLICT (doc_id) DO UPDATE SET
                status = 'active', acl_epoch = EXCLUDED.acl_epoch, updated_at = now()
            """,
            (doc_id, epoch),
        )
        # 幂等：先删旧块（父块有引用也要先删子块）
        cur.execute("DELETE FROM knowops_read.kb_chunk WHERE doc_id = %s", (doc_id,))
        cur.execute("DELETE FROM knowops_read.kb_parent_chunk WHERE doc_id = %s", (doc_id,))

        for p in parents:
            cur.execute(
                """
                INSERT INTO knowops_read.kb_parent_chunk (id, doc_id, content, section_path, page_no)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (p["id"], doc_id, p["content"], p.get("section_path"), p.get("page_no")),
            )

    contents = [c["content"] for c in children]
    vectors = embed_texts(contents) if embed else []

    with conn.cursor() as cur:
        for i, c in enumerate(children):
            vec = vectors[i] if i < len(vectors) and vectors[i] else None
            cur.execute(
                """
                INSERT INTO knowops_read.kb_chunk
                    (id, doc_id, parent_id, ord, content, section_path, page_no, token_len,
                     acl_jsonb, acl_epoch, embed_model, embed_ver, tsv, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s,
                        to_tsvector('simple', %s), %s::vector)
                """,
                (
                    c["id"],
                    doc_id,
                    c.get("parent_id"),
                    c.get("ord", i),
                    c["content"],
                    c.get("section_path"),
                    c.get("page_no"),
                    c.get("token_len"),
                    acl_jsonb,
                    epoch,
                    embed_model if vec else None,
                    "v1" if vec else None,
                    c["content"],
                    to_vector_literal(vec) if vec else None,
                ),
            )
    return bool(vectors) and len(vectors) == len(children)


def load_acl(path: Path) -> dict[str, Any]:
    """读 `<doc>.acl.yaml`；缺省 public。字段与 tools._seed_corpus 保持一致。"""
    acl: dict[str, Any] = {"v": "public", "depts": [], "roles": [], "groups": [], "acl_epoch": 1}
    acl_path = path.with_suffix(".acl.yaml")
    if acl_path.is_file():
        import yaml

        try:
            acl = {**acl, **(yaml.safe_load(acl_path.read_text(encoding="utf-8")) or {})}
        except Exception:
            pass
    return acl


def ingest_path(
    conn: Any,
    path: Path,
    *,
    doc_id: str | None = None,
    title: str | None = None,
    acl: dict[str, Any] | None = None,
    embed: bool = True,
) -> IngestReport:
    """解析 + 分块 + 写库，单文档。异常收进 report，不打断整批。"""
    from parse.chunker import ChunkConfig, SemanticChunker
    from parse.pipeline import ParsePipeline

    did = doc_id or path.stem
    rep = IngestReport(doc_id=did, title=title or path.stem, acl=acl or load_acl(path))
    try:
        doc, _report = ParsePipeline().parse(path, doc_id=did)
        result = SemanticChunker(ChunkConfig()).chunk(doc)
        if not result.children:
            rep.error = "分块结果为空（解析质量闸门未通过？）"
            return rep
        upsert_document(
            conn,
            doc_id=did,
            title=rep.title,
            acl=rep.acl,
            file_key=str(path),
            mime=_MIME.get(path.suffix.lower()),
        )
        rep.embedded = write_chunks(
            conn,
            did,
            parents=[_parent_row(p) for p in result.parents],
            children=[_child_row(c) for c in result.children],
            acl=rep.acl,
            embed=embed,
        )
        rep.parents = len(result.parents)
        rep.children = len(result.children)
    except Exception as exc:  # 单文档失败不阻塞整批
        rep.error = f"{type(exc).__name__}: {exc}"
    return rep


def _parent_row(p: Any) -> dict[str, Any]:
    return {
        "id": p.id,
        "content": p.content,
        "section_path": p.section_path,
        "page_no": p.page_no,
    }


def _child_row(c: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "parent_id": c.parent_id,
        "ord": c.ord,
        "content": c.content,
        "section_path": c.section_path,
        "page_no": c.page_no,
        "token_len": c.token_len,
    }


def ingest_directory(
    dsn: str,
    corpus: Path,
    *,
    embed: bool = True,
    suffixes: tuple[str, ...] = (".md", ".txt", ".pdf"),
) -> list[IngestReport]:
    """把一个目录下的文档全部灌进 PG。返回逐文档结果。"""
    import psycopg

    reports: list[IngestReport] = []
    with psycopg.connect(dsn) as conn:
        for p in sorted(corpus.iterdir()):
            if not p.is_file() or p.suffix.lower() not in suffixes:
                continue
            rep = ingest_path(conn, p, embed=embed)
            conn.commit()  # 每份文档一个事务：失败不影响已成功的
            reports.append(rep)
    return reports
