package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import com.knowops.contract.dto.Budget;
import com.knowops.contract.dto.SubjectContext;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * AnswerRequest
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class AnswerRequest {

  private String query;

  private SubjectContext subjectCtx;

  private String sessionId;

  private @Nullable String threadId;

  private @Nullable Integer turnSeq;

  private @Nullable Budget budget;

  private @Nullable String configVersion;

  /**
   * 仅用于评测复现，不参与线上分流逻辑。
   */
  public enum DomainHintEnum {
    AUTO("auto"),
    
    FACT("fact"),
    
    MULTI_HOP("multi_hop"),
    
    CHITCHAT("chitchat");

    private final String value;

    DomainHintEnum(String value) {
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
    public static DomainHintEnum fromValue(String value) {
      for (DomainHintEnum b : DomainHintEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private DomainHintEnum domainHint = DomainHintEnum.AUTO;

  public AnswerRequest() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public AnswerRequest(String query, SubjectContext subjectCtx, String sessionId) {
    this.query = query;
    this.subjectCtx = subjectCtx;
    this.sessionId = sessionId;
  }

  public AnswerRequest query(String query) {
    this.query = query;
    return this;
  }

  /**
   * Get query
   * @return query
   */
  @NotNull @Size(min = 1, max = 2000) 
  @JsonProperty("query")
  public String getQuery() {
    return query;
  }

  @JsonProperty("query")
  public void setQuery(String query) {
    this.query = query;
  }

  public AnswerRequest subjectCtx(SubjectContext subjectCtx) {
    this.subjectCtx = subjectCtx;
    return this;
  }

  /**
   * Get subjectCtx
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

  public AnswerRequest sessionId(String sessionId) {
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

  public AnswerRequest threadId(@Nullable String threadId) {
    this.threadId = threadId;
    return this;
  }

  /**
   * 图 checkpoint 的 key（docs/07 §3.1）；缺省取 session_id。
   * @return threadId
   */
  
  @JsonProperty("thread_id")
  public @Nullable String getThreadId() {
    return threadId;
  }

  @JsonProperty("thread_id")
  public void setThreadId(@Nullable String threadId) {
    this.threadId = threadId;
  }

  public AnswerRequest turnSeq(@Nullable Integer turnSeq) {
    this.turnSeq = turnSeq;
    return this;
  }

  /**
   * 会话内轮次，参与幂等键与 chat_turn 关联。
   * minimum: 1
   * @return turnSeq
   */
  @Min(value = 1) 
  @JsonProperty("turn_seq")
  public @Nullable Integer getTurnSeq() {
    return turnSeq;
  }

  @JsonProperty("turn_seq")
  public void setTurnSeq(@Nullable Integer turnSeq) {
    this.turnSeq = turnSeq;
  }

  public AnswerRequest budget(@Nullable Budget budget) {
    this.budget = budget;
    return this;
  }

  /**
   * Get budget
   * @return budget
   */
  @Valid 
  @JsonProperty("budget")
  public @Nullable Budget getBudget() {
    return budget;
  }

  @JsonProperty("budget")
  public void setBudget(@Nullable Budget budget) {
    this.budget = budget;
  }

  public AnswerRequest configVersion(@Nullable String configVersion) {
    this.configVersion = configVersion;
    return this;
  }

  /**
   * 灰度切流用；缺省取服务端当前生效版本（docs/12 §5.1）。
   * @return configVersion
   */
  
  @JsonProperty("config_version")
  public @Nullable String getConfigVersion() {
    return configVersion;
  }

  @JsonProperty("config_version")
  public void setConfigVersion(@Nullable String configVersion) {
    this.configVersion = configVersion;
  }

  public AnswerRequest domainHint(DomainHintEnum domainHint) {
    this.domainHint = domainHint;
    return this;
  }

  /**
   * 仅用于评测复现，不参与线上分流逻辑。
   * @return domainHint
   */
  
  @JsonProperty("domain_hint")
  public DomainHintEnum getDomainHint() {
    return domainHint;
  }

  @JsonProperty("domain_hint")
  public void setDomainHint(DomainHintEnum domainHint) {
    this.domainHint = domainHint;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    AnswerRequest answerRequest = (AnswerRequest) o;
    return Objects.equals(this.query, answerRequest.query) &&
        Objects.equals(this.subjectCtx, answerRequest.subjectCtx) &&
        Objects.equals(this.sessionId, answerRequest.sessionId) &&
        Objects.equals(this.threadId, answerRequest.threadId) &&
        Objects.equals(this.turnSeq, answerRequest.turnSeq) &&
        Objects.equals(this.budget, answerRequest.budget) &&
        Objects.equals(this.configVersion, answerRequest.configVersion) &&
        Objects.equals(this.domainHint, answerRequest.domainHint);
  }

  @Override
  public int hashCode() {
    return Objects.hash(query, subjectCtx, sessionId, threadId, turnSeq, budget, configVersion, domainHint);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class AnswerRequest {\n");
    sb.append("    query: ").append(toIndentedString(query)).append("\n");
    sb.append("    subjectCtx: ").append(toIndentedString(subjectCtx)).append("\n");
    sb.append("    sessionId: ").append(toIndentedString(sessionId)).append("\n");
    sb.append("    threadId: ").append(toIndentedString(threadId)).append("\n");
    sb.append("    turnSeq: ").append(toIndentedString(turnSeq)).append("\n");
    sb.append("    budget: ").append(toIndentedString(budget)).append("\n");
    sb.append("    configVersion: ").append(toIndentedString(configVersion)).append("\n");
    sb.append("    domainHint: ").append(toIndentedString(domainHint)).append("\n");
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

