package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonTypeName;
import com.fasterxml.jackson.annotation.JsonValue;
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
 * GetReadyz200Response
 */

@JsonTypeName("getReadyz_200_response")
@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class GetReadyz200Response {

  /**
   * Gets or Sets status
   */
  public enum StatusEnum {
    READY("ready"),
    
    DEGRADED("degraded");

    private final String value;

    StatusEnum(String value) {
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
    public static StatusEnum fromValue(String value) {
      for (StatusEnum b : StatusEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private @Nullable StatusEnum status;

  /**
   * Gets or Sets inner
   */
  public enum InnerEnum {
    OK("ok"),
    
    TIMEOUT("timeout"),
    
    ERROR("error");

    private final String value;

    InnerEnum(String value) {
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
    public static InnerEnum fromValue(String value) {
      for (InnerEnum b : InnerEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private Map<String, InnerEnum> deps = new HashMap<>();

  public GetReadyz200Response status(@Nullable StatusEnum status) {
    this.status = status;
    return this;
  }

  /**
   * Get status
   * @return status
   */
  
  @JsonProperty("status")
  public @Nullable StatusEnum getStatus() {
    return status;
  }

  @JsonProperty("status")
  public void setStatus(@Nullable StatusEnum status) {
    this.status = status;
  }

  public GetReadyz200Response deps(Map<String, InnerEnum> deps) {
    this.deps = deps;
    return this;
  }

  public GetReadyz200Response putDepsItem(String key, InnerEnum depsItem) {
    if (this.deps == null) {
      this.deps = new HashMap<>();
    }
    this.deps.put(key, depsItem);
    return this;
  }

  /**
   * Get deps
   * @return deps
   */
  
  @JsonProperty("deps")
  public Map<String, InnerEnum> getDeps() {
    return deps;
  }

  @JsonProperty("deps")
  public void setDeps(Map<String, InnerEnum> deps) {
    this.deps = deps;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    GetReadyz200Response getReadyz200Response = (GetReadyz200Response) o;
    return Objects.equals(this.status, getReadyz200Response.status) &&
        Objects.equals(this.deps, getReadyz200Response.deps);
  }

  @Override
  public int hashCode() {
    return Objects.hash(status, deps);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class GetReadyz200Response {\n");
    sb.append("    status: ").append(toIndentedString(status)).append("\n");
    sb.append("    deps: ").append(toIndentedString(deps)).append("\n");
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

