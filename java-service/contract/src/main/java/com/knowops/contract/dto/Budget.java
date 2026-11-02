package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * Budget
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class Budget {

  private Integer maxTokens = 12000;

  private Integer maxSteps = 5;

  private Float maxCostUsd = 0.05f;

  public Budget maxTokens(Integer maxTokens) {
    this.maxTokens = maxTokens;
    return this;
  }

  /**
   * Get maxTokens
   * @return maxTokens
   */
  
  @JsonProperty("max_tokens")
  public Integer getMaxTokens() {
    return maxTokens;
  }

  @JsonProperty("max_tokens")
  public void setMaxTokens(Integer maxTokens) {
    this.maxTokens = maxTokens;
  }

  public Budget maxSteps(Integer maxSteps) {
    this.maxSteps = maxSteps;
    return this;
  }

  /**
   * Get maxSteps
   * @return maxSteps
   */
  
  @JsonProperty("max_steps")
  public Integer getMaxSteps() {
    return maxSteps;
  }

  @JsonProperty("max_steps")
  public void setMaxSteps(Integer maxSteps) {
    this.maxSteps = maxSteps;
  }

  public Budget maxCostUsd(Float maxCostUsd) {
    this.maxCostUsd = maxCostUsd;
    return this;
  }

  /**
   * Get maxCostUsd
   * @return maxCostUsd
   */
  
  @JsonProperty("max_cost_usd")
  public Float getMaxCostUsd() {
    return maxCostUsd;
  }

  @JsonProperty("max_cost_usd")
  public void setMaxCostUsd(Float maxCostUsd) {
    this.maxCostUsd = maxCostUsd;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    Budget budget = (Budget) o;
    return Objects.equals(this.maxTokens, budget.maxTokens) &&
        Objects.equals(this.maxSteps, budget.maxSteps) &&
        Objects.equals(this.maxCostUsd, budget.maxCostUsd);
  }

  @Override
  public int hashCode() {
    return Objects.hash(maxTokens, maxSteps, maxCostUsd);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class Budget {\n");
    sb.append("    maxTokens: ").append(toIndentedString(maxTokens)).append("\n");
    sb.append("    maxSteps: ").append(toIndentedString(maxSteps)).append("\n");
    sb.append("    maxCostUsd: ").append(toIndentedString(maxCostUsd)).append("\n");
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

