-- =============================================================================
-- ACL 冒烟测试：证明"鉴权是检索语句的一部分"
-- =============================================================================
-- 为什么 M0 就要有这个：这是整个项目最核心的论点（05 §3），
-- 但它依赖三个容易踩空的细节：
--   1. acl_jsonb 索引必须是 jsonb_ops，否则 ?| 直接报错；
--   2. 部门走祖先链展开，不是递归查子树；
--   3. 审批授权走独立小集合 = ANY()，不物化进 acl_jsonb。
-- 这三条任意一条写错，M1 的越权集（80 组）都会红，但那时已经很难定位。
--
-- 用法：make smoke-db（或 python scripts/kw.py smoke-db）
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS knowops_smoke;

-- 把"许可集合"写成函数，只是为了在冒烟里复用；生产代码里它就是那段 CTE，
-- 不允许被拆成"先算清单再过滤"（那会退回 1500 万条缓存的老路，05 §2.1）。
CREATE OR REPLACE FUNCTION knowops_smoke.visible_chunk_count(
    p_user_id       text,
    p_dept_paths    text[],
    p_roles         text[],
    p_groups        text[],
    p_approval_docs text[]
) RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT count(*)::integer
    FROM knowops_read.kb_chunk c
    JOIN knowops_read.kb_doc_mirror d ON d.doc_id = c.doc_id
    WHERE d.status = 'active'
      AND d.acl_from <= now()
      AND (d.acl_to IS NULL OR d.acl_to > now())
      AND (
             c.acl_jsonb->>'v' = 'public'
          OR c.acl_jsonb->'depts'  ?| p_dept_paths
          OR c.acl_jsonb->'roles'  ?| p_roles
          OR c.acl_jsonb->'groups' ?| p_groups
          OR c.acl_jsonb->'users'  ?  p_user_id
          OR c.doc_id = ANY(p_approval_docs)
      );
$$;

DO $$
DECLARE
    n integer;
BEGIN
    -- ① 部门祖先链命中 + 审批例外 + public，三路并集
    n := knowops_smoke.visible_chunk_count(
             'u_2048', ARRAY['D10', 'D10.3'], ARRAY['employee', 'fulfillment_ops'],
             ARRAY['sg_17'], ARRAY['doc_902']);
    IF n <> 3 THEN
        RAISE EXCEPTION 'ACL 冒烟失败①：u_2048 期望 3 条（public + 部门 + 审批），实际 %', n;
    END IF;

    -- ② 兄弟部门不在祖先链上：只见 public
    n := knowops_smoke.visible_chunk_count(
             'u_7000', ARRAY['D20'], ARRAY['employee'], ARRAY[]::text[], ARRAY[]::text[]);
    IF n <> 1 THEN
        RAISE EXCEPTION 'ACL 冒烟失败②：u_7000 期望 1 条（仅 public），实际 %', n;
    END IF;

    -- ③ 空数组不能把条件变成"全可见"（?| 空数组必须是 false，不是 true）
    n := knowops_smoke.visible_chunk_count(
             'u_8888', ARRAY[]::text[], ARRAY[]::text[], ARRAY[]::text[], ARRAY[]::text[]);
    IF n <> 1 THEN
        RAISE EXCEPTION 'ACL 冒烟失败③：空主体属性期望 1 条（仅 public），实际 %', n;
    END IF;

    -- ④ 主体失效一票否决：查询本身不做这件事，必须在解析 SubjectContext 时前置拦掉（ADR-0001）
    SELECT count(*) INTO n
    FROM knowops_write.kb_subject_state
    WHERE user_id = 'u_9999' AND account_status = 'active';
    IF n <> 0 THEN
        RAISE EXCEPTION 'ACL 冒烟失败④：u_9999 应为 disabled，实际仍是 active';
    END IF;

    RAISE NOTICE 'ACL 冒烟通过：4/4（public / 部门祖先链 / 审批例外 / 空数组边界 / 主体失效）';
END
$$;
