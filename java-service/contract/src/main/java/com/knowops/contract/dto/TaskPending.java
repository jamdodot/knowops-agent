package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonTypeName;
import com.fasterxml.jackson.annotation.JsonValue;
import com.knowops.contract.dto.ActionProposal;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * 仅 state&#x3D;input_required 时存在。
 */

@JsonTypeName("Task_pending")
@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class TaskPending {

  /**
   * Gets or Sets kind
   */
  public enum KindEnum {
    APPROVAL("approval"),
    
    CLARIFICATION("clarification");

    private final String value;

    KindEnum(String value) {
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
    public static KindEnum fromValue(String value) {
      for (KindEnum b : KindEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private @Nullable KindEnum kind;

  private @Nullable ActionProposal approval;

  private @Nullable String question;

  public TaskPending kind(@Nullable KindEnum kind) {
    this.kind = kind;
    return this;
  }

  /**
   * Get kind
   * @return kind
   */
  
  @JsonProperty("kind")
  public @Nullable KindEnum getKind() {
    return kind;
  }

  @JsonProperty("kind")
  public void setKind(@Nullable KindEnum kind) {
    this.kind = kind;
  }

  public TaskPending approval(@Nullable ActionProposal approval) {
    this.approval = approval;
    return this;
  }

  /**
   * Get approval
   * @return approval
   */
  @Valid 
  @JsonProperty("approval")
  public @Nullable ActionProposal getApproval() {
    return approval;
  }

  @JsonProperty("approval")
  public void setApproval(@Nullable ActionProposal approval) {
    this.approval = approval;
  }

  public TaskPending question(@Nullable String question) {
    this.question = question;
    return this;
  }

  /**
   * Get question
   * @return question
   */
  
  @JsonProperty("question")
  public @Nullable String getQuestion() {
    return question;
  }

  @JsonProperty("question")
  public void setQuestion(@Nullable String question) {
    this.question = question;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    TaskPending taskPending = (TaskPending) o;
    return Objects.equals(this.kind, taskPending.kind) &&
        Objects.equals(this.approval, taskPending.approval) &&
        Objects.equals(this.question, taskPending.question);
  }

  @Override
  public int hashCode() {
    return Objects.hash(kind, approval, question);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class TaskPending {\n");
    sb.append("    kind: ").append(toIndentedString(kind)).append("\n");
    sb.append("    approval: ").append(toIndentedString(approval)).append("\n");
    sb.append("    question: ").append(toIndentedString(question)).append("\n");
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

