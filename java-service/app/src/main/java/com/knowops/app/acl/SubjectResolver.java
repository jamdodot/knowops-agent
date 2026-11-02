package com.knowops.app.acl;

import com.knowops.contract.dto.SubjectContext;

/**
 * 主体解析：把 user_id 解析成 {@link SubjectContext}。
 *
 * <p>这是「权限是检索语句的一部分」这条命题在 Java 侧的起点（docs/05 §4）：
 * 缓存/下发的不是"可见文档清单"，而是"我是谁"——dept_paths（祖先链）、
 * roles、groups、approval_doc_ids、subject_epoch。
 *
 * <p>硬约束（docs/13 §3.1、ADR-0001）：
 * <ul>
 *   <li>{@code account_status != active} 一票否决，优先于所有授权求值；</li>
 *   <li>{@code approval_doc_ids} 软上限 50，超限降级强一致 join；</li>
 *   <li>{@code subject_epoch} 任一变则 +1，用于缓存 key 与幂等键。</li>
 * </ul>
 */
public interface SubjectResolver {

    /**
     * 解析主体。返回 {@code null} 表示账号不存在或已停用（调用方转 SUBJECT_DISABLED）。
     *
     * @throws SubjectDisabledException 账号非 active（一票否决）
     */
    SubjectContext resolve(String userId);
}
