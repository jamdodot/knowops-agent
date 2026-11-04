-- =============================================================================
-- KnowOps Agent — M0 数据模型定稿
-- =============================================================================
-- 依据：docs/06 §2（表清单与归属）、docs/05 §3（检索 SQL 依赖的列与索引）、
--       docs/07 §3（会话四表）、docs/03 §4.2（评测三表）、docs/13 §3.3（epoch 口径）
--
-- 三个 schema，归属不同，别混：
--   knowops_write = 真相，Java 拥有。M0~M2 由 MySQL 合并进 PG（06 §5 路径 A），
--                   M5 再拆回独立 MySQL 演示 outbox。
--   knowops_read  = 派生，Python 拥有。只读，永不反向写（06 §3 的 CQRS 单向派生）。
--   knowops_mem   = 记忆，Python 拥有。它既不是 MySQL 的副本（没有源可以重放），
--                   也不是检索派生物，所以单独一个 schema；且永不进 kb_chunk 索引（07 §1.5）。
--
-- 两条不写会造成事故的约束：
--   1. 跨 schema 不建外键。物理外键会让"收权同步写、放权异步写"的顺序设计失效
--      （06 §4），且路径 A → 拆库时全部要删。
--   2. acl_jsonb 的索引必须是默认 jsonb_ops。jsonb_path_ops 只支持 @>，
--      不支持 ? / ?|，用它会直接报 "operator does not exist"（11 §2.4）。
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector：向量列与 HNSW
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- 退化方案备用（10 §5：pg_search 受限时用 trgm + tsvector）
-- pg_search 由镜像的 shared_preload_libraries 提供（见 deploy/docker-compose.yml），
-- 但**预加载不等于已安装**：还要 CREATE EXTENSION，否则 03 的 bm25 索引建不出来。
-- 用 DO 兜底，非 ParadeDB 镜像（没有 pg_search）时只告警，退化到 GIN(tsv)。
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_search;
    RAISE NOTICE 'pg_search 已安装，BM25 走主路';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'pg_search 不可用（%），BM25 退化到 GIN(tsv)', SQLERRM;
END
$$;

CREATE SCHEMA IF NOT EXISTS knowops_write;
CREATE SCHEMA IF NOT EXISTS knowops_read;
CREATE SCHEMA IF NOT EXISTS knowops_mem;


