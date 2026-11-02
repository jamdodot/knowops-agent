package com.knowops.app.acl;

import com.knowops.contract.dto.SubjectContext;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * M0 骨架的内存实现：真实实现要查 kb_subject_state + 部门表 + 审批表。
 *
 * <p>这里刻意把「账号状态一票否决」「祖先链展开」「approval 软上限 50」三条
 * 语义用代码固定下来，让单元测试能钉住它们——真实实现换数据源时，这几条
 * 断言不能变（docs/05 §8 边界用例）。
 */
@Component
public class InMemorySubjectResolver implements SubjectResolver {

    /** 部门 -> 父部门（用于祖先链展开）。真实实现来自组织架构表。 */
    private static final Map<String, String> PARENT = Map.of(
            "D10.3", "D10",
            "D10.3.7", "D10.3"
    );

    private final Map<String, SubjectRecord> subjects = new ConcurrentHashMap<>();

    public InMemorySubjectResolver() {
        seed();
    }

    private void seed() {
        // u_2048：活跃，D10 部门，员工 + 履约运营，一条审批授权
        put("u_2048", "active", List.of("D10.3.7"), List.of("employee", "fulfillment_ops"),
                List.of("sg_17"), List.of("doc_902"), 1743L);
        // u_9999：已停用（一票否决的测试靶子）
        put("u_9999", "disabled", List.of("D10"), List.of("employee"),
                List.of(), List.of(), 1L);
    }

    private void put(String userId, String status, List<String> deptLeafs, List<String> roles,
                     List<String> groups, List<String> approvals, long epoch) {
        subjects.put(userId, new SubjectRecord(status, deptLeafs, roles, groups, approvals, epoch));
    }

    @Override
    public SubjectContext resolve(String userId) {
        SubjectRecord r = subjects.get(userId);
        if (r == null) {
            throw new SubjectDisabledException(userId, "unknown");
        }
        if (!"active".equals(r.accountStatus())) {
            // ADR-0001：主体失效优先于所有授权求值，一票否决
            throw new SubjectDisabledException(userId, r.accountStatus());
        }

        SubjectContext ctx = new SubjectContext();
        ctx.setUserId(userId);
        ctx.setAccountStatus(SubjectContext.AccountStatusEnum.ACTIVE);
        ctx.setDeptPaths(ancestors(r.deptLeafs()));
        ctx.setRoles(r.roles());
        ctx.setGroups(r.groups());
        ctx.setApprovalDocIds(capApprovals(r.approvals()));
        ctx.setSubjectEpoch(r.subjectEpoch());
        return ctx;
    }

    /** 自身 + 所有祖先（docs/05 §2.3 语义 1：父部门授权天然覆盖子部门）。 */
    private List<String> ancestors(List<String> leafs) {
        var out = new java.util.LinkedHashSet<String>();
        for (String leaf : leafs) {
            String cur = leaf;
            while (cur != null) {
                out.add(cur);
                cur = PARENT.get(cur);
            }
        }
        return List.copyOf(out);
    }

    /** 软上限 50：超限应降级为强一致 join（ADR-0001 缓解措施）。M0 只做截断并告警。 */
    private List<String> capApprovals(List<String> approvals) {
        if (approvals.size() <= 50) {
            return approvals;
        }
        // 生产实现：不截断，改走强一致 join。这里截断仅保证契约 maxItems=50 不被违反。
        return approvals.subList(0, 50);
    }

    private record SubjectRecord(String accountStatus, List<String> deptLeafs, List<String> roles,
                                 List<String> groups, List<String> approvals, long subjectEpoch) {
    }
}
