package com.knowops.app.contract;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.knowops.contract.dto.SubjectContext;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 契约反序列化：验证 docs/13 §3.2 的机器守卫。
 *
 * <p>「权限是检索语句的一部分」要靠代码守住，不靠人记——谁哪天顺手加回
 * allowed_doc_ids，反序列化必须当场报错，而不是静默忽略（对应 Python 侧
 * pydantic 的 extra="forbid"，Java 侧是 Jackson FAIL_ON_UNKNOWN_PROPERTIES）。
 */
class ContractDeserializationTest {

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void unknown_field_is_rejected() throws Exception {
        String json = """
                {
                  "user_id": "u_1",
                  "account_status": "active",
                  "dept_paths": ["D10"],
                  "roles": ["employee"],
                  "subject_epoch": 1,
                  "allowed_doc_ids": ["doc_881"]
                }
                """;
        // 生成器默认开启 FAIL_ON_UNKNOWN_PROPERTIES，未知字段应抛异常
        assertThatThrownBy(() -> mapper.readValue(json, SubjectContext.class))
                .isInstanceOf(Exception.class);
    }

    @Test
    void valid_context_deserializes() throws Exception {
        String json = """
                {
                  "user_id": "u_2048",
                  "account_status": "active",
                  "dept_paths": ["D10", "D10.3"],
                  "roles": ["employee"],
                  "groups": ["sg_17"],
                  "approval_doc_ids": ["doc_902"],
                  "subject_epoch": 1743
                }
                """;
        SubjectContext ctx = mapper.readValue(json, SubjectContext.class);
        assertThat(ctx.getUserId()).isEqualTo("u_2048");
        assertThat(ctx.getDeptPaths()).contains("D10");
        assertThat(ctx.getApprovalDocIds()).contains("doc_902");
    }

    @Test
    void snake_case_maps_to_camel_fields() throws Exception {
        String json = "{\"user_id\":\"u_1\",\"account_status\":\"active\","
                + "\"dept_paths\":[],\"roles\":[],\"subject_epoch\":0}";
        SubjectContext ctx = mapper.readValue(json, SubjectContext.class);
        assertThat(ctx.getUserId()).isEqualTo("u_1");
        assertThat(ctx.getSubjectEpoch()).isZero();
    }
}