-- =============================================================================
-- knowops_write：真相（Java 拥有）
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 文档主表
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.kb_document (
    id              text        PRIMARY KEY,
    title           text        NOT NULL,
    file_key        text        NOT NULL,                      -- MinIO key 或本地相对路径
    mime            text,
    source_type     text        NOT NULL DEFAULT 'upload'
                                CHECK (source_type IN ('upload', 'sync', 'crawl')),
    version         integer     NOT NULL DEFAULT 1,
    owner_id        text        NOT NULL,
    owner_dept      text        NOT NULL,
    acl_mode        text        NOT NULL DEFAULT 'static'
                                CHECK (acl_mode IN ('static', 'dynamic', 'approval')),
    status          text        NOT NULL DEFAULT 'parsing'
                                CHECK (status IN ('parsing', 'active', 'failed', 'archived')),
    acl_from        timestamptz NOT NULL DEFAULT now(),
    acl_to          timestamptz,
    acl_epoch       bigint      NOT NULL DEFAULT 1,
    parsed_at       timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- acl_epoch 是"引用是否已失效"的唯一判据（07 §3.3）：每次 ACL 变更 +1，
-- 并冗余到 kb_chunk / kb_doc_mirror / chat_turn。真相永远在这一列。
COMMENT ON COLUMN knowops_write.kb_document.acl_epoch IS
  '文档级权限版本；每次 ACL 变更 +1。冗余到 kb_chunk / kb_doc_mirror / chat_turn。';

CREATE INDEX kb_document_owner_idx ON knowops_write.kb_document (owner_dept);
CREATE INDEX kb_document_status_idx ON knowops_write.kb_document (status, updated_at DESC);

-- ---------------------------------------------------------------------------
-- 授权真相：四种来源统一建模，靠 source 区分（05 §1）
-- 不给每种来源建一张表，否则求值要 union 四张表，加一种来源就要改查询。
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.kb_acl_grant (
    id              bigserial   PRIMARY KEY,
    doc_id          text        NOT NULL,
    subject_type    text        NOT NULL CHECK (subject_type IN ('user', 'dept', 'role')),
    subject_id      text        NOT NULL,
    action          text        NOT NULL DEFAULT 'read' CHECK (action IN ('read', 'export')),
    source          text        NOT NULL CHECK (source IN ('static', 'dynamic', 'approval', 'public')),
    rule_expr       text,                                      -- 仅 dynamic 用
    granted_by      text,
    granted_at      timestamptz NOT NULL DEFAULT now(),
    expire_at       timestamptz                                -- 仅 approval 用
);

CREATE INDEX kb_acl_grant_doc_idx     ON knowops_write.kb_acl_grant (doc_id);
CREATE INDEX kb_acl_grant_subject_idx ON knowops_write.kb_acl_grant (subject_type, subject_id);
-- 撤销必须作用在"来源"上：撤销部门授权只删这一条，不按 doc_id 批量删（05 §8 U6）
CREATE UNIQUE INDEX kb_acl_grant_uniq
    ON knowops_write.kb_acl_grant (doc_id, subject_type, subject_id, action, source);

-- ---------------------------------------------------------------------------
-- 审批流：与 action_request 的区别是"资源准入" vs "写动作执行"
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.kb_approval (
    id              text        PRIMARY KEY,
    doc_id          text        NOT NULL,
    applicant_id    text        NOT NULL,
    approver_id     text,
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    granted_at      timestamptz,
    expire_at       timestamptz,
    granted_context jsonb       NOT NULL DEFAULT '{}'::jsonb  -- 申请时部门/角色快照，仅审计（ADR-0001）
);

CREATE INDEX kb_approval_applicant_idx ON knowops_write.kb_approval (applicant_id, status);
CREATE INDEX kb_approval_doc_idx       ON knowops_write.kb_approval (doc_id);

-- ---------------------------------------------------------------------------
-- 共享组：授权主体 > 20 时抽象（05 §2.4），文档只存 group_id
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.kb_share_group (
    id              text        PRIMARY KEY,
    name            text        NOT NULL,
    members_json    jsonb       NOT NULL DEFAULT '[]'::jsonb
);

-- ---------------------------------------------------------------------------
-- 主体状态：ADR-0001 的"主体失效一票否决"要有数据来源
-- subject_epoch 是主体属性版本（13 §3.3），任一变则 +1，用于缓存 key 与幂等键
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.kb_subject_state (
    user_id         text        PRIMARY KEY,
    account_status  text        NOT NULL DEFAULT 'active'
                                CHECK (account_status IN ('active', 'disabled', 'resigned')),
    subject_epoch   bigint      NOT NULL DEFAULT 1,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE knowops_write.kb_subject_state IS
  '主体状态与版本。account_status != active 必须在解析 SubjectContext 最前面拦掉（ADR-0001）。';

-- ---------------------------------------------------------------------------
-- 动作审批：Agent 只提议，Java 执行（05 §7）
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.action_request (
    id              text        PRIMARY KEY,
    trace_id        text        NOT NULL,
    agent           text        NOT NULL CHECK (agent IN ('fulfillment')),
    action_type     text        NOT NULL,
    params          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    risk_level      text        NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    approver_id     text,
    comment         text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    decided_at      timestamptz
);

CREATE INDEX action_request_trace_idx ON knowops_write.action_request (trace_id);
CREATE INDEX action_request_status_idx ON knowops_write.action_request (status, created_at DESC);

-- ---------------------------------------------------------------------------
-- 会话四表（07 §0.1 / §3.1）：只有 chat_message 是严格 append-only
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.session (
    id              text        PRIMARY KEY,
    user_id         text        NOT NULL,
    domain          text        NOT NULL CHECK (domain IN ('knowledge', 'fulfillment')),
    title           text,
    status          text        NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'archived', 'deleted')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_active_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX session_user_idx ON knowops_write.session (user_id, last_active_at DESC);

-- 纯 append：不改不删（合规删除走墓碑 deleted_at + 内容脱敏，07 §0.1 例外 3）
CREATE TABLE knowops_write.chat_message (
    id              text        PRIMARY KEY,
    session_id      text        NOT NULL,
    seq             integer     NOT NULL,
    role            text        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text        NOT NULL,
    deleted_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, seq)
);

-- 一轮问答的聚合视图：给分析、评测、成本核算用（07 §3.1）
CREATE TABLE knowops_write.chat_turn (
    id                text        PRIMARY KEY,
    session_id        text        NOT NULL,
    turn_seq          integer     NOT NULL,
    user_msg_id       text,
    assistant_msg_id  text,
    trace_id          text        NOT NULL,
    status            text        NOT NULL DEFAULT 'running'
                                  CHECK (status IN ('running', 'completed', 'failed', 'canceled')),
    input_tokens      integer,
    output_tokens     integer,
    latency_ms        integer,
    citation_json     jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- 含每个引用的 doc_id + acl_epoch
    acl_epoch         bigint,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, turn_seq)
);

