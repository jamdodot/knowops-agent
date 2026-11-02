package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.knowops.contract.dto.ApprovalDecision;
import com.knowops.contract.dto.ResumeRequestClarification;
import com.knowops.contract.dto.SubjectContext;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * ResumeRequest
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class ResumeRequest {

  private String taskId;

  private SubjectContext subjectCtx;

  private @Nullable ApprovalDecision approval;

  private @Nullable ResumeRequestClarification clarification;

  private @Nullable Integer lastEventId;

  public ResumeRequest() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public ResumeRequest(String taskId, SubjectContext subjectCtx) {
    this.taskId = taskId;
    this.subjectCtx = subjectCtx;
  }

  public ResumeRequest taskId(String taskId) {
    this.taskId = taskId;
    return this;
  }

  /**
   * Get taskId
   * @return taskId
   */
  @NotNull 
  @JsonProperty("task_id")
  public String getTaskId() {
    return taskId;
  }

  @JsonProperty("task_id")
  public void setTaskId(String taskId) {
    this.taskId = taskId;
  }

  public ResumeRequest subjectCtx(SubjectContext subjectCtx) {
    this.subjectCtx = subjectCtx;
    return this;
  }

  /**
   * 必须重传并重新校验——恢复不是\"延续旧权限\"（docs/05 §5 出口 6）。
   * @return subjectCtx
   */
  @NotNull @Valid 
  @JsonProperty("subject_ctx")
  public SubjectContext getSubjectCtx() {
    return subjectCtx;
  }

  @JsonProperty("subject_ctx")
  public void setSubjectCtx(SubjectContext subjectCtx) {
    this.subjectCtx = subjectCtx;
  }

  public ResumeRequest approval(@Nullable ApprovalDecision approval) {
    this.approval = approval;
    return this;
  }

  /**
   * Get approval
   * @return approval
   */
  @Valid 
  @JsonProperty("approval")
  public @Nullable ApprovalDecision getApproval() {
    return approval;
  }

  @JsonProperty("approval")
  public void setApproval(@Nullable ApprovalDecision approval) {
    this.approval = approval;
  }

  public ResumeRequest clarification(@Nullable ResumeRequestClarification clarification) {
    this.clarification = clarification;
    return this;
  }

  /**
   * Get clarification
   * @return clarification
   */
  @Valid 
  @JsonProperty("clarification")
  public @Nullable ResumeRequestClarification getClarification() {
    return clarification;
  }

  @JsonProperty("clarification")
  public void setClarification(@Nullable ResumeRequestClarification clarification) {
    this.clarification = clarification;
  }

  public ResumeRequest lastEventId(@Nullable Integer lastEventId) {
    this.lastEventId = lastEventId;
    return this;
  }

  /**
   * 断线续传水位；服务端从该点之后继续推。
   * @return lastEventId
   */
  
  @JsonProperty("last_event_id")
  public @Nullable Integer getLastEventId() {
    return lastEventId;
  }

  @JsonProperty("last_event_id")
  public void setLastEventId(@Nullable Integer lastEventId) {
    this.lastEventId = lastEventId;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    ResumeRequest resumeRequest = (ResumeRequest) o;
    return Objects.equals(this.taskId, resumeRequest.taskId) &&
        Objects.equals(this.subjectCtx, resumeRequest.subjectCtx) &&
        Objects.equals(this.approval, resumeRequest.approval) &&
        Objects.equals(this.clarification, resumeRequest.clarification) &&
        Objects.equals(this.lastEventId, resumeRequest.lastEventId);
  }

  @Override
  public int hashCode() {
    return Objects.hash(taskId, subjectCtx, approval, clarification, lastEventId);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class ResumeRequest {\n");
    sb.append("    taskId: ").append(toIndentedString(taskId)).append("\n");
    sb.append("    subjectCtx: ").append(toIndentedString(subjectCtx)).append("\n");
    sb.append("    approval: ").append(toIndentedString(approval)).append("\n");
    sb.append("    clarification: ").append(toIndentedString(clarification)).append("\n");
    sb.append("    lastEventId: ").append(toIndentedString(lastEventId)).append("\n");
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

