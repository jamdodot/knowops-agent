"""排障域工具（M0 夹具数据）。

一个刻意的设计：`propose_action` 是**干跑校验器**，不是执行器。
它只回答"这个动作能不能提"，永不产生副作用——因为 Agent 全程没有写权限（docs/01 §4）。
把这条约束放在工具的语义里，比放在"大家记得不要执行"的约定里可靠。

夹具里的时间都是固定的，便于断言（测试不该依赖当前时间）。
"""

from __future__ import annotations

from typing import Any

from runtime.registry import ToolRegistry

ORDERS: dict[str, dict[str, Any]] = {
    "O20261102001": {
        "order_id": "O20261102001",
        "warehouse": "WH-WH-01",
        "carrier": "SF",
        "status": "in_transit",
        "outbound_at": "2026-11-01T09:12:00+08:00",
        "promised_at": "2026-11-02T12:00:00+08:00",
        "tracking": [
            {"at": "2026-11-01T09:12:00+08:00", "node": "武汉仓", "event": "已出库交接承运商"},
            {"at": "2026-11-01T18:40:00+08:00", "node": "武汉分拨中心", "event": "到达分拨中心"},
        ],
    }
}

LOGS: list[dict[str, Any]] = [
    {
        "ts": "2026-11-01T18:40:00+08:00",
        "level": "INFO",
        "msg": "SF 武汉分拨中心 到达扫描，同批次 37 单",
    },
    {
        "ts": "2026-11-02T09:05:00+08:00",
        "level": "WARN",
        "msg": "SF 武汉分拨中心 滞留告警：本单 72h 无新轨迹，同批次 36 单已发出",
    },
    {
        "ts": "2026-11-02T09:06:00+08:00",
        "level": "INFO",
        "msg": "出库复核通过，拣货与打包无异常",
    },
]


def register(registry: ToolRegistry) -> None:
    @registry.register(
        name="lookup_order",
        description="按订单号查履约快照：出库时间、承运商、物流轨迹、承诺时效。",
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
        order = ORDERS.get(order_id)
        if order is None:
            return {"order_id": order_id, "error": "not_found"}
        return order

    @registry.register(
        name="query_metric",
        description="查询仓配指标（按名称与时间窗），用于排除库存/时效类的系统性原因。",
        input_schema={
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": ["inventory_diff_rate", "outbound_delay_rate"]},
                "window": {"type": "string"},
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
        timeout_ms=3_000,
        idempotent=True,
    )
    def query_metric(metric: str, window: str = "2026-11-01") -> dict[str, Any]:
        values = {"inventory_diff_rate": 0.0008, "outbound_delay_rate": 0.012}
        return {"metric": metric, "window": window, "value": values.get(metric), "unit": "ratio"}

    @registry.register(
        name="search_logs",
        description="按关键词检索履约日志，返回时间、级别、消息。用于确认是否存在滞留/异常告警。",
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["keyword"],
            "additionalProperties": False,
        },
        timeout_ms=3_000,
        idempotent=True,
        cost_hint="M1 起为日志平台查询，注意时间窗",
    )
    def search_logs(keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        hits = [row for row in LOGS if keyword in row["msg"] or keyword in row["ts"]]
        return hits[:limit]

    @registry.register(
        name="check_rule",
        description="查履约规则库中的规则定义（赔付、重推的触发条件）。",
        input_schema={
            "type": "object",
            "properties": {"rule_id": {"type": "string"}},
            "required": ["rule_id"],
            "additionalProperties": False,
        },
        timeout_ms=2_000,
        idempotent=True,
    )
    def check_rule(rule_id: str) -> dict[str, Any]:
        rules = {
            "compensation_v2": {
                "rule_id": "compensation_v2",
                "trigger": "承诺时效超时 48h 且非不可抗力",
                "action": "retry_push_carrier 且触发赔付流程",
                "requires_approval": True,
            }
        }
        return rules.get(rule_id, {"rule_id": rule_id, "error": "not_found"})

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