-- 命名债：字段名是 acl_epoch，存的是"本轮回答时的主体版本"，不是文档版本。
-- 引用级判定用 citation_json[].acl_epoch，别拿这一列去比 kb_document.acl_epoch（13 §3.3）。
COMMENT ON COLUMN knowops_write.chat_turn.acl_epoch IS
  '语义为 subject_epoch（主体版本），不是文档级 acl_epoch；引用失效判定看 citation_json[].acl_epoch。';

CREATE INDEX chat_turn_trace_idx ON knowops_write.chat_turn (trace_id);

-- ---------------------------------------------------------------------------
-- outbox：与业务写同一事务，避免"写了库没发消息"（06 §4）
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.outbox_event (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),  -- 即 event_id，消费端按它去重
    event_type      text        NOT NULL,
    payload         jsonb       NOT NULL,
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'published', 'failed', 'dead')),
    retry_count     integer     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz
);

CREATE INDEX outbox_event_pending_idx ON knowops_write.outbox_event (status, created_at)
    WHERE status = 'pending';

-- ---------------------------------------------------------------------------
-- 访问审计：与 trace 分工不同——审计要可读、长期留存；trace 要可分析、30 天过期（14 §7）
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_write.audit_log (
    id              bigserial   PRIMARY KEY,
    user_id         text        NOT NULL,
    action          text        NOT NULL,                      -- read / export / query_data / approve
    resource_id     text,
    doc_ids         text[]      NOT NULL DEFAULT '{}',
    trace_id        text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_user_idx ON knowops_write.audit_log (user_id, created_at DESC);
CREATE INDEX audit_log_doc_idx  ON knowops_write.audit_log USING gin (doc_ids);


-- =============================================================================
-- knowops_read：派生（Python 拥有，只读）
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 文档级快照：检索 SQL 必须能 join 到它，否则那句 SQL 物理上跨库（05 §3）
-- 只存元数据，不含正文；由 outbox 同步，收权走同步写（06 §4）
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.kb_doc_mirror (
    doc_id          text        PRIMARY KEY,
    status          text        NOT NULL,
    acl_from        timestamptz NOT NULL,
    acl_to          timestamptz,
    acl_epoch       bigint      NOT NULL,
    version         integer     NOT NULL DEFAULT 1,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX kb_doc_mirror_epoch_idx ON knowops_read.kb_doc_mirror (acl_epoch);

-- ---------------------------------------------------------------------------
-- 检索单元
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.kb_chunk (
    id              text        PRIMARY KEY,
    doc_id          text        NOT NULL,
    parent_id       text,                                       -- small-to-big 回填
    ord             integer     NOT NULL,
    content         text        NOT NULL,
    section_path    text,
    page_no         integer,
    token_len       integer,
    acl_jsonb       jsonb       NOT NULL,                       -- 授权快照（冗余，非真相）
    acl_epoch       bigint      NOT NULL,
    embed_model     text,
    embed_ver       text,                                       -- 三锁之一（models.yaml embed_lock）
    tsv             tsvector,                                   -- GIN 退路；主路是 pg_search BM25
    embedding       vector(1024),                               -- BGE-M3 dense
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (doc_id, ord)
);

-- 向量那一路：cosine。参数按 50w~200w chunk 的中等规模起步，压测后再调。
CREATE INDEX kb_chunk_embedding_idx ON knowops_read.kb_chunk
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- 鉴权那一路：必须是默认 jsonb_ops（支持 ? 与 ?|），不能用 jsonb_path_ops（只支持 @>）
CREATE INDEX kb_chunk_acl_idx ON knowops_read.kb_chunk USING gin (acl_jsonb jsonb_ops);

-- 关键词退路：pg_search 不可用时的 tsvector 路径
CREATE INDEX kb_chunk_tsv_idx ON knowops_read.kb_chunk USING gin (tsv);

CREATE INDEX kb_chunk_doc_idx ON knowops_read.kb_chunk (doc_id);
CREATE INDEX kb_chunk_parent_idx ON knowops_read.kb_chunk (parent_id) WHERE parent_id IS NOT NULL;

COMMENT ON COLUMN knowops_read.kb_chunk.acl_jsonb IS
  '授权快照（派生）。真相在 knowops_write.kb_acl_grant；此列仅为把鉴权下推进检索 SQL。';

-- ---------------------------------------------------------------------------
-- 受限内容分区（可选，M2 打开）：解决 HNSW 强过滤导致的召回骤降（05 §3 性能坑）
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.kb_chunk_restricted (
    LIKE knowops_read.kb_chunk INCLUDING DEFAULTS INCLUDING CONSTRAINTS
);

CREATE INDEX kb_chunk_restricted_embedding_idx ON knowops_read.kb_chunk_restricted
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX kb_chunk_restricted_acl_idx ON knowops_read.kb_chunk_restricted
    USING gin (acl_jsonb jsonb_ops);

-- ---------------------------------------------------------------------------
-- 父块：子块 200~400 token 精准召回，父块 1000~1500 token 喂模型（01 §4）
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.kb_parent_chunk (
    id              text        PRIMARY KEY,
    doc_id          text        NOT NULL,
    content         text        NOT NULL,
    section_path    text,
    page_no         integer
);

CREATE INDEX kb_parent_chunk_doc_idx ON knowops_read.kb_parent_chunk (doc_id);

-- ---------------------------------------------------------------------------
-- 解析质量：归因 E1 的唯一入口（14 §5.1）
-- engine / route / deploy 三个字段是必须的，缺 deploy 就分不清"本地慢"还是"云端排队"（11 §2.12）
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.parse_report (
    doc_id              text        PRIMARY KEY,
    engine              text        NOT NULL,                   -- pymupdf / docling / mineru
    route               text        NOT NULL,                   -- L0 / pipeline / vlm
    deploy              text        NOT NULL,                   -- local / cloud
    page_count          integer,
    text_coverage       real,
    table_teds          real,                                   -- 表格还原率
    reading_order_conf  real,
    header_footer_leak  real,                                   -- 页眉页脚泄漏率
    empty_ratio         real,
    score               real,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX parse_report_score_idx ON knowops_read.parse_report (score);

-- ---------------------------------------------------------------------------
-- 入库任务状态：按 stage 分片，支持断点续跑
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.ingest_task (
    doc_id          text        NOT NULL,
    stage           text        NOT NULL,                       -- parse / chunk / embed / graph
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'running', 'done', 'failed')),
    error           text,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_id, stage)
);

