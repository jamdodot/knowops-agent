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
 * ProgressEvent
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class ProgressEvent {

  private String node;

  private Integer step;

  private @Nullable String note;

  public ProgressEvent() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public ProgressEvent(String node, Integer step) {
    this.node = node;
    this.step = step;
  }

  public ProgressEvent node(String node) {
    this.node = node;
    return this;
  }

  /**
   * Get node
   * @return node
   */
  @NotNull 
  @JsonProperty("node")
  public String getNode() {
    return node;
  }

  @JsonProperty("node")
  public void setNode(String node) {
    this.node = node;
  }

  public ProgressEvent step(Integer step) {
    this.step = step;
    return this;
  }

  /**
   * Get step
   * @return step
   */
  @NotNull 
  @JsonProperty("step")
  public Integer getStep() {
    return step;
  }

  @JsonProperty("step")
  public void setStep(Integer step) {
    this.step = step;
  }

  public ProgressEvent note(@Nullable String note) {
    this.note = note;
    return this;
  }

  /**
   * Get note
   * @return note
   */
  
  @JsonProperty("note")
  public @Nullable String getNote() {
    return note;
  }

  @JsonProperty("note")
  public void setNote(@Nullable String note) {
    this.note = note;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    ProgressEvent progressEvent = (ProgressEvent) o;
    return Objects.equals(this.node, progressEvent.node) &&
        Objects.equals(this.step, progressEvent.step) &&
        Objects.equals(this.note, progressEvent.note);
  }

  @Override
  public int hashCode() {
    return Objects.hash(node, step, note);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class ProgressEvent {\n");
    sb.append("    node: ").append(toIndentedString(node)).append("\n");
    sb.append("    step: ").append(toIndentedString(step)).append("\n");
    sb.append("    note: ").append(toIndentedString(note)).append("\n");
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

