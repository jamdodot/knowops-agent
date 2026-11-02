package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * 唯一合法的权限载体。鉴权条件由 Python 拼进检索 SQL（docs/05 §3）。 刻意不含 allowed_doc_ids —— 那是\&quot;先算清单再过滤\&quot;的老路。 
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class SubjectContext {

  private String userId;

  /**
   * 非 active 一票否决（ADR-0001 主体失效优先于所有授权求值）。
   */
  public enum AccountStatusEnum {
    ACTIVE("active"),
    
    DISABLED("disabled"),
    
    RESIGNED("resigned");

    private final String value;

    AccountStatusEnum(String value) {
      this.value = value;
    }

    @JsonValue
    public String getValue() {
      return value;
    }

    @Override
    public String toString() {
      return String.valueOf(value);
    }

    @JsonCreator
    public static AccountStatusEnum fromValue(String value) {
      for (AccountStatusEnum b : AccountStatusEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private AccountStatusEnum accountStatus;

  private List<String> deptPaths = new ArrayList<>();

  private List<String> roles = new ArrayList<>();

  private List<String> groups = new ArrayList<>();

  private List<String> approvalDocIds = new ArrayList<>();

  private Long subjectEpoch;

  public SubjectContext() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public SubjectContext(String userId, AccountStatusEnum accountStatus, List<String> deptPaths, List<String> roles, Long subjectEpoch) {
    this.userId = userId;
    this.accountStatus = accountStatus;
    this.deptPaths = deptPaths;
    this.roles = roles;
    this.subjectEpoch = subjectEpoch;
  }

  public SubjectContext userId(String userId) {
    this.userId = userId;
    return this;
  }

  /**
   * Get userId
   * @return userId
   */
  @NotNull 
  @JsonProperty("user_id")
  public String getUserId() {
    return userId;
  }

  @JsonProperty("user_id")
  public void setUserId(String userId) {
    this.userId = userId;
  }

  public SubjectContext accountStatus(AccountStatusEnum accountStatus) {
    this.accountStatus = accountStatus;
    return this;
  }

  /**
   * 非 active 一票否决（ADR-0001 主体失效优先于所有授权求值）。
   * @return accountStatus
   */
  @NotNull 
  @JsonProperty("account_status")
  public AccountStatusEnum getAccountStatus() {
    return accountStatus;
  }

  @JsonProperty("account_status")
  public void setAccountStatus(AccountStatusEnum accountStatus) {
    this.accountStatus = accountStatus;
  }

  public SubjectContext deptPaths(List<String> deptPaths) {
    this.deptPaths = deptPaths;
    return this;
  }

  public SubjectContext addDeptPathsItem(String deptPathsItem) {
    if (this.deptPaths == null) {
      this.deptPaths = new ArrayList<>();
    }
    this.deptPaths.add(deptPathsItem);
    return this;
  }

  /**
   * 自身 + 所有祖先，层级通常 ≤ 6。父部门授权天然覆盖子孙。
   * @return deptPaths
   */
  @NotNull @Size(max = 32) 
  @JsonProperty("dept_paths")
  public List<String> getDeptPaths() {
    return deptPaths;
  }

  @JsonProperty("dept_paths")
  public void setDeptPaths(List<String> deptPaths) {
    this.deptPaths = deptPaths;
  }

  public SubjectContext roles(List<String> roles) {
    this.roles = roles;
    return this;
  }

  public SubjectContext addRolesItem(String rolesItem) {
    if (this.roles == null) {
      this.roles = new ArrayList<>();
    }
    this.roles.add(rolesItem);
    return this;
  }

  /**
   * Get roles
   * @return roles
   */
  @NotNull 
  @JsonProperty("roles")
  public List<String> getRoles() {
    return roles;
  }

  @JsonProperty("roles")
  public void setRoles(List<String> roles) {
    this.roles = roles;
  }

  public SubjectContext groups(List<String> groups) {
    this.groups = groups;
    return this;
  }

  public SubjectContext addGroupsItem(String groupsItem) {
    if (this.groups == null) {
      this.groups = new ArrayList<>();
    }
    this.groups.add(groupsItem);
    return this;
  }

  /**
   * 共享组（授权主体 > 20 时收敛）。可为空。
   * @return groups
   */
  
  @JsonProperty("groups")
  public List<String> getGroups() {
    return groups;
  }

  @JsonProperty("groups")
  public void setGroups(List<String> groups) {
    this.groups = groups;
  }

  public SubjectContext approvalDocIds(List<String> approvalDocIds) {
    this.approvalDocIds = approvalDocIds;
    return this;
  }

  public SubjectContext addApprovalDocIdsItem(String approvalDocIdsItem) {
    if (this.approvalDocIds == null) {
      this.approvalDocIds = new ArrayList<>();
    }
    this.approvalDocIds.add(approvalDocIdsItem);
    return this;
  }

  /**
   * 例外层（主体租约），走 = ANY()，不物化进 acl_jsonb。 软上限 50：超限降级为强一致 join（ADR-0001 缓解措施）。 
   * @return approvalDocIds
   */
  @Size(max = 50) 
  @JsonProperty("approval_doc_ids")
  public List<String> getApprovalDocIds() {
    return approvalDocIds;
  }

  @JsonProperty("approval_doc_ids")
  public void setApprovalDocIds(List<String> approvalDocIds) {
    this.approvalDocIds = approvalDocIds;
  }

  public SubjectContext subjectEpoch(Long subjectEpoch) {
    this.subjectEpoch = subjectEpoch;
    return this;
  }

  /**
   * 主体属性版本，任一变则 +1。用于缓存 key / 审计对齐 / 幂等键。
   * minimum: 0
   * @return subjectEpoch
   */
  @NotNull @Min(value = 0L) 
  @JsonProperty("subject_epoch")
  public Long getSubjectEpoch() {
    return subjectEpoch;
  }

  @JsonProperty("subject_epoch")
  public void setSubjectEpoch(Long subjectEpoch) {
    this.subjectEpoch = subjectEpoch;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    SubjectContext subjectContext = (SubjectContext) o;
    return Objects.equals(this.userId, subjectContext.userId) &&
        Objects.equals(this.accountStatus, subjectContext.accountStatus) &&
        Objects.equals(this.deptPaths, subjectContext.deptPaths) &&
        Objects.equals(this.roles, subjectContext.roles) &&
        Objects.equals(this.groups, subjectContext.groups) &&
        Objects.equals(this.approvalDocIds, subjectContext.approvalDocIds) &&
        Objects.equals(this.subjectEpoch, subjectContext.subjectEpoch);
  }

  @Override
  public int hashCode() {
    return Objects.hash(userId, accountStatus, deptPaths, roles, groups, approvalDocIds, subjectEpoch);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class SubjectContext {\n");
    sb.append("    userId: ").append(toIndentedString(userId)).append("\n");
    sb.append("    accountStatus: ").append(toIndentedString(accountStatus)).append("\n");
    sb.append("    deptPaths: ").append(toIndentedString(deptPaths)).append("\n");
    sb.append("    roles: ").append(toIndentedString(roles)).append("\n");
    sb.append("    groups: ").append(toIndentedString(groups)).append("\n");
    sb.append("    approvalDocIds: ").append(toIndentedString(approvalDocIds)).append("\n");
    sb.append("    subjectEpoch: ").append(toIndentedString(subjectEpoch)).append("\n");
    sb.append("}");
    return sb.toString();
  }

  /**
   * Convert the given object to string with each line indented by 4 spaces
   * (except the first line).
   */
  private String toIndentedString(@Nullable Object o) {
    return o == null ? "null" : o.toString().replace("\n", "\n    ");
  }
}

