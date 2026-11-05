"""SQL Guard 四道闸的回归测试。

这些测试的写法有个刻意的选择：**每条危险 SQL 都断言具体是哪道闸拦的**，
而不只是断言"抛异常了"。因为四道闸的职责不同，如果 `DROP TABLE` 被
"缺少时间条件"拦掉，说明语法层漏了——测试全绿但闸是坏的。
"""

from __future__ import annotations

import pytest

from runtime.sqlguard import (
    ALLOWED_SCHEMA,
    DEFAULT_LIMIT,
    MAX_ROWS,
    SqlBlocked,
    guard,
    mask_rows,
)

ORDERS_Q = f"SELECT order_id, status FROM {ALLOWED_SCHEMA}.v_order WHERE order_id = %(oid)s"


# --------------------------------------------------------------------- 闸 1 语法
@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE knowops_ops.orders",
        "UPDATE knowops_ops.orders SET amount_cent = 0 WHERE order_id = 'x'",
        "DELETE FROM knowops_ops.orders WHERE order_id = 'x'",
        "TRUNCATE knowops_ops.op_log",
        "CREATE TABLE t (a int)",
        "GRANT ALL ON knowops_ops.orders TO PUBLIC",
    ],
)
def test_write_statements_are_blocked_at_syntax_gate(sql: str) -> None:
    with pytest.raises(SqlBlocked) as exc:
        guard(sql)
    assert exc.value.gate == "syntax"


def test_multi_statement_is_blocked() -> None:
    # 多语句是最常见的注入形态：前半句合法，后半句要命
    with pytest.raises(SqlBlocked) as exc:
        guard("SELECT * FROM knowops_ops.v_order WHERE order_id = 'x'; DELETE FROM knowops_ops.orders")
    assert exc.value.gate == "syntax"
    assert "多语句" in exc.value.reason


def test_comment_cannot_hide_a_second_statement() -> None:
    """注释绕过：先剥注释再判分号，否则 `-- ;` 这类写法会让黑名单失效。"""
    with pytest.raises(SqlBlocked) as exc:
        guard("SELECT * FROM knowops_ops.v_order WHERE order_id='x' -- harmless\n; DROP TABLE x")
    assert exc.value.gate in ("syntax", "objects")


def test_cte_must_be_select_only() -> None:
    with pytest.raises(SqlBlocked) as exc:
        guard("WITH gone AS (DELETE FROM knowops_ops.orders RETURNING *) SELECT * FROM gone")
    assert exc.value.gate == "syntax"


def test_plain_select_passes() -> None:
    out = guard(ORDERS_Q)
    assert "LIMIT" in out.sql.upper()


# --------------------------------------------------------------------- 闸 2 对象
def test_table_outside_whitelist_is_blocked() -> None:
    with pytest.raises(SqlBlocked) as exc:
        guard("SELECT usename FROM pg_catalog.pg_user WHERE usename = 'x'")
    assert exc.value.gate == "objects"


def test_cross_schema_is_blocked() -> None:
    with pytest.raises(SqlBlocked) as exc:
        guard("SELECT * FROM knowops_read.kb_doc WHERE doc_id = %(d)s")
    assert exc.value.gate == "objects"
    assert "跨 schema" in exc.value.reason


def test_full_table_scan_is_blocked() -> None:
    """只带 LIMIT 不等于安全：LIMIT 是扫完之后才截断的。"""
    with pytest.raises(SqlBlocked) as exc:
        guard("SELECT * FROM knowops_ops.op_log LIMIT 50")
    assert exc.value.gate == "objects"
    assert "全表扫" in exc.value.reason


def test_time_range_query_is_allowed_without_key() -> None:
    # 聚合/区间查询没有 order_id 是正常的，只要有时间窗
    guard("SELECT action, count(*) FROM knowops_ops.op_log WHERE ts >= %(since)s GROUP BY action")


# --------------------------------------------------------------------- 闸 3 执行
def test_missing_limit_is_appended() -> None:
    out = guard(ORDERS_Q)
    assert out.sql.upper().rstrip().endswith(f"LIMIT {DEFAULT_LIMIT}")
    assert any("补 LIMIT" in r for r in out.rewritten)


def test_oversized_limit_is_clamped_not_rejected() -> None:
    """改写而不是拒绝：模型写 1000 是"想多拿点"，不是恶意；拒绝只会让它重试一次再被拒。"""
    out = guard(f"{ORDERS_Q} LIMIT 1000")
    assert f"LIMIT {MAX_ROWS}" in out.sql
    assert "1000" not in out.sql


def test_limit_within_bound_is_kept() -> None:
    out = guard(f"{ORDERS_Q} LIMIT 5")
    assert out.rewritten == []
    assert "LIMIT 5" in out.sql


# --------------------------------------------------------------------- 闸 4 结果
def test_sensitive_columns_are_masked() -> None:
    rows, masked = mask_rows(
        [
            {"order_id": "O1", "receiver_phone": "13800138000"},
            {"order_id": "O2", "receiver_phone": "13900139000"},
        ]
    )
    assert rows[0]["receiver_phone"] == "138****8000"
    assert rows[1]["receiver_phone"] == "139****9000"
    assert masked == ["receiver_phone"]


def test_non_sensitive_columns_untouched() -> None:
    rows, masked = mask_rows([{"order_id": "O1", "status": "in_transit"}])
    assert rows[0]["status"] == "in_transit"
    assert masked == []
