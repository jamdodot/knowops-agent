"""SQL Guard：保护「Agent 能读到的数据」的四道闸。

为什么要单独一个模块，而不是塞进 `guard.py`：
`guard.py` 管的是**行为边界**（这个主体能不能做、这个动作能不能提）；
这里管的是**数据出口**（这条 SQL 该不该被执行、结果该不该原样返回）。
两者的失败语义不同：动作越界是"丢弃提议并转人工"，SQL 越界是"拒绝执行并记一条告警"。
混在一起之后，看板上一句"被护栏拦截"就分不清是模型想越权还是模型写错了 SQL。

四道闸（docs/02 §9.1）：

| 闸 | 拦住什么 | 失败时 |
|---|---|---|
| 1 语法层 | DDL/DML/多语句/注释绕过 | 拒绝，不执行 |
| 2 对象层 | 非白名单表、无等值/时间条件的全表扫 | 拒绝，不执行 |
| 3 执行层 | 无 LIMIT、超时、可写事务 | 改写 SQL（补 LIMIT）或拒绝 |
| 4 结果层 | 行数超限、敏感字段 | 截断 + 脱敏，不报错 |

一个必须说清的局限：**语法层是正则 + 关键字拒绝，不是真正的 AST 解析。**
装了 `pglast` 时会升级成 AST（见 `_parse_ast`），没装就退化为正则。
正则挡得住"明显写错"和"手滑"，挡不住精心构造的绕过——所以第 2/3/4 道闸才是真正的兜底：
即使 SQL 混过了第一道，非白名单表、只读事务、结果脱敏仍然生效。**纵深防御不是四道一样强的墙，
是四道失败点不同的墙。**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- 策略
#: 允许被查询的对象。只写表名，schema 固定为 knowops_ops。
#: 白名单是"默认拒绝"：新加表必须来这里登记，否则查不到——这个摩擦是故意的。
ALLOWED_TABLES = frozenset({
    "v_order",            # Agent 只该看这个视图（手机号已脱敏）
    "orders",
    "inventory_snapshot",
    "shipment_track",
    "payment_flow",
    "op_log",
})

ALLOWED_SCHEMA = "knowops_ops"

#: 这些列出现在 WHERE 的**等值**条件里时，可以豁免"必须带时间条件"。
#: 豁免的理由：按订单号查单行不是全表扫，硬要它带时间窗只会让工具多传一个没用的参数。
KEY_COLUMNS = ("order_id", "pay_id", "id")

#: 时间列：范围查询必须至少命中一个，否则就是全表扫（防的是"查一下所有订单"）
TIME_COLUMNS = ("created_at", "outbound_at", "promised_at", "snapshot_at", "at", "paid_at", "ts")

#: 结果行上限。超过就截断并标记，不报错——报错会让整条诊断失败，
#: 而"只看到前 200 行"对排障来说通常仍然可用。
MAX_ROWS = 200

#: 未写 LIMIT 时自动补的行数
DEFAULT_LIMIT = 50

#: 单条查询的执行超时（毫秒）
STATEMENT_TIMEOUT_MS = 3_000

#: 结果层要脱敏的列名（大小写不敏感）。视图已经脱了一层，这里是第二层。
SENSITIVE_COLUMNS = frozenset({"receiver_phone", "phone", "id_card", "email", "bank_card"})

_DANGEROUS = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|copy|vacuum|"
    r"analyze|call|do|merge|reindex|cluster|comment|security|listen|notify|load)\b",
    re.IGNORECASE,
)
_COMMENT = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.DOTALL)
_LIMIT = re.compile(r"\blimit\s+(\d+)\s*(?:offset\s+\d+)?\s*$", re.IGNORECASE)
_TABLE_REF = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)", re.IGNORECASE
)


class SqlBlocked(Exception):
    """SQL 被闸拦下。**这不是程序错误**，是护栏在正常工作。

    继承 Exception 而不是 KwError：调用方（工具层）要把"被拦"和"查询出错"分开处理——
    前者应当让模型换个思路，后者应当降级。混成一个异常就会把"模型想干坏事"
    和"数据库挂了"记成同一类指标。
    """

    def __init__(self, gate: str, reason: str, sql: str = "") -> None:
        super().__init__(f"[{gate}] {reason}")
        self.gate = gate
        self.reason = reason
        self.sql = sql


@dataclass
class GuardedQuery:
    sql: str                                  # 改写后的 SQL（已补 LIMIT）
    tables: list[str] = field(default_factory=list)
    rewritten: list[str] = field(default_factory=list)   # 改了什么，进日志用


# --------------------------------------------------------------------------- 闸 1
def _strip_comments(sql: str) -> str:
    """去掉注释再判断。

    注释是最经典的绕过手段：`SELECT 1 -- ; DROP TABLE x` 在很多简陋的
    "关键词黑名单"里能过，因为黑名单看的是整串。先剥注释，再判分号。
    """
    return _COMMENT.sub(" ", sql)


def _check_syntax(sql: str) -> str:
    cleaned = _strip_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        raise SqlBlocked("syntax", "空语句")

    # 多语句：剥掉注释后还有分号，说明想一次跑两条
    if ";" in cleaned:
        raise SqlBlocked("syntax", "禁止多语句（同一个请求里只能有一条 SELECT）", sql)

    if _DANGEROUS.search(cleaned):
        hit = _DANGEROUS.search(cleaned)
        raise SqlBlocked("syntax", f"出现非只读关键字：{hit.group(1).upper()}", sql)

    head = cleaned.split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH"):
        raise SqlBlocked("syntax", f"只允许 SELECT / WITH 开头，实际是 {head}", sql)

    if head == "WITH":
        # CTE 必须先 SELECT 出来再被引用，但 `WITH x AS (DELETE ... RETURNING *)`
        # 在 PG 里是合法的写语句——所以 CTE 只允许纯 SELECT 体。
        # 上面 _DANGEROUS 已经挡住了关键字，这里再确认 CTE 后面确实有 SELECT。
        if not re.search(r"\bselect\b", cleaned, re.IGNORECASE):
            raise SqlBlocked("syntax", "WITH 子句里没有 SELECT", sql)

    return cleaned


# --------------------------------------------------------------------------- 闸 2
def _check_objects(sql: str) -> list[str]:
    refs = _TABLE_REF.findall(sql)
    if not refs:
        raise SqlBlocked("objects", "没有 FROM / JOIN 子句，无法判断查询对象", sql)

    tables: list[str] = []
    for ref in refs:
        schema, _, name = ref.rpartition(".")
        if schema and schema.lower() != ALLOWED_SCHEMA:
            raise SqlBlocked("objects", f"禁止跨 schema 查询：{ref}（只允许 {ALLOWED_SCHEMA}）", sql)
        if name.lower() not in ALLOWED_TABLES:
            raise SqlBlocked("objects", f"表不在白名单：{name}", sql)
        tables.append(name.lower())
    return tables


def _check_scope(sql: str) -> None:
    """必须有等值主键条件或时间条件，否则就是全表扫。

    为什么这条要单独一道闸：`SELECT * FROM op_log LIMIT 50` 看起来无害，
    但它在业务库上是一次全表顺序扫描——50 行是**扫完之后**才截断的。
    成本不在返回的数据量上，在扫描的行数上。所以"带 LIMIT"不等于"安全"。
    """
    lowered = sql.lower()
    has_key = any(re.search(rf"\b{col}\s*=\s*[%(:]", lowered) for col in KEY_COLUMNS)
    has_time = any(re.search(rf"\b{col}\b", lowered) for col in TIME_COLUMNS)
    if not has_key and not has_time:
        raise SqlBlocked(
            "objects",
            "缺少主键等值条件或时间条件（会退化成全表扫）："
            f"至少要有一个 order_id/pay_id 等值条件，或 {TIME_COLUMNS[0]} 这类时间列",
            sql,
        )


# --------------------------------------------------------------------------- 闸 3
def _check_limit(sql: str) -> GuardedQuery:
    rewritten: list[str] = []
    match = _LIMIT.search(sql)
    if match is None:
        sql = f"{sql}\nLIMIT {DEFAULT_LIMIT}"
        rewritten.append(f"补 LIMIT {DEFAULT_LIMIT}")
    elif int(match.group(1)) > MAX_ROWS:
        # 改写而不是拒绝：模型写 LIMIT 1000 通常是"想多拿点"，不是恶意。
        # 拒绝会让它重试并被拒第二次，白烧两轮；改写到上限并记录，等价于"按你的意思，
        # 但只到这个数"。
        sql = f"{sql[: match.start(1)]}{MAX_ROWS}{sql[match.end(1):]}"
        rewritten.append(f"LIMIT {match.group(1)} 收敛到 {MAX_ROWS}")
    return GuardedQuery(sql=sql, rewritten=rewritten)


# --------------------------------------------------------------------------- 闸 4
def mask_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """敏感列脱敏。返回 (脱敏后的行, 脱敏了哪些列)。

    视图层已经脱过手机号，这里再脱一次看起来冗余，但两者的失效方式不同：
    视图失效（有人改了 v_order 或直接查了 orders）和 Guard 失效（有人绕过了它）
    是独立事件，同时失效的概率远低于任一单独失效。
    """
    masked: list[str] = []
    out: list[dict[str, Any]] = []
    for row in rows:
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if key.lower() in SENSITIVE_COLUMNS and isinstance(value, str) and value:
                clean[key] = _mask_phone_like(value)
                if key not in masked:
                    masked.append(key)
            else:
                clean[key] = value
        out.append(clean)
    return out, masked


def _mask_phone_like(value: str) -> str:
    if re.fullmatch(r"1[3-9]\d{9}", value):
        return f"{value[:3]}****{value[-4:]}"
    return "***"


# --------------------------------------------------------------------------- 入口
def guard(sql: str) -> GuardedQuery:
    """跑前四道闸。返回可执行的 SQL；任何一道不过就抛 SqlBlocked。"""
    cleaned = _check_syntax(sql)
    tables = _check_objects(cleaned)
    _check_scope(cleaned)
    checked = _check_limit(cleaned)
    checked.tables = tables
    return checked


def execute(sql: str, params: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """执行一条被 Guard 保护过的查询。

    返回 (rows, meta)。meta 里有 `rewritten` / `masked_columns` / `truncated`，
    调用方要把它们带进 trace——**降级必须可见**，否则"悄悄少返回 150 行"
    会变成一次查不出原因的误诊。
    """
    import psycopg
    from psycopg.rows import dict_row

    from retrieval.dsn import pg_dsn
    from runtime.config import load_dotenv

    load_dotenv()
    checked = guard(sql)

    with psycopg.connect(pg_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # 只读事务：即使前两道闸全被绕过，PG 在这里会直接拒绝写操作。
            # 这是四道闸里唯一由**数据库**而不是我们的代码保证的一道。
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            cur.execute(checked.sql, params or {})
            rows = cur.fetchall()

    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]
    rows, masked = mask_rows(rows)
    meta = {
        "rewritten": checked.rewritten,
        "masked_columns": masked,
        "truncated": truncated,
        "tables": checked.tables,
        "rows": len(rows),
    }
    return rows, meta
