package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.knowops.contract.dto.Budget;
import com.knowops.contract.dto.FulfillmentTaskRequestScope;
import com.knowops.contract.dto.SubjectContext;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * FulfillmentTaskRequest
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class FulfillmentTaskRequest {

  private String symptom;

  private SubjectContext subjectCtx;

  private String sessionId;

  private @Nullable FulfillmentTaskRequestScope scope;

  private @Nullable Budget budget;

  private @Nullable String configVersion;

  public FulfillmentTaskRequest() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public FulfillmentTaskRequest(String symptom, SubjectContext subjectCtx, String sessionId) {
    this.symptom = symptom;
    this.subjectCtx = subjectCtx;
    this.sessionId = sessionId;
  }

  public FulfillmentTaskRequest symptom(String symptom) {
    this.symptom = symptom;
    return this;
  }

  /**
   * Get symptom
   * @return symptom
   */
  @NotNull @Size(min = 1, max = 2000) 
  @JsonProperty("symptom")
  public String getSymptom() {
    return symptom;
  }

  @JsonProperty("symptom")
  public void setSymptom(String symptom) {
    this.symptom = symptom;
  }

  public FulfillmentTaskRequest subjectCtx(SubjectContext subjectCtx) {
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

  public FulfillmentTaskRequest sessionId(String sessionId) {
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

  public FulfillmentTaskRequest scope(@Nullable FulfillmentTaskRequestScope scope) {
    this.scope = scope;
    return this;
  }

  /**
   * Get scope
   * @return scope
   */
  @Valid 
  @JsonProperty("scope")
  public @Nullable FulfillmentTaskRequestScope getScope() {
    return scope;
  }

  @JsonProperty("scope")
  public void setScope(@Nullable FulfillmentTaskRequestScope scope) {
    this.scope = scope;
  }

  public FulfillmentTaskRequest budget(@Nullable Budget budget) {
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

  public FulfillmentTaskRequest configVersion(@Nullable String configVersion) {
    this.configVersion = configVersion;
    return this;
  }

  /**
   * Get configVersion
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

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    FulfillmentTaskRequest fulfillmentTaskRequest = (FulfillmentTaskRequest) o;
    return Objects.equals(this.symptom, fulfillmentTaskRequest.symptom) &&
        Objects.equals(this.subjectCtx, fulfillmentTaskRequest.subjectCtx) &&
        Objects.equals(this.sessionId, fulfillmentTaskRequest.sessionId) &&
        Objects.equals(this.scope, fulfillmentTaskRequest.scope) &&
        Objects.equals(this.budget, fulfillmentTaskRequest.budget) &&
        Objects.equals(this.configVersion, fulfillmentTaskRequest.configVersion);
  }

  @Override
  public int hashCode() {
    return Objects.hash(symptom, subjectCtx, sessionId, scope, budget, configVersion);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class FulfillmentTaskRequest {\n");
    sb.append("    symptom: ").append(toIndentedString(symptom)).append("\n");
    sb.append("    subjectCtx: ").append(toIndentedString(subjectCtx)).append("\n");
    sb.append("    sessionId: ").append(toIndentedString(sessionId)).append("\n");
    sb.append("    scope: ").append(toIndentedString(scope)).append("\n");
    sb.append("    budget: ").append(toIndentedString(budget)).append("\n");
    sb.append("    configVersion: ").append(toIndentedString(configVersion)).append("\n");
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

