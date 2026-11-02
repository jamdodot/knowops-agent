package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import com.knowops.contract.dto.TaskPending;
import com.knowops.contract.dto.TaskResult;
import com.knowops.contract.dto.Usage;
import java.time.OffsetDateTime;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * Task
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class Task {

  private String taskId;

  /**
   * 与 A2A 的 TASK_STATE_* 一一对应，同一套状态机两种绑定（docs/13 §8）。
   */
  public enum StateEnum {
    SUBMITTED("submitted"),
    
    WORKING("working"),
    
    INPUT_REQUIRED("input_required"),
    
    COMPLETED("completed"),
    
    FAILED("failed"),
    
    CANCELED("canceled");

    private final String value;

    StateEnum(String value) {
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
    public static StateEnum fromValue(String value) {
      for (StateEnum b : StateEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private StateEnum state;

  private String sessionId;

  private @Nullable TaskPending pending;

  private @Nullable TaskResult result;

  private @Nullable Usage usage;

  private @Nullable String traceId;

  @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
  private OffsetDateTime createdAt;

  @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
  private @Nullable OffsetDateTime updatedAt;

  public Task() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public Task(String taskId, StateEnum state, String sessionId, OffsetDateTime createdAt) {
    this.taskId = taskId;
    this.state = state;
    this.sessionId = sessionId;
    this.createdAt = createdAt;
  }

  public Task taskId(String taskId) {
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

  public Task state(StateEnum state) {
    this.state = state;
    return this;
  }

  /**
   * 与 A2A 的 TASK_STATE_* 一一对应，同一套状态机两种绑定（docs/13 §8）。
   * @return state
   */
  @NotNull 
  @JsonProperty("state")
  public StateEnum getState() {
    return state;
  }

  @JsonProperty("state")
  public void setState(StateEnum state) {
    this.state = state;
  }

  public Task sessionId(String sessionId) {
    this.sessionId = sessionId;
    return this;
  }

  /**
   * Get sessionId
   * @return sessionId
   */
  @NotNull 
  @JsonProperty("session_id")
  public String getSessionId() {
    return sessionId;
  }

  @JsonProperty("session_id")
  public void setSessionId(String sessionId) {
    this.sessionId = sessionId;
  }

  public Task pending(@Nullable TaskPending pending) {
    this.pending = pending;
    return this;
  }

  /**
   * Get pending
   * @return pending
   */
  @Valid 
  @JsonProperty("pending")
  public @Nullable TaskPending getPending() {
    return pending;
  }

  @JsonProperty("pending")
  public void setPending(@Nullable TaskPending pending) {
    this.pending = pending;
  }

  public Task result(@Nullable TaskResult result) {
    this.result = result;
    return this;
  }

  /**
   * Get result
   * @return result
   */
  @Valid 
  @JsonProperty("result")
  public @Nullable TaskResult getResult() {
    return result;
  }

  @JsonProperty("result")
  public void setResult(@Nullable TaskResult result) {
    this.result = result;
  }

  public Task usage(@Nullable Usage usage) {
    this.usage = usage;
    return this;
  }

  /**
   * Get usage
   * @return usage
   */
  @Valid 
  @JsonProperty("usage")
  public @Nullable Usage getUsage() {
    return usage;
  }

  @JsonProperty("usage")
  public void setUsage(@Nullable Usage usage) {
    this.usage = usage;
  }

  public Task traceId(@Nullable String traceId) {
    this.traceId = traceId;
    return this;
  }

  /**
   * Get traceId
   * @return traceId
   */
  
  @JsonProperty("trace_id")
  public @Nullable String getTraceId() {
    return traceId;
  }

  @JsonProperty("trace_id")
  public void setTraceId(@Nullable String traceId) {
    this.traceId = traceId;
  }

  public Task createdAt(OffsetDateTime createdAt) {
    this.createdAt = createdAt;
    return this;
  }

  /**
   * Get createdAt
   * @return createdAt
   */
  @NotNull @Valid 
  @JsonProperty("created_at")
  public OffsetDateTime getCreatedAt() {
    return createdAt;
  }

  @JsonProperty("created_at")
  public void setCreatedAt(OffsetDateTime createdAt) {
    this.createdAt = createdAt;
  }

  public Task updatedAt(@Nullable OffsetDateTime updatedAt) {
    this.updatedAt = updatedAt;
    return this;
  }

  /**
   * Get updatedAt
   * @return updatedAt
   */
  @Valid 
  @JsonProperty("updated_at")
  public @Nullable OffsetDateTime getUpdatedAt() {
    return updatedAt;
  }

  @JsonProperty("updated_at")
  public void setUpdatedAt(@Nullable OffsetDateTime updatedAt) {
    this.updatedAt = updatedAt;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    Task task = (Task) o;
    return Objects.equals(this.taskId, task.taskId) &&
        Objects.equals(this.state, task.state) &&
        Objects.equals(this.sessionId, task.sessionId) &&
        Objects.equals(this.pending, task.pending) &&
        Objects.equals(this.result, task.result) &&
        Objects.equals(this.usage, task.usage) &&
        Objects.equals(this.traceId, task.traceId) &&
        Objects.equals(this.createdAt, task.createdAt) &&
        Objects.equals(this.updatedAt, task.updatedAt);
  }

  @Override
  public int hashCode() {
    return Objects.hash(taskId, state, sessionId, pending, result, usage, traceId, createdAt, updatedAt);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class Task {\n");
    sb.append("    taskId: ").append(toIndentedString(taskId)).append("\n");
    sb.append("    state: ").append(toIndentedString(state)).append("\n");
    sb.append("    sessionId: ").append(toIndentedString(sessionId)).append("\n");
    sb.append("    pending: ").append(toIndentedString(pending)).append("\n");
    sb.append("    result: ").append(toIndentedString(result)).append("\n");
    sb.append("    usage: ").append(toIndentedString(usage)).append("\n");
    sb.append("    traceId: ").append(toIndentedString(traceId)).append("\n");
    sb.append("    createdAt: ").append(toIndentedString(createdAt)).append("\n");
    sb.append("    updatedAt: ").append(toIndentedString(updatedAt)).append("\n");
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

