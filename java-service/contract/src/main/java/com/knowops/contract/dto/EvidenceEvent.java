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
 * EvidenceEvent
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class EvidenceEvent {

  private String hypothesisId;

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

  private VerdictEnum verdict;

  private @Nullable String summary;

  public EvidenceEvent() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public EvidenceEvent(String hypothesisId, VerdictEnum verdict) {
    this.hypothesisId = hypothesisId;
    this.verdict = verdict;
  }

  public EvidenceEvent hypothesisId(String hypothesisId) {
    this.hypothesisId = hypothesisId;
    return this;
  }

  /**
   * Get hypothesisId
   * @return hypothesisId
   */
  @NotNull 
  @JsonProperty("hypothesis_id")
  public String getHypothesisId() {
    return hypothesisId;
  }

  @JsonProperty("hypothesis_id")
  public void setHypothesisId(String hypothesisId) {
    this.hypothesisId = hypothesisId;
  }

  public EvidenceEvent verdict(VerdictEnum verdict) {
    this.verdict = verdict;
    return this;
  }

  /**
   * Get verdict
   * @return verdict
   */
  @NotNull 
  @JsonProperty("verdict")
  public VerdictEnum getVerdict() {
    return verdict;
  }

  @JsonProperty("verdict")
  public void setVerdict(VerdictEnum verdict) {
    this.verdict = verdict;
  }

  public EvidenceEvent summary(@Nullable String summary) {
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
    EvidenceEvent evidenceEvent = (EvidenceEvent) o;
    return Objects.equals(this.hypothesisId, evidenceEvent.hypothesisId) &&
        Objects.equals(this.verdict, evidenceEvent.verdict) &&
        Objects.equals(this.summary, evidenceEvent.summary);
  }

  @Override
  public int hashCode() {
    return Objects.hash(hypothesisId, verdict, summary);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class EvidenceEvent {\n");
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

