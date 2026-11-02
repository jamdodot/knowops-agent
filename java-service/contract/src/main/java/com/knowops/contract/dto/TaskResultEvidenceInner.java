package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonTypeName;
import com.fasterxml.jackson.annotation.JsonValue;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * TaskResultEvidenceInner
 */

@JsonTypeName("Task_result_evidence_inner")
@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class TaskResultEvidenceInner {

  private @Nullable String hypothesisId;

  /**
   * Gets or Sets verdict
   */
  public enum VerdictEnum {
    SUPPORT("support"),
    
    REFUTE("refute"),
    
    IRRELEVANT("irrelevant");

    private final String value;

    VerdictEnum(String value) {
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
    public static VerdictEnum fromValue(String value) {
      for (VerdictEnum b : VerdictEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private @Nullable VerdictEnum verdict;

  private @Nullable String summary;

  public TaskResultEvidenceInner hypothesisId(@Nullable String hypothesisId) {
    this.hypothesisId = hypothesisId;
    return this;
  }

  /**
   * Get hypothesisId
   * @return hypothesisId
   */
  
  @JsonProperty("hypothesis_id")
  public @Nullable String getHypothesisId() {
    return hypothesisId;
  }

  @JsonProperty("hypothesis_id")
  public void setHypothesisId(@Nullable String hypothesisId) {
    this.hypothesisId = hypothesisId;
  }

  public TaskResultEvidenceInner verdict(@Nullable VerdictEnum verdict) {
    this.verdict = verdict;
    return this;
  }

  /**
   * Get verdict
   * @return verdict
   */
  
  @JsonProperty("verdict")
  public @Nullable VerdictEnum getVerdict() {
    return verdict;
  }

  @JsonProperty("verdict")
  public void setVerdict(@Nullable VerdictEnum verdict) {
    this.verdict = verdict;
  }

  public TaskResultEvidenceInner summary(@Nullable String summary) {
    this.summary = summary;
    return this;
  }

  /**
   * Get summary
   * @return summary
   */
  
  @JsonProperty("summary")
  public @Nullable String getSummary() {
    return summary;
  }

  @JsonProperty("summary")
  public void setSummary(@Nullable String summary) {
    this.summary = summary;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    TaskResultEvidenceInner taskResultEvidenceInner = (TaskResultEvidenceInner) o;
    return Objects.equals(this.hypothesisId, taskResultEvidenceInner.hypothesisId) &&
        Objects.equals(this.verdict, taskResultEvidenceInner.verdict) &&
        Objects.equals(this.summary, taskResultEvidenceInner.summary);
  }

  @Override
  public int hashCode() {
    return Objects.hash(hypothesisId, verdict, summary);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class TaskResultEvidenceInner {\n");
    sb.append("    hypothesisId: ").append(toIndentedString(hypothesisId)).append("\n");
    sb.append("    verdict: ").append(toIndentedString(verdict)).append("\n");
    sb.append("    summary: ").append(toIndentedString(summary)).append("\n");
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

