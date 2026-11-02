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
 * Usage
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class Usage {

  private @Nullable Integer inputTokens;

  private @Nullable Integer outputTokens;

  private @Nullable Float costUsd;

  private @Nullable Integer steps;

  private @Nullable Integer retrievalRounds;

  public Usage inputTokens(@Nullable Integer inputTokens) {
    this.inputTokens = inputTokens;
    return this;
  }

  /**
   * Get inputTokens
   * @return inputTokens
   */
  
  @JsonProperty("input_tokens")
  public @Nullable Integer getInputTokens() {
    return inputTokens;
  }

  @JsonProperty("input_tokens")
  public void setInputTokens(@Nullable Integer inputTokens) {
    this.inputTokens = inputTokens;
  }

  public Usage outputTokens(@Nullable Integer outputTokens) {
    this.outputTokens = outputTokens;
    return this;
  }

  /**
   * Get outputTokens
   * @return outputTokens
   */
  
  @JsonProperty("output_tokens")
  public @Nullable Integer getOutputTokens() {
    return outputTokens;
  }

  @JsonProperty("output_tokens")
  public void setOutputTokens(@Nullable Integer outputTokens) {
    this.outputTokens = outputTokens;
  }

  public Usage costUsd(@Nullable Float costUsd) {
    this.costUsd = costUsd;
    return this;
  }

  /**
   * Get costUsd
   * @return costUsd
   */
  
  @JsonProperty("cost_usd")
  public @Nullable Float getCostUsd() {
    return costUsd;
  }

  @JsonProperty("cost_usd")
  public void setCostUsd(@Nullable Float costUsd) {
    this.costUsd = costUsd;
  }

  public Usage steps(@Nullable Integer steps) {
    this.steps = steps;
    return this;
  }

  /**
   * 图执行步数
   * @return steps
   */
  
  @JsonProperty("steps")
  public @Nullable Integer getSteps() {
    return steps;
  }

  @JsonProperty("steps")
  public void setSteps(@Nullable Integer steps) {
    this.steps = steps;
  }

  public Usage retrievalRounds(@Nullable Integer retrievalRounds) {
    this.retrievalRounds = retrievalRounds;
    return this;
  }

  /**
   * Get retrievalRounds
   * @return retrievalRounds
   */
  
  @JsonProperty("retrieval_rounds")
  public @Nullable Integer getRetrievalRounds() {
    return retrievalRounds;
  }

  @JsonProperty("retrieval_rounds")
  public void setRetrievalRounds(@Nullable Integer retrievalRounds) {
    this.retrievalRounds = retrievalRounds;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    Usage usage = (Usage) o;
    return Objects.equals(this.inputTokens, usage.inputTokens) &&
        Objects.equals(this.outputTokens, usage.outputTokens) &&
        Objects.equals(this.costUsd, usage.costUsd) &&
        Objects.equals(this.steps, usage.steps) &&
        Objects.equals(this.retrievalRounds, usage.retrievalRounds);
  }

  @Override
  public int hashCode() {
    return Objects.hash(inputTokens, outputTokens, costUsd, steps, retrievalRounds);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class Usage {\n");
    sb.append("    inputTokens: ").append(toIndentedString(inputTokens)).append("\n");
    sb.append("    outputTokens: ").append(toIndentedString(outputTokens)).append("\n");
    sb.append("    costUsd: ").append(toIndentedString(costUsd)).append("\n");
    sb.append("    steps: ").append(toIndentedString(steps)).append("\n");
    sb.append("    retrievalRounds: ").append(toIndentedString(retrievalRounds)).append("\n");
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

