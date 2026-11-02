package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * ApprovalDecision
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class ApprovalDecision {

  private String approvalId;

  /**
   * Gets or Sets decision
   */
  public enum DecisionEnum {
    APPROVED("approved"),
    
    REJECTED("rejected");

    private final String value;

    DecisionEnum(String value) {
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
    public static DecisionEnum fromValue(String value) {
      for (DecisionEnum b : DecisionEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private DecisionEnum decision;

  private @Nullable String comment;

  public ApprovalDecision() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public ApprovalDecision(String approvalId, DecisionEnum decision) {
    this.approvalId = approvalId;
    this.decision = decision;
  }

  public ApprovalDecision approvalId(String approvalId) {
    this.approvalId = approvalId;
    return this;
  }

  /**
   * Get approvalId
   * @return approvalId
   */
  @NotNull 
  @JsonProperty("approval_id")
  public String getApprovalId() {
    return approvalId;
  }

  @JsonProperty("approval_id")
  public void setApprovalId(String approvalId) {
    this.approvalId = approvalId;
  }

  public ApprovalDecision decision(DecisionEnum decision) {
    this.decision = decision;
    return this;
  }

  /**
   * Get decision
   * @return decision
   */
  @NotNull 
  @JsonProperty("decision")
  public DecisionEnum getDecision() {
    return decision;
  }

  @JsonProperty("decision")
  public void setDecision(DecisionEnum decision) {
    this.decision = decision;
  }

  public ApprovalDecision comment(@Nullable String comment) {
    this.comment = comment;
    return this;
  }

  /**
   * 拒绝原因会回灌给 Agent 重新规划（docs/01 §5.4）。
   * @return comment
   */
  @Size(max = 500) 
  @JsonProperty("comment")
  public @Nullable String getComment() {
    return comment;
  }

  @JsonProperty("comment")
  public void setComment(@Nullable String comment) {
    this.comment = comment;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    ApprovalDecision approvalDecision = (ApprovalDecision) o;
    return Objects.equals(this.approvalId, approvalDecision.approvalId) &&
        Objects.equals(this.decision, approvalDecision.decision) &&
        Objects.equals(this.comment, approvalDecision.comment);
  }

  @Override
  public int hashCode() {
    return Objects.hash(approvalId, decision, comment);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class ApprovalDecision {\n");
    sb.append("    approvalId: ").append(toIndentedString(approvalId)).append("\n");
    sb.append("    decision: ").append(toIndentedString(decision)).append("\n");
    sb.append("    comment: ").append(toIndentedString(comment)).append("\n");
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

