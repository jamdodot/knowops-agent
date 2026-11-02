-- =============================================================================
-- pg_search 的 BM25 索引（可选，失败不阻塞）
-- =============================================================================
-- 为什么要单独一个文件：`USING bm25` 是 ParadeDB 扩展的语法，不同镜像版本
-- 签名不同（v1 用 `USING bm25 (id, (content))`，v2 改 `USING bm25 (id, content)`）。
-- 放在主 schema 里，一旦镜像 tag 不对，整个初始化会失败，连表都建不出来。
--
-- 所以这里用 DO + 异常兜底：建不出来只告警，退化到 kb_chunk_tsv_idx（GIN tsvector）。
-- 这正是 10 §5 说的退化方案，不是妥协——先能跑，再谈 BM25 质量。
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search') THEN
        RAISE WARNING 'pg_search 扩展不存在，跳过 BM25 索引；关键词那一路退化到 GIN(tsv)';
        RETURN;
    END IF;

    -- ParadeDB v2 签名：key_field 必须是主键或唯一列（这里用 id）
    EXECUTE 'CREATE INDEX IF NOT EXISTS kb_chunk_bm25_idx
             ON knowops_read.kb_chunk
             USING bm25 (id, content)
             WITH (key_field = ''id'')';
    RAISE NOTICE 'pg_search BM25 索引已创建';

EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'pg_search BM25 索引创建失败（%），退化到 GIN(tsv)；不影响 M0 验收', SQLERRM;
END
$$;
