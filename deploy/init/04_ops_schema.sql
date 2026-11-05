-- =============================================================================
-- KnowOps Agent — 排障域业务库（knowops_ops）
-- =============================================================================
-- 依据：docs/02 §9.1（只读数据源 + SQL Guard 四道闸）、docs/15 阶段 B2/B3
--
-- 与 knowops_read 的区别，别混：
--   knowops_read 是**我们自己**的派生物（文档 chunk、向量、BM25），可以随便重建。
--   knowops_ops  是**别人的**业务库的只读副本（订单/库存/物流/支付/日志），
--                真相源在交易系统，我们只读、永不写。这正是它值得被 SQL Guard 保护的原因：
--                一旦 Agent 有机会在这里跑 UPDATE，事故等级和"改生产库"是一样的。
--
-- 三条不写就会出事的设计：
--   1. 所有时间列带时区（timestamptz）。物流轨迹是跨时区的，naive 时间会在
--      "72 小时滞留"这种判定上算出 8 小时误差，把正常单判成滞留。
--   2. receiver_phone 存明文但在**视图层**暴露脱敏版。表用于对账，视图给 Agent 用。
--      把脱敏放在 SQL Guard 的结果层做也行，但那意味着"忘了走 Guard 就漏"；
--      视图是默认安全的，Guard 是最后一道，两道都要有（纵深防御）。
--   3. 不建外键。理由同 01_schema.sql：跨源副本的外键会在同步窗口里报错，
--      而且业务库本来就没有这些约束。
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS knowops_ops;

-- ---------------------------------------------------------------------------
-- 1. 订单主表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowops_ops.orders (
    order_id        text PRIMARY KEY,
    warehouse_id    text NOT NULL,
    carrier         text NOT NULL,
    status          text NOT NULL,          -- created / allocated / outbound / in_transit / delivered / cancelled
    created_at      timestamptz NOT NULL,
    outbound_at     timestamptz,            -- 未出库为 NULL：这正是"缺货未发"的证据
    promised_at     timestamptz NOT NULL,   -- 对客承诺时效
    ship_to_city    text NOT NULL,
    receiver_phone  text NOT NULL,          -- 敏感字段：只经视图暴露脱敏版
    amount_cent     integer NOT NULL,
    sku             text NOT NULL
);

-- ---------------------------------------------------------------------------
-- 2. 库存快照
-- ---------------------------------------------------------------------------
-- 为什么按订单存而不是按 SKU 存：排障要回答的是"**这一单**为什么没发出去"，
-- 不是"这个 SKU 还有多少货"。按 SKU 存会让每次诊断都缺一个 join 条件，
-- 而那个 join 一旦漏掉，就会拿别的仓的库存当证据。
CREATE TABLE IF NOT EXISTS knowops_ops.inventory_snapshot (
    order_id        text NOT NULL,
    sku             text NOT NULL,
    warehouse_id    text NOT NULL,
    required_qty    integer NOT NULL,
    available_qty   integer NOT NULL,
    snapshot_at     timestamptz NOT NULL,
    PRIMARY KEY (order_id, sku)
);

-- ---------------------------------------------------------------------------
-- 3. 物流轨迹
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowops_ops.shipment_track (
    order_id        text NOT NULL,
    seq             integer NOT NULL,
    at              timestamptz NOT NULL,
    node            text NOT NULL,
    event           text NOT NULL,
    carrier_status  text NOT NULL,          -- 承运商口径状态，与 orders.status 不一定一致
    PRIMARY KEY (order_id, seq)
);

-- ---------------------------------------------------------------------------
-- 4. 支付流水
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowops_ops.payment_flow (
    order_id        text NOT NULL,
    pay_id          text NOT NULL,
    channel         text NOT NULL,          -- wechat / alipay / unionpay
    status          text NOT NULL,          -- paid / failed / refunded / pending
    amount_cent     integer NOT NULL,
    paid_at         timestamptz,
    fail_code       text,
    fail_msg        text,
    PRIMARY KEY (order_id, pay_id)
);

-- ---------------------------------------------------------------------------
-- 5. 操作日志
-- ---------------------------------------------------------------------------
-- id 用 bigserial 而不是 (order_id, ts)：同一秒内可能有多条操作，
-- 用时间做 PK 会互相覆盖——而排障恰恰要看"同一秒里到底发生了什么"。
CREATE TABLE IF NOT EXISTS knowops_ops.op_log (
    id              bigserial PRIMARY KEY,
    order_id        text NOT NULL,
    ts              timestamptz NOT NULL,
    level           text NOT NULL,          -- INFO / WARN / ERROR
    operator        text NOT NULL,          -- system / 工号
    action          text NOT NULL,
    detail          text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ops_log_order_ts ON knowops_ops.op_log (order_id, ts);
CREATE INDEX IF NOT EXISTS idx_ops_track_order   ON knowops_ops.shipment_track (order_id, seq);
CREATE INDEX IF NOT EXISTS idx_ops_orders_status ON knowops_ops.orders (status);

-- ---------------------------------------------------------------------------
-- 6. Agent 可见视图（脱敏）
-- ---------------------------------------------------------------------------
-- 工具只查这张视图，不查 orders 原表。这样即使 SQL Guard 的脱敏规则被改坏，
-- Agent 也拿不到完整手机号——脱敏不该只有一个实现点。
CREATE OR REPLACE VIEW knowops_ops.v_order AS
SELECT
    order_id,
    warehouse_id,
    carrier,
    status,
    created_at,
    outbound_at,
    promised_at,
    ship_to_city,
    -- 隐藏中间四位：既能让用户报单号时对上，又不构成个人信息泄露
    CASE
        WHEN length(receiver_phone) >= 11
        THEN substr(receiver_phone, 1, 3) || '****' || substr(receiver_phone, 8)
        ELSE '***'
    END AS receiver_phone_masked,
    amount_cent,
    sku
FROM knowops_ops.orders;
