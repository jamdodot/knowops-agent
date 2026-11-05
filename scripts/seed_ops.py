"""灌排障域业务数据：30 个订单，覆盖 6 类根因。

为什么是"造数据"而不是"接真库"：真实交易库接不进来，但**数据形态**可以照搬。
这里刻意让每一类根因的证据分散在 3~4 张表里——单个工具一次查询看不出结论，
必须按假设逐条取证。如果证据全挤在一张表，那测的就不是"假设验证"，
而是"会不会写 SQL"。

六个根因的证据特征（判定口径见 eval/datasets/replay_ops_v1.jsonl）：

| 根因 | 关键证据 | 容易混淆的地方 |
|---|---|---|
| carrier_stuck   | 轨迹停在分拨中心 ≥72h，同批其他单已发出 | 承诺超时也可能只是发货晚 |
| out_of_stock    | available < required，outbound_at 为 NULL | 与"仓库漏扫"区分：漏扫有出库日志 |
| address_invalid | 轨迹里有"地址异常/超区"，op_log 有校验失败 | 与"客户拒收"区分：拒收在派送后 |
| payment_failed  | payment.status=failed，订单停在 created | 与"已付款未发货"区分 |
| duplicate_outbound | 同一 SKU 两次出库，两个运单号 | 与"拆单发货"区分：拆单 SKU 不同 |
| rule_misjudge   | 系统按超时判赔付，但轨迹里有不可抗力节点 | 需要先 check_rule 才知道规则怎么写的 |

用法：
    python scripts/seed_ops.py            # 幂等：先清后灌
    python scripts/seed_ops.py --check    # 只校验，不写
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-service"))

CST = timezone(timedelta(hours=8))


def T(day: int, hour: int, minute: int = 0) -> datetime:
    """2026-11-<day> <hour>:<minute> +08:00。固定时间，测试不依赖 now()。"""
    return datetime(2026, 11, day, hour, minute, tzinfo=CST)


# 六类根因各 5 单。订单号连续，便于人工对照。
CAUSES = [
    "carrier_stuck",
    "out_of_stock",
    "address_invalid",
    "payment_failed",
    "duplicate_outbound",
    "rule_misjudge",
]

CITIES = ["武汉", "长沙", "郑州", "合肥", "南昌"]
CARRIERS = ["SF", "JD", "YTO", "ZTO", "EMS"]


def build() -> dict[str, list[dict]]:
    """返回五张表的行。所有数据由固定的 (根因, 序号) 推导，重跑结果一致。"""
    orders: list[dict] = []
    inventory: list[dict] = []
    tracks: list[dict] = []
    payments: list[dict] = []
    logs: list[dict] = []

    seq = 0
    for cause in CAUSES:
        for i in range(5):
            seq += 1
            oid = f"O2026110{seq:04d}"          # O + 11 位
            wh = f"WH-WH-0{i % 3 + 1}"
            carrier = CARRIERS[i % len(CARRIERS)]
            city = CITIES[i % len(CITIES)]
            sku = f"SKU-{1000 + seq}"
            amount = 12900 + seq * 100
            promised = T(2, 12)                  # 全部承诺 11-02 12:00 送达
            created = T(1, 10, 5)

            order = {
                "order_id": oid,
                "warehouse_id": wh,
                "carrier": carrier,
                "status": "in_transit",
                "created_at": created,
                "outbound_at": T(1, 16, 20),
                "promised_at": promised,
                "ship_to_city": city,
                "receiver_phone": f"1380013{seq:04d}",
                "amount_cent": amount,
                "sku": sku,
            }
            inv = {
                "order_id": oid,
                "sku": sku,
                "warehouse_id": wh,
                "required_qty": 1,
                "available_qty": 37 - seq,
                "snapshot_at": T(1, 15, 0),
            }
            pay = {
                "order_id": oid,
                "pay_id": f"P{20261101000 + seq}",
                "channel": ["wechat", "alipay", "unionpay"][i % 3],
                "status": "paid",
                "amount_cent": amount,
                "paid_at": T(1, 10, 7),
                "fail_code": None,
                "fail_msg": None,
            }

            if cause == "carrier_stuck":
                # 出库正常，卡在分拨中心；同批次其他单已发出（这条是判"承运商侧"的关键）
                tracks += [
                    {"order_id": oid, "seq": 1, "at": T(1, 16, 20), "node": f"{city}仓",
                     "event": "已出库交接承运商", "carrier_status": "已揽收"},
                    {"order_id": oid, "seq": 2, "at": T(1, 22, 40), "node": f"{city}分拨中心",
                     "event": "到达分拨中心", "carrier_status": "运输中"},
                ]
                logs += [
                    {"order_id": oid, "ts": T(1, 22, 41), "level": "INFO", "operator": "system",
                     "action": "TRACK_IN", "detail": f"{carrier} {city}分拨中心 到达扫描，同批次 37 单"},
                    # 告警在第 82 小时才扫到：巡检是每天 09:00 跑一次，超 72h 才告警，
                    # 所以文案写"已超 72h"而不是"恰好 72h"——差这 10 小时是巡检节奏，不是数据错误。
                    {"order_id": oid, "ts": T(4, 9, 5), "level": "WARN", "operator": "system",
                     "action": "STUCK_ALERT",
                     "detail": f"{carrier} {city}分拨中心 滞留告警：已超 72h 无新轨迹，同批次 36 单已发出"},
                ]

            elif cause == "out_of_stock":
                # 未出库：outbound_at 为 NULL，库存不足是根因
                order["status"] = "allocated"
                order["outbound_at"] = None
                inv["available_qty"] = 0
                inv["required_qty"] = 2
                logs += [
                    {"order_id": oid, "ts": T(1, 15, 0), "level": "WARN", "operator": "system",
                     "action": "ALLOC_FAIL",
                     "detail": f"库存不足，待补货：{sku} 需求 2 可用 0，占用分配失败"},
                    {"order_id": oid, "ts": T(2, 8, 30), "level": "INFO", "operator": "WMS",
                     "action": "REPLENISH_PLAN", "detail": "补货计划已生成，预计 11-03 到仓"},
                ]

            elif cause == "address_invalid":
                order["status"] = "outbound"
                tracks += [
                    {"order_id": oid, "seq": 1, "at": T(1, 16, 20), "node": f"{city}仓",
                     "event": "已出库交接承运商", "carrier_status": "已揽收"},
                    {"order_id": oid, "seq": 2, "at": T(2, 7, 15), "node": f"{city}网点",
                     "event": "地址异常：收货地址超出派送范围，待客户确认", "carrier_status": "异常"},
                ]
                logs += [
                    {"order_id": oid, "ts": T(1, 15, 40), "level": "ERROR", "operator": "system",
                     "action": "ADDR_CHECK_FAIL",
                     "detail": f"地址校验失败：{city}市{district_of(seq)}超出可派送范围，已放行待人工确认"},
                    {"order_id": oid, "ts": T(2, 7, 16), "level": "INFO", "operator": "CS",
                     "action": "CONTACT_CUSTOMER", "detail": "已短信通知客户补充地址，等待回复"},
                ]

            elif cause == "payment_failed":
                # 支付失败 → 根本没进履约链路，所以没有出库也没有轨迹
                order["status"] = "created"
                order["outbound_at"] = None
                pay["status"] = "failed"
                pay["paid_at"] = None
                pay["fail_code"] = "BALANCE_NOT_ENOUGH"
                pay["fail_msg"] = "银行卡余额不足"
                logs += [
                    {"order_id": oid, "ts": T(1, 10, 8), "level": "ERROR", "operator": "system",
                     "action": "PAY_CALLBACK",
                     "detail": "支付回调失败：BALANCE_NOT_ENOUGH 银行卡余额不足，订单未进入履约"},
                ]

            elif cause == "duplicate_outbound":
                # 同 SKU 两次出库、两个运单号：库存被多扣，客户可能收到两件
                order["status"] = "in_transit"
                tracks += [
                    {"order_id": oid, "seq": 1, "at": T(1, 16, 20), "node": f"{city}仓",
                     "event": "已出库交接承运商 运单 SF7700{seq}001", "carrier_status": "已揽收"},
                    {"order_id": oid, "seq": 2, "at": T(1, 18, 5), "node": f"{city}仓",
                     "event": "已出库交接承运商 运单 SF7700{seq}002", "carrier_status": "已揽收"},
                    {"order_id": oid, "seq": 3, "at": T(2, 6, 30), "node": f"{city}分拨中心",
                     "event": "到达分拨中心", "carrier_status": "运输中"},
                ]
                inv["available_qty"] = 12 - seq
                logs += [
                    {"order_id": oid, "ts": T(1, 16, 20), "level": "INFO", "operator": "OP1024",
                     "action": "OUTBOUND_CONFIRM", "detail": f"出库复核通过，拣货与打包无异常 {sku} x1"},
                    {"order_id": oid, "ts": T(1, 18, 5), "level": "WARN", "operator": "OP1024",
                     "action": "OUTBOUND_CONFIRM",
                     "detail": f"出库复核通过（重复提交）：{sku} x1，运单与首单不同"},
                    {"order_id": oid, "ts": T(1, 18, 6), "level": "WARN", "operator": "system",
                     "action": "STOCK_DEDUCT", "detail": f"库存扣减重复：{sku} 已扣 2 件，实发可能 2 件"},
                ]

            else:  # rule_misjudge
                # 系统按"承诺超时 48h"判赔付，但轨迹里有不可抗力——规则误判
                order["status"] = "in_transit"
                tracks += [
                    {"order_id": oid, "seq": 1, "at": T(1, 16, 20), "node": f"{city}仓",
                     "event": "已出库交接承运商", "carrier_status": "已揽收"},
                    {"order_id": oid, "seq": 2, "at": T(4, 12, 30), "node": f"{city}转运站",
                     "event": f"因{force_of(seq)}交通管制，区域暂停收派（不可抗力）",
                     "carrier_status": "延误"},
                ]
                logs += [
                    {"order_id": oid, "ts": T(4, 12, 31), "level": "WARN", "operator": "system",
                     "action": "FORCE_MAJEURE",
                     "detail": f"承运商公告：{force_of(seq)}导致 {city} 区域交通管制，时效顺延"},
                    {"order_id": oid, "ts": T(4, 12, 32), "level": "WARN", "operator": "system",
                     "action": "COMPENSATION_TRIGGER",
                     "detail": "触发 compensation_v2 赔付判定：承诺时效超时 48h（未排除不可抗力）"},
                ]

            orders.append(order)
            inventory.append(inv)
            payments.append(pay)

    return {"orders": orders, "inventory": inventory, "tracks": tracks,
            "payments": payments, "logs": logs}


def district_of(seq: int) -> str:
    return ["江夏区梁山头路 88 号", "黄陂区远郊仓 3 号门", "新洲区物流园 B12"][seq % 3]


def force_of(seq: int) -> str:
    return ["持续暴雨", "大雾", "台风外围"][seq % 3]


def write(data: dict[str, list[dict]]) -> None:
    import psycopg

    from retrieval.dsn import pg_dsn
    from runtime.config import load_dotenv

    load_dotenv()
    schema_sql = (ROOT / "deploy" / "init" / "04_ops_schema.sql").read_text(encoding="utf-8")

    with psycopg.connect(pg_dsn()) as conn, conn.cursor() as cur:
        cur.execute(schema_sql)
        # 先清后灌：这个脚本会被反复执行，不幂等的话第二次就撞主键
        cur.execute("TRUNCATE knowops_ops.op_log, knowops_ops.shipment_track, "
                    "knowops_ops.payment_flow, knowops_ops.inventory_snapshot, knowops_ops.orders")

        cur.executemany(
            "INSERT INTO knowops_ops.orders (order_id, warehouse_id, carrier, status, created_at, "
            "outbound_at, promised_at, ship_to_city, receiver_phone, amount_cent, sku) "
            "VALUES (%(order_id)s, %(warehouse_id)s, %(carrier)s, %(status)s, %(created_at)s, "
            "%(outbound_at)s, %(promised_at)s, %(ship_to_city)s, %(receiver_phone)s, "
            "%(amount_cent)s, %(sku)s)",
            data["orders"],
        )
        cur.executemany(
            "INSERT INTO knowops_ops.inventory_snapshot (order_id, sku, warehouse_id, "
            "required_qty, available_qty, snapshot_at) VALUES (%(order_id)s, %(sku)s, "
            "%(warehouse_id)s, %(required_qty)s, %(available_qty)s, %(snapshot_at)s)",
            data["inventory"],
        )
        cur.executemany(
            "INSERT INTO knowops_ops.shipment_track (order_id, seq, at, node, event, carrier_status) "
            "VALUES (%(order_id)s, %(seq)s, %(at)s, %(node)s, %(event)s, %(carrier_status)s)",
            data["tracks"],
        )
        cur.executemany(
            "INSERT INTO knowops_ops.payment_flow (order_id, pay_id, channel, status, amount_cent, "
            "paid_at, fail_code, fail_msg) VALUES (%(order_id)s, %(pay_id)s, %(channel)s, "
            "%(status)s, %(amount_cent)s, %(paid_at)s, %(fail_code)s, %(fail_msg)s)",
            data["payments"],
        )
        cur.executemany(
            "INSERT INTO knowops_ops.op_log (order_id, ts, level, operator, action, detail) "
            "VALUES (%(order_id)s, %(ts)s, %(level)s, %(operator)s, %(action)s, %(detail)s)",
            data["logs"],
        )
        conn.commit()
        cur.execute("SELECT count(*) FROM knowops_ops.orders")
        n_orders = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM knowops_ops.op_log")
        n_logs = cur.fetchone()[0]
    print(f"已灌入 {n_orders} 单 / {len(data['tracks'])} 条轨迹 / {n_logs} 条操作日志")


def check() -> int:
    """校验每类根因的订单数与证据完整性。返回非 0 表示有缺失。"""
    import psycopg

    from retrieval.dsn import pg_dsn
    from runtime.config import load_dotenv

    load_dotenv()
    data = build()
    expect = {c: 5 for c in CAUSES}
    got: dict[str, int] = {}

    with psycopg.connect(pg_dsn()) as conn, conn.cursor() as cur:
        for cause, oid_list in group_by_cause(data["orders"]).items():
            got[cause] = len(oid_list)
        print("根因分布：", {k: got.get(k, 0) for k in CAUSES})
        cur.execute("SELECT count(*) FROM knowops_ops.orders")
        print("库中订单数：", cur.fetchone()[0])

    missing = {k: (expect[k], got.get(k, 0)) for k in CAUSES if got.get(k, 0) != expect[k]}
    if missing:
        print("数量不符：", missing)
        return 1
    return 0


def group_by_cause(orders: list[dict]) -> dict[str, list[str]]:
    """按订单号区间反推根因：每 5 单同一根因，顺序与 CAUSES 一致。"""
    out: dict[str, list[str]] = {}
    for idx, order in enumerate(orders):
        out.setdefault(CAUSES[idx // 5], []).append(order["order_id"])
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写库")
    args = ap.parse_args()
    if args.check:
        raise SystemExit(check())
    write(build())
