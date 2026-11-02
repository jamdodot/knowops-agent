package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import com.knowops.contract.dto.Citation;
import com.knowops.contract.dto.Usage;
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
 * DoneEvent
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class DoneEvent {

  /**
   * abstained 是设计内行为，不是错误（docs/01 §6）。
   */
  public enum FinishReasonEnum {
    ANSWERED("answered"),
    
    ABSTAINED("abstained"),
    
    ESCALATED("escalated"),
    
    DEGRADED("degraded");

    private final String value;

    FinishReasonEnum(String value) {
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
    public static FinishReasonEnum fromValue(String value) {
      for (FinishReasonEnum b : FinishReasonEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private FinishReasonEnum finishReason;

  private Usage usage;

  private List<@Valid Citation> citations = new ArrayList<>();

  private @Nullable String taskId;

  public DoneEvent() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public DoneEvent(FinishReasonEnum finishReason, Usage usage) {
    this.finishReason = finishReason;
    this.usage = usage;
  }

  public DoneEvent finishReason(FinishReasonEnum finishReason) {
    this.finishReason = finishReason;
    return this;
  }

  /**
   * abstained 是设计内行为，不是错误（docs/01 §6）。
   * @return finishReason
   */
  @NotNull 
  @JsonProperty("finish_reason")
  public FinishReasonEnum getFinishReason() {
    return finishReason;
  }

  @JsonProperty("finish_reason")
  public void setFinishReason(FinishReasonEnum finishReason) {
    this.finishReason = finishReason;
  }

  public DoneEvent usage(Usage usage) {
    this.usage = usage;
    return this;
  }

  /**
   * Get usage
   * @return usage
   */
  @NotNull @Valid 
  @JsonProperty("usage")
  public Usage getUsage() {
    return usage;
  }

  @JsonProperty("usage")
  public void setUsage(Usage usage) {
    this.usage = usage;
  }

  public DoneEvent citations(List<@Valid Citation> citations) {
    this.citations = citations;
    return this;
  }

  public DoneEvent addCitationsItem(Citation citationsItem) {
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

  public DoneEvent taskId(@Nullable String taskId) {
    this.taskId = taskId;
    return this;
  }

  /**
   * Get taskId
   * @return taskId
   */
  
  @JsonProperty("task_id")
  public @Nullable String getTaskId() {
    return taskId;
  }

  @JsonProperty("task_id")
  public void setTaskId(@Nullable String taskId) {
    this.taskId = taskId;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    DoneEvent doneEvent = (DoneEvent) o;
    return Objects.equals(this.finishReason, doneEvent.finishReason) &&
        Objects.equals(this.usage, doneEvent.usage) &&
        Objects.equals(this.citations, doneEvent.citations) &&
        Objects.equals(this.taskId, doneEvent.taskId);
  }

  @Override
  public int hashCode() {
    return Objects.hash(finishReason, usage, citations, taskId);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class DoneEvent {\n");
    sb.append("    finishReason: ").append(toIndentedString(finishReason)).append("\n");
    sb.append("    usage: ").append(toIndentedString(usage)).append("\n");
    sb.append("    citations: ").append(toIndentedString(citations)).append("\n");
    sb.append("    taskId: ").append(toIndentedString(taskId)).append("\n");
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

