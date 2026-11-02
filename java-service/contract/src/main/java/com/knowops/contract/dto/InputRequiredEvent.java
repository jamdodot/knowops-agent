package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import com.knowops.contract.dto.ActionProposal;
import java.util.HashMap;
import java.util.Map;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * InputRequiredEvent
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class InputRequiredEvent {

  private String taskId;

  /**
   * Gets or Sets kind
   */
  public enum KindEnum {
    APPROVAL("approval"),
    
    CLARIFICATION("clarification");

    private final String value;

    KindEnum(String value) {
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
    public static KindEnum fromValue(String value) {
      for (KindEnum b : KindEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private KindEnum kind;

  private @Nullable ActionProposal approval;

  private @Nullable String question;

  private Map<String, Object> schema = new HashMap<>();

  public InputRequiredEvent() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public InputRequiredEvent(String taskId, KindEnum kind) {
    this.taskId = taskId;
    this.kind = kind;
  }

  public InputRequiredEvent taskId(String taskId) {
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

  public InputRequiredEvent kind(KindEnum kind) {
    this.kind = kind;
    return this;
  }

  /**
   * Get kind
   * @return kind
   */
  @NotNull 
  @JsonProperty("kind")
  public KindEnum getKind() {
    return kind;
  }

  @JsonProperty("kind")
  public void setKind(KindEnum kind) {
    this.kind = kind;
  }

  public InputRequiredEvent approval(@Nullable ActionProposal approval) {
    this.approval = approval;
    return this;
  }

  /**
   * Get approval
   * @return approval
   */
  @Valid 
  @JsonProperty("approval")
  public @Nullable ActionProposal getApproval() {
    return approval;
  }

  @JsonProperty("approval")
  public void setApproval(@Nullable ActionProposal approval) {
    this.approval = approval;
  }

  public InputRequiredEvent question(@Nullable String question) {
    this.question = question;
    return this;
  }

  /**
   * Get question
   * @return question
   */
  
  @JsonProperty("question")
  public @Nullable String getQuestion() {
    return question;
  }

  @JsonProperty("question")
  public void setQuestion(@Nullable String question) {
    this.question = question;
  }

  public InputRequiredEvent schema(Map<String, Object> schema) {
    this.schema = schema;
    return this;
  }

  public InputRequiredEvent putSchemaItem(String key, Object schemaItem) {
    if (this.schema == null) {
      this.schema = new HashMap<>();
    }
    this.schema.put(key, schemaItem);
    return this;
  }

  /**
   * kind=clarification 时，期望补充的字段契约。
   * @return schema
   */
  
  @JsonProperty("schema")
  public Map<String, Object> getSchema() {
    return schema;
  }

  @JsonProperty("schema")
  public void setSchema(Map<String, Object> schema) {
    this.schema = schema;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    InputRequiredEvent inputRequiredEvent = (InputRequiredEvent) o;
    return Objects.equals(this.taskId, inputRequiredEvent.taskId) &&
        Objects.equals(this.kind, inputRequiredEvent.kind) &&
        Objects.equals(this.approval, inputRequiredEvent.approval) &&
        Objects.equals(this.question, inputRequiredEvent.question) &&
        Objects.equals(this.schema, inputRequiredEvent.schema);
  }

  @Override
  public int hashCode() {
    return Objects.hash(taskId, kind, approval, question, schema);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class InputRequiredEvent {\n");
    sb.append("    taskId: ").append(toIndentedString(taskId)).append("\n");
    sb.append("    kind: ").append(toIndentedString(kind)).append("\n");
    sb.append("    approval: ").append(toIndentedString(approval)).append("\n");
    sb.append("    question: ").append(toIndentedString(question)).append("\n");
    sb.append("    schema: ").append(toIndentedString(schema)).append("\n");
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

