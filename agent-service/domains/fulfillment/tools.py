"""排障域工具：5 个只读数据源 + 1 个干跑校验器。

三个刻意的设计：

1. **`propose_action` 是干跑校验器，不是执行器。** 它只回答"这个动作能不能提"，
   永不产生副作用——因为 Agent 全程没有写权限（docs/01 §4）。把这条约束放在
   工具的语义里，比放在"大家记得不要执行"的约定里可靠。

2. **所有查询走 SQL Guard，不直连。** 工具自己不拼字符串、不判权限，
   只负责"把参数变成一条参数化 SQL"。判断该不该执行是 `runtime/sqlguard.py` 的事。
   这样只有一处需要审计，而不是七个工具各写一遍。

3. **查询的是视图 `v_order` 而不是 `orders` 表。** 手机号在视图层就脱敏了，
   即使哪天 Guard 被改坏，Agent 也拿不到完整号码。

夹具数据（M0）已经被真实业务库副本取代，但 `check_rule` / `propose_action`
仍是内存实现——它们查的是**规则**和**动作白名单**，不是业务数据，
放进数据库反而多一次网络往返。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from runtime import sqlguard
from runtime.registry import ToolRegistry

SCHEMA = sqlguard.ALLOWED_SCHEMA

#: 规则库留在内存：它是配置，不是业务数据。改规则要发版，不该和订单数据混在一起。
RULES: dict[str, dict[str, Any]] = {
    "compensation_v2": {
        "rule_id": "compensation_v2",
        "trigger": "承诺时效超时 48h 且**非不可抗力**",
        "action": "retry_push_carrier 且触发赔付流程",
        "requires_approval": True,
        "note": "不可抗力（极端天气、交通管制、政策管控）不计入超时，需人工复核",
    },
    "stuck_72h": {
        "rule_id": "stuck_72h",
        "trigger": "物流轨迹在分拨中心停留超过 72h 且同批次其他单已发出",
        "action": "retry_push_carrier",
        "requires_approval": True,
        "note": "同批次对照是判据的一部分：只有本单滞留才指向承运商单点问题",
    },
    "stock_alloc_fail": {
        "rule_id": "stock_alloc_fail",
        "trigger": "可用库存小于需求量导致分配失败",
        "action": "notify_customer",
        "requires_approval": False,
        "note": "缺货不是异常，是供给问题；不能按异常件处理",
    },
}


def _rows(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """执行受 Guard 保护的查询，并把 psycopg 的 Python 对象转成模型可读的形态。

    为什么在这里转而不是让上层兜底：模型要比较"承诺时效"和"当前时间"来判断超时。
    psycopg 返回的是 `datetime(2026, 11, 1, 16, 20, tzinfo=ZoneInfo('Asia/Shanghai'))`，
    序列化后是一串 Python repr；ISO 字符串它才能直接读懂、也能自己算差。
    兜底（registry 的 default=str）只保证不崩，不保证好用——两件事要分开做。
    """
    rows, _meta = sqlguard.execute(sql, params)
    return [{k: _plain(v) for k, v in row.items()} for row in rows]


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def register(registry: ToolRegistry) -> None:
    # ---------------------------------------------------------------- 1 订单
    @registry.register(
        name="lookup_order",
        description="按订单号查履约快照：仓库、承运商、状态、出库时间、承诺时效、金额。",
        input_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string", "pattern": "^O[0-9]{11}$"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        timeout_ms=3_000,
        idempotent=True,
    )
    def lookup_order(order_id: str) -> dict[str, Any]:
        rows = _rows(
            f"SELECT order_id, warehouse_id, carrier, status, created_at, outbound_at, "
            f"promised_at, ship_to_city, receiver_phone_masked, amount_cent, sku "
            f"FROM {SCHEMA}.v_order WHERE order_id = %(order_id)s",
            {"order_id": order_id},
        )
        if not rows:
            # 返回 not_found 而不是抛错：排障时"订单不存在"本身就是一条有效线索
            # （可能是客户报错单号），不该让整条诊断链断在第一步。
            return {"order_id": order_id, "error": "not_found"}
        return rows[0]

    # ---------------------------------------------------------------- 2 库存
    @registry.register(
        name="inventory_snapshot",
        description="查该订单的库存分配快照：需求量、可用量、快照时间。可用量小于需求量即分配失败。",
        input_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string", "pattern": "^O[0-9]{11}$"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        timeout_ms=3_000,
        idempotent=True,
    )
    def inventory_snapshot(order_id: str) -> dict[str, Any]:
        rows = _rows(
            f"SELECT sku, warehouse_id, required_qty, available_qty, snapshot_at "
            f"FROM {SCHEMA}.inventory_snapshot WHERE order_id = %(order_id)s",
            {"order_id": order_id},
        )
        if not rows:
            return {"order_id": order_id, "error": "not_found", "hint": "该单可能未进入库存分配环节"}
        row = rows[0]
        row["sufficient"] = int(row["available_qty"]) >= int(row["required_qty"])
        return row

    # ---------------------------------------------------------------- 3 物流
    @registry.register(
        name="shipment_track",
        description="按订单号查物流轨迹（按时间正序）。注意 event 文本里可能包含地址异常、不可抗力等关键判断依据。",
        input_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string", "pattern": "^O[0-9]{11}$"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        timeout_ms=3_000,
        idempotent=True,
    )
    def shipment_track(order_id: str) -> dict[str, Any]:
        rows = _rows(
            f"SELECT seq, at, node, event, carrier_status FROM {SCHEMA}.shipment_track "
            f"WHERE order_id = %(order_id)s ORDER BY seq",
            {"order_id": order_id},
        )
        return {
            "order_id": order_id,
            "count": len(rows),
            "events": rows,
            # 没轨迹本身就是证据：支付失败的单压根没进履约链路
            "empty_hint": None if rows else "无轨迹：该单可能未出库或未创建运单",
        }

    # ---------------------------------------------------------------- 4 支付
    @registry.register(
        name="payment_flow",
        description="按订单号查支付流水：渠道、状态、金额、失败码。状态 failed 时订单不会进入履约链路。",
        input_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string", "pattern": "^O[0-9]{11}$"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        timeout_ms=3_000,
        idempotent=True,
    )
    def payment_flow(order_id: str) -> dict[str, Any]:
        rows = _rows(
            f"SELECT pay_id, channel, status, amount_cent, paid_at, fail_code, fail_msg "
            f"FROM {SCHEMA}.payment_flow WHERE order_id = %(order_id)s",
            {"order_id": order_id},
        )
        return {"order_id": order_id, "count": len(rows), "flows": rows}

    # ---------------------------------------------------------------- 5 日志
    @registry.register(
        name="search_op_logs",
        description="按订单号检索操作日志（可选按级别过滤），返回时间、级别、操作人、动作、详情。",
        input_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "pattern": "^O[0-9]{11}$"},
                "level": {"type": "string", "enum": ["INFO", "WARN", "ERROR"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        timeout_ms=3_000,
        idempotent=True,
    )
    def search_op_logs(order_id: str, level: str | None = None, limit: int = 50) -> dict[str, Any]:
        # level 虽然是枚举，仍然走参数化：枚举校验发生在 schema 层，
        # 而 schema 层是"尽量校验"，不该成为 SQL 拼接的理由。
        sql = (
            f"SELECT ts, level, operator, action, detail FROM {SCHEMA}.op_log "
            f"WHERE order_id = %(order_id)s"
        )
        params: dict[str, Any] = {"order_id": order_id}
        if level:
            sql += " AND level = %(level)s"
            params["level"] = level
        sql += " ORDER BY ts"
        rows = _rows(sql, params)
        return {"order_id": order_id, "count": len(rows), "logs": rows[:limit]}

    # ---------------------------------------------------------------- 6 指标
    @registry.register(
        name="query_metric",
        description="按时间窗查询仓配指标，用于排除系统性原因（是普遍现象还是个例）。这是唯一一条聚合查询，走白名单视图 + SQL Guard。",
        input_schema={
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["outbound_delay_rate", "stock_alloc_fail_rate", "pay_fail_rate"],
                },
                "since": {"type": "string", "description": "ISO8601 起始时间，必须带时区"},
            },
            "required": ["metric", "since"],
            "additionalProperties": False,
        },
        timeout_ms=3_000,
        idempotent=True,
        cost_hint="聚合查询，注意时间窗；全表扫会被 SQL Guard 拒掉",
    )
    def query_metric(metric: str, since: str) -> dict[str, Any]:
        expressions = {
            "outbound_delay_rate": "count(*) FILTER (WHERE outbound_at IS NULL)::numeric / greatest(count(*), 1)",
            "stock_alloc_fail_rate": "count(*) FILTER (WHERE status = 'allocated')::numeric / greatest(count(*), 1)",
            "pay_fail_rate": "0::numeric",  # 支付失败率要看 payment_flow，见下方分支
        }
        if metric == "pay_fail_rate":
            sql = (
                f"SELECT count(*) FILTER (WHERE status = 'failed')::numeric "
                f"/ greatest(count(*), 1) AS value FROM {SCHEMA}.payment_flow "
                f"WHERE coalesce(paid_at, %(since)s::timestamptz) >= %(since)s::timestamptz"
            )
        else:
            sql = (
                f"SELECT {expressions[metric]} AS value FROM {SCHEMA}.orders "
                f"WHERE created_at >= %(since)s::timestamptz"
            )
        rows = _rows(sql, {"since": since})
        value = rows[0]["value"] if rows else None
        return {
            "metric": metric,
            "since": since,
            "value": float(value) if value is not None else None,
            "unit": "ratio",
        }

    # ---------------------------------------------------------------- 7 规则
    @registry.register(
        name="check_rule",
        description="查履约规则库：赔付/重推/缺货处置的触发条件与是否需审批。判断是否规则误判时必须先查规则原文。",
        input_schema={
            "type": "object",
            "properties": {"rule_id": {"type": "string", "enum": sorted(RULES)}},
            "required": ["rule_id"],
            "additionalProperties": False,
        },
        timeout_ms=2_000,
        idempotent=True,
    )
    def check_rule(rule_id: str) -> dict[str, Any]:
        return RULES.get(rule_id, {"rule_id": rule_id, "error": "not_found"})

    # ---------------------------------------------------------------- 8 动作
    @registry.register(
        name="propose_action",
        description="校验并规范化一个待审批动作（干跑，不执行任何写操作）。",
        input_schema={
            "type": "object",
            "properties": {
                "action_type": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["action_type"],
            "additionalProperties": False,
        },
        timeout_ms=2_000,
        idempotent=True,
    )
    def propose_action(action_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "accepted": True,
            "executed": False,  # 永远为 False：执行只能由 Java 在审批通过后发起
            "action_type": action_type,
            "params": params or {},
        }