-- ---------------------------------------------------------------------------
-- 图状态：HITL 中断恢复的落点（07 §3.1）
-- append + 链式引用，不覆盖前一行
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.langgraph_checkpoint (
    thread_id       text        NOT NULL,
    checkpoint_id   text        NOT NULL,
    parent_id       text,
    state_jsonb     jsonb       NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, checkpoint_id)
);

CREATE INDEX langgraph_checkpoint_thread_idx ON knowops_read.langgraph_checkpoint (thread_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 滚动摘要水位缓存（派生，可重建）：避免每轮 O(n²) 重算摘要（07 §0.4）
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.summary_cache (
    thread_id       text        PRIMARY KEY,
    upto_seq        integer     NOT NULL,
    summary_text    text        NOT NULL,
    covered_msg_ids text[]      NOT NULL DEFAULT '{}',
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 评测三表（03 §4.2）：跑批必须绑定不可变 config_id
-- ---------------------------------------------------------------------------
CREATE TABLE knowops_read.eval_run (
    id              text        PRIMARY KEY,                    -- cfg-20261108-01 这类
    config_id       text        NOT NULL,
    dataset_id      text        NOT NULL,
    status          text        NOT NULL DEFAULT 'running'
                                CHECK (status IN ('running', 'completed', 'failed', 'invalid')),
    git_sha         text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE TABLE knowops_read.eval_case_result (
    run_id          text        NOT NULL,
    case_id         text        NOT NULL,
    retrieved_ids   text[]      NOT NULL DEFAULT '{}',           -- 含 top50 与 top8 两段
    sent_ids        text[]      NOT NULL DEFAULT '{}',           -- 真正进模型的证据（E5 用）
    answer          text,
    scores          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    diagnose        text,                                        -- E1~E10 / F1~F7
    cost_usd        numeric(10, 6),
    latency_ms      integer,
    trace_id        text,
    PRIMARY KEY (run_id, case_id)
);

CREATE INDEX eval_case_result_diagnose_idx ON knowops_read.eval_case_result (run_id, diagnose);

CREATE TABLE knowops_read.eval_metric (
    id              bigserial   PRIMARY KEY,
    run_id          text        NOT NULL,
    name            text        NOT NULL,
    value           double precision NOT NULL,
    dimension       jsonb       NOT NULL DEFAULT '{}'::jsonb,    -- {"query_type":"multi_hop"}
    UNIQUE (run_id, name, dimension)
);

CREATE INDEX eval_metric_run_idx ON knowops_read.eval_metric (run_id);


-- =============================================================================
-- knowops_mem：记忆（Python 拥有，真相，无上游可重放）
-- =============================================================================

-- 语义记忆：只记偏好 / 常用实体 / 纠错，不记每次提问原文（07 §1.2）
CREATE TABLE knowops_mem.mem_semantic (
    id              text        PRIMARY KEY,
    namespace       text        NOT NULL,                       -- user:{id}，检索强制过滤（07 §1.5）
    user_id         text        NOT NULL,
    kind            text        NOT NULL CHECK (kind IN ('preference', 'entity', 'correction')),
    content         text        NOT NULL,
    confidence      real        NOT NULL DEFAULT 0.6,
    status          text        NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'superseded', 'revoked')),
    embedding       vector(1024),
    hit_count       integer     NOT NULL DEFAULT 0,
    last_hit_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX mem_semantic_ns_idx ON knowops_mem.mem_semantic (namespace, status);
CREATE INDEX mem_semantic_embedding_idx ON knowops_mem.mem_semantic
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- 情景记忆：只存成功的工单，失败案例会把错误先验带进新任务（07 §1.3）
CREATE TABLE knowops_mem.mem_episodic (
    id              text        PRIMARY KEY,
    namespace       text        NOT NULL DEFAULT 'global',
    symptom         text        NOT NULL,
    root_cause      text        NOT NULL,
    evidence_pattern jsonb      NOT NULL DEFAULT '{}'::jsonb,
    outcome         text        NOT NULL DEFAULT 'success' CHECK (outcome = 'success'),
    embedding       vector(1024),
    hit_count       integer     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX mem_episodic_embedding_idx ON knowops_mem.mem_episodic
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

COMMENT ON TABLE knowops_mem.mem_episodic IS
  '只存成功工单（outcome 恒为 success）。存失败案例会把错误先验带进新任务，是情景记忆最危险的污染源。';
