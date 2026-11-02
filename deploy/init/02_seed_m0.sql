-- =============================================================================
-- M0 最小种子数据
-- =============================================================================
-- 目的：让"鉴权下推进检索 SQL"这条最核心的设计在 M0 就能被验证，
--       而不是等到 M1 有真实文档才第一次跑。
--
-- 覆盖三种授权路径（对应 docs/05 §9.1 的六路来源中的三路）：
--   doc_pub_01  : v=public                → 任何人都可见
--   doc_881     : depts=[D10.3]           → 部门共享（祖先链命中）
--   doc_902     : 无 depts，仅审批授权     → 走 approval_doc_ids 例外集合
--
-- 期望结果（M0 smoke 断言，见 Makefile 的 smoke-db）：
--   用户 u_2048 (dept_paths=[D10,D10.3], approval=[doc_902])
--     → 可见 doc_pub_01 / doc_881 / doc_902，共 3 份
--   用户 u_7000 (dept_paths=[D20], 无审批)
--     → 只见 doc_pub_01，共 1 份
--   用户 u_9999 (account_status=disabled)
--     → 0 份（ADR-0001：主体失效一票否决，且优先于所有授权求值）
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 主体
-- ---------------------------------------------------------------------------
INSERT INTO knowops_write.kb_subject_state (user_id, account_status, subject_epoch) VALUES
    ('u_2048', 'active',   1),
    ('u_7000', 'active',   1),
    ('u_9999', 'disabled', 1)
ON CONFLICT (user_id) DO NOTHING;

-- u_2048 属于 D10.3（祖先链 D10 → D10.3），并在共享组 sg_17 里
INSERT INTO knowops_write.kb_share_group (id, name, members_json) VALUES
    ('sg_17', '履约中台-知识共享组', '{"users": ["u_2048"]}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 文档
-- ---------------------------------------------------------------------------
INSERT INTO knowops_write.kb_document
    (id, title, file_key, mime, version, owner_id, owner_dept, acl_mode, status, acl_epoch) VALUES
    ('doc_pub_01', '员工手册（全公司）',   'seed/public/handbook.pdf',   'application/pdf', 1, 'u_1001', 'D10',    'static',   'active', 1),
    ('doc_881',    '跨部门调拨管理办法',   'seed/d10-3/transfer.docx',   'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 1, 'u_1001', 'D10.3', 'static',   'active', 3),
    ('doc_902',    '理赔政策（审批可见）', 'seed/approval/claim.pdf',    'application/pdf', 1, 'u_1002', 'D20',    'approval', 'active', 1)
ON CONFLICT (id) DO NOTHING;

INSERT INTO knowops_write.kb_acl_grant (doc_id, subject_type, subject_id, action, source) VALUES
    ('doc_pub_01', 'role', 'employee', 'read', 'public'),
    ('doc_881',    'dept', 'D10.3',    'read', 'static')
ON CONFLICT DO NOTHING;

-- 审批授权：既进 kb_approval（审计），也生成一条 source=approval 的 grant；
-- 但检索时走的是 approval_doc_ids 这个小集合，不物化进 acl_jsonb（05 §2.3 语义 3）
INSERT INTO knowops_write.kb_approval (id, doc_id, applicant_id, approver_id, status, granted_at, expire_at) VALUES
    ('AP-01', 'doc_902', 'u_2048', 'u_1002', 'approved', now(), now() + interval '30 days')
ON CONFLICT (id) DO NOTHING;

INSERT INTO knowops_write.kb_acl_grant (doc_id, subject_type, subject_id, action, source, expire_at) VALUES
    ('doc_902', 'user', 'u_2048', 'read', 'approval', now() + interval '30 days')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- 读模型：文档镜像（检索 SQL 的 join 目标）
-- ---------------------------------------------------------------------------
INSERT INTO knowops_read.kb_doc_mirror (doc_id, status, acl_from, acl_to, acl_epoch, version) VALUES
    ('doc_pub_01', 'active', now() - interval '10 days', NULL, 1, 1),
    ('doc_881',    'active', now() - interval '10 days', NULL, 3, 1),
    ('doc_902',    'active', now() - interval '1 day',   NULL, 1, 1)
ON CONFLICT (doc_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 父块 + 子块
-- embedding 用常量占位（array_fill → vector）：M0 只验证鉴权管道与 SQL 可执行，
-- 不验证召回质量；真实向量在 M1 由 BGE-M3 写入。
-- ---------------------------------------------------------------------------
INSERT INTO knowops_read.kb_parent_chunk (id, doc_id, content, section_path, page_no) VALUES
    ('p_pub_1', 'doc_pub_01', '员工手册全文（父块占位）', '第一章', 1),
    ('p_881_1', 'doc_881',    '跨部门调拨管理办法全文（父块占位）', '第三章 审批', 12),
    ('p_902_1', 'doc_902',    '理赔政策全文（父块占位）', '第二章 赔付标准', 4)
ON CONFLICT (id) DO NOTHING;

INSERT INTO knowops_read.kb_chunk
    (id, doc_id, parent_id, ord, content, section_path, page_no, token_len, acl_jsonb, acl_epoch, embed_model, embed_ver, embedding) VALUES
    ('c_pub_1',  'doc_pub_01', 'p_pub_1', 1, '本手册适用于全体员工。',
     '第一章', 1, 12,
     '{"v":"public"}'::jsonb, 1, 'BAAI/bge-m3', 'v1', array_fill(0.01::real, ARRAY[1024])::vector),

    ('c_881_13', 'doc_881', 'p_881_1', 1, '跨部门调拨需一级部门负责人审批，财务复核后生效。',
     '第三章 审批', 12, 26,
     '{"v":"scoped","depts":["D10.3"],"roles":[],"groups":[],"users":[]}'::jsonb, 3,
     'BAAI/bge-m3', 'v1', array_fill(0.02::real, ARRAY[1024])::vector),

    ('c_902_04', 'doc_902', 'p_902_1', 1, '赔付标准按订单实付金额的 1.5 倍计算，上限 500 元。',
     '第二章 赔付标准', 4, 30,
     '{"v":"scoped","depts":[],"roles":["claim_admin"],"groups":[],"users":[]}'::jsonb, 1,
     'BAAI/bge-m3', 'v1', array_fill(0.03::real, ARRAY[1024])::vector)
ON CONFLICT (id) DO NOTHING;

INSERT INTO knowops_read.parse_report
    (doc_id, engine, route, deploy, page_count, text_coverage, table_teds, reading_order_conf, header_footer_leak, empty_ratio, score) VALUES
    ('doc_pub_01', 'docling',  'L1', 'local', 24, 0.98, 0.91, 0.95, 0.01, 0.00, 0.95),
    ('doc_881',    'docling',  'L1', 'local', 18, 0.96, 0.88, 0.93, 0.02, 0.01, 0.93),
    ('doc_902',    'pymupdf',  'L0', 'local',  6, 0.99, NULL, 0.99, 0.00, 0.00, 0.97)
ON CONFLICT (doc_id) DO NOTHING;
