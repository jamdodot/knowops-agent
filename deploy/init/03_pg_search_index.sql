-- =============================================================================
-- pg_search 的 BM25 索引（可选，失败不阻塞）
-- =============================================================================
-- 为什么要单独一个文件：`USING bm25` 是 ParadeDB 扩展的语法，不同镜像版本
-- 签名不同（早期要 key_field=，0.26 起 key_field 已废弃为空操作）。
-- 放在主 schema 里，一旦镜像 tag 不对，整个初始化会失败，连表都建不出来。
--
-- 所以这里用 DO + 异常兜底：建不出来只告警，退化到 kb_chunk_tsv_idx（GIN tsvector）。
-- 这正是 10 §5 说的退化方案，不是妥协——先能跑，再谈 BM25 质量。
--
-- 两个真库踩过的坑（2026-11-04 验证）：
--   ① 扩展"预加载"不等于"已安装"，必须先 CREATE EXTENSION pg_search（见 01_schema.sql），
--      否则下面 IF NOT EXISTS 判断为真直接跳过，最后只有 GIN(tsv) 可用。
--   ② 默认分词器把整句中文当一个 token：
--        paradedb.tokenize('{"type":"default"}', '跨部门调拨需审批')
--          → (跨部门调拨需审批,0)        ← 一个 token，中文查询永远 0 命中
--        paradedb.tokenize('{"type":"chinese_compatible"}', '跨部门调拨需审批')
--          → (跨,0) (部,1) (门,2) ...    ← 逐字 + 2-gram，才能命中
--      所以 text_fields 里必须显式指定 chinese_compatible。
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search') THEN
        RAISE WARNING 'pg_search 扩展不存在，跳过 BM25 索引；关键词那一路退化到 GIN(tsv)';
        RETURN;
    END IF;

    -- key_field 在 0.26 起废弃（不再是必填项），主键 id 自动作为 key
    EXECUTE $ddl$
        CREATE INDEX IF NOT EXISTS kb_chunk_bm25_idx
        ON knowops_read.kb_chunk
        USING bm25 (id, content)
        WITH (text_fields = '{"content": {"tokenizer": {"type": "chinese_compatible"}}}')
    $ddl$;
    RAISE NOTICE 'pg_search BM25 索引已创建（content 用 chinese_compatible 分词）';

EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'pg_search BM25 索引创建失败（%），退化到 GIN(tsv)；不影响 M0 验收', SQLERRM;
END
$$;
