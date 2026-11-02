package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonTypeName;
import com.knowops.contract.dto.Citation;
import com.knowops.contract.dto.TaskResultEvidenceInner;
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
 * 仅 state&#x3D;completed 时存在。
 */

@JsonTypeName("Task_result")
@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class TaskResult {

  private @Nullable String rootCause;

  private @Nullable Float confidence;

  private List<@Valid TaskResultEvidenceInner> evidence = new ArrayList<>();

  private List<@Valid Citation> citations = new ArrayList<>();

  public TaskResult rootCause(@Nullable String rootCause) {
    this.rootCause = rootCause;
    return this;
  }

  /**
   * Get rootCause
   * @return rootCause
   */
  
  @JsonProperty("root_cause")
  public @Nullable String getRootCause() {
    return rootCause;
  }

  @JsonProperty("root_cause")
  public void setRootCause(@Nullable String rootCause) {
    this.rootCause = rootCause;
  }

  public TaskResult confidence(@Nullable Float confidence) {
    this.confidence = confidence;
    return this;
  }

  /**
   * Get confidence
   * minimum: 0
   * maximum: 1
   * @return confidence
   */
  @DecimalMin(value = "0") @DecimalMax(value = "1") 
  @JsonProperty("confidence")
  public @Nullable Float getConfidence() {
    return confidence;
  }

  @JsonProperty("confidence")
  public void setConfidence(@Nullable Float confidence) {
    this.confidence = confidence;
  }

  public TaskResult evidence(List<@Valid TaskResultEvidenceInner> evidence) {
    this.evidence = evidence;
    return this;
  }

  public TaskResult addEvidenceItem(TaskResultEvidenceInner evidenceItem) {
    if (this.evidence == null) {
      this.evidence = new ArrayList<>();
    }
    this.evidence.add(evidenceItem);
    return this;
  }

  /**
   * Get evidence
   * @return evidence
   */
  
  @JsonProperty("evidence")
  public List<@Valid TaskResultEvidenceInner> getEvidence() {
    return evidence;
  }

  @JsonProperty("evidence")
  public void setEvidence(List<@Valid TaskResultEvidenceInner> evidence) {
    this.evidence = evidence;
  }

  public TaskResult citations(List<@Valid Citation> citations) {
    this.citations = citations;
    return this;
  }

  public TaskResult addCitationsItem(Citation citationsItem) {
    if (this.citations == null) {
      this.citations = new ArrayList<>();
    }
    this.citations.add(citationsItem);
    return this;
  }

  /**
   * Get citations
   * @return citations
   */
  
  @JsonProperty("citations")
  public List<@Valid Citation> getCitations() {
    return citations;
  }

  @JsonProperty("citations")
  public void setCitations(List<@Valid Citation> citations) {
    this.citations = citations;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    TaskResult taskResult = (TaskResult) o;
    return Objects.equals(this.rootCause, taskResult.rootCause) &&
        Objects.equals(this.confidence, taskResult.confidence) &&
        Objects.equals(this.evidence, taskResult.evidence) &&
        Objects.equals(this.citations, taskResult.citations);
  }

  @Override
  public int hashCode() {
    return Objects.hash(rootCause, confidence, evidence, citations);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class TaskResult {\n");
    sb.append("    rootCause: ").append(toIndentedString(rootCause)).append("\n");
    sb.append("    confidence: ").append(toIndentedString(confidence)).append("\n");
    sb.append("    evidence: ").append(toIndentedString(evidence)).append("\n");
    sb.append("    citations: ").append(toIndentedString(citations)).append("\n");
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

