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
 * Error
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class Error {

  /**
   * Gets or Sets code
   */
  public enum CodeEnum {
    INVALID_SCHEMA("INVALID_SCHEMA"),
    
    SUBJECT_MISSING("SUBJECT_MISSING"),
    
    SUBJECT_DISABLED("SUBJECT_DISABLED"),
    
    CONTRACT_VERSION_UNSUPPORTED("CONTRACT_VERSION_UNSUPPORTED"),
    
    RATE_LIMITED("RATE_LIMITED"),
    
    UPSTREAM_UNAVAILABLE("UPSTREAM_UNAVAILABLE"),
    
    MODEL_TIMEOUT("MODEL_TIMEOUT"),
    
    DEADLINE_EXCEEDED("DEADLINE_EXCEEDED"),
    
    BUDGET_EXCEEDED("BUDGET_EXCEEDED"),
    
    ACL_SNAPSHOT_STALE("ACL_SNAPSHOT_STALE"),
    
    INJECTION_BLOCKED("INJECTION_BLOCKED"),
    
    DUPLICATE_INFLIGHT("DUPLICATE_INFLIGHT"),
    
    TASK_NOT_FOUND("TASK_NOT_FOUND"),
    
    TASK_STATE_CONFLICT("TASK_STATE_CONFLICT"),
    
    INTERNAL("INTERNAL");

    private final String value;

    CodeEnum(String value) {
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
    public static CodeEnum fromValue(String value) {
      for (CodeEnum b : CodeEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private CodeEnum code;

  private String message;

  private Boolean retryable;

  private @Nullable Integer retryAfterMs;

  private @Nullable String traceId;

  public Error() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public Error(CodeEnum code, String message, Boolean retryable) {
    this.code = code;
    this.message = message;
    this.retryable = retryable;
  }

  public Error code(CodeEnum code) {
    this.code = code;
    return this;
  }

  /**
   * Get code
   * @return code
   */
  @NotNull 
  @JsonProperty("code")
  public CodeEnum getCode() {
    return code;
  }

  @JsonProperty("code")
  public void setCode(CodeEnum code) {
    this.code = code;
  }

  public Error message(String message) {
    this.message = message;
    return this;
  }

  /**
   * 必须脱敏（不含文档正文、手机号、内部代号）。
   * @return message
   */
  @NotNull 
  @JsonProperty("message")
  public String getMessage() {
    return message;
  }

  @JsonProperty("message")
  public void setMessage(String message) {
    this.message = message;
  }

  public Error retryable(Boolean retryable) {
    this.retryable = retryable;
    return this;
  }

  /**
   * Get retryable
   * @return retryable
   */
  @NotNull 
  @JsonProperty("retryable")
  public Boolean getRetryable() {
    return retryable;
  }

  @JsonProperty("retryable")
  public void setRetryable(Boolean retryable) {
    this.retryable = retryable;
  }

  public Error retryAfterMs(@Nullable Integer retryAfterMs) {
    this.retryAfterMs = retryAfterMs;
    return this;
  }

  /**
   * Get retryAfterMs
   * @return retryAfterMs
   */
  
  @JsonProperty("retry_after_ms")
  public @Nullable Integer getRetryAfterMs() {
    return retryAfterMs;
  }

  @JsonProperty("retry_after_ms")
  public void setRetryAfterMs(@Nullable Integer retryAfterMs) {
    this.retryAfterMs = retryAfterMs;
  }

  public Error traceId(@Nullable String traceId) {
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

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    Error error = (Error) o;
    return Objects.equals(this.code, error.code) &&
        Objects.equals(this.message, error.message) &&
        Objects.equals(this.retryable, error.retryable) &&
        Objects.equals(this.retryAfterMs, error.retryAfterMs) &&
        Objects.equals(this.traceId, error.traceId);
  }

  @Override
  public int hashCode() {
    return Objects.hash(code, message, retryable, retryAfterMs, traceId);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class Error {\n");
    sb.append("    code: ").append(toIndentedString(code)).append("\n");
    sb.append("    message: ").append(toIndentedString(message)).append("\n");
    sb.append("    retryable: ").append(toIndentedString(retryable)).append("\n");
    sb.append("    retryAfterMs: ").append(toIndentedString(retryAfterMs)).append("\n");
    sb.append("    traceId: ").append(toIndentedString(traceId)).append("\n");
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

