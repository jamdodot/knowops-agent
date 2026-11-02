package com.knowops.app.web;

import com.knowops.app.acl.SubjectResolver;
import com.knowops.contract.dto.SubjectContext;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * ACL 计算（主体解析）接口（docs/04 §2 M0 验收项：ACL 计算）。
 *
 * <p>这是「权限是检索语句的一部分」的 Java 侧落点（docs/05 §4）：
 * 返回的是主体属性（dept_paths 祖先链 / roles / groups / approval_doc_ids
 * / subject_epoch），<b>不是可见文档清单</b>。
 *
 * <p>供前端/内部系统显式调用，也供调试与审计对齐。网关内部调用
 * {@link SubjectResolver} 时走同一实现——权限逻辑只有一份。
 */
@RestController
@RequestMapping("/v1/subjects")
public class SubjectController {

    private final SubjectResolver subjectResolver;

    public SubjectController(SubjectResolver subjectResolver) {
        this.subjectResolver = subjectResolver;
    }

    @GetMapping("/{user_id}")
    public SubjectContext resolve(@PathVariable("user_id") String userId) {
        return subjectResolver.resolve(userId);
    }
}
