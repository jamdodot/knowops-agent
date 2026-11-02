package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonTypeName;
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
 * FulfillmentTaskRequestScopeTimeRange
 */

@JsonTypeName("FulfillmentTaskRequest_scope_time_range")
@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class FulfillmentTaskRequestScopeTimeRange {

  @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
  private @Nullable OffsetDateTime from;

  @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
  private @Nullable OffsetDateTime to;

  public FulfillmentTaskRequestScopeTimeRange from(@Nullable OffsetDateTime from) {
    this.from = from;
    return this;
  }

  /**
   * Get from
   * @return from
   */
  @Valid 
  @JsonProperty("from")
  public @Nullable OffsetDateTime getFrom() {
    return from;
  }

  @JsonProperty("from")
  public void setFrom(@Nullable OffsetDateTime from) {
    this.from = from;
  }

  public FulfillmentTaskRequestScopeTimeRange to(@Nullable OffsetDateTime to) {
    this.to = to;
    return this;
  }

  /**
   * Get to
   * @return to
   */
  @Valid 
  @JsonProperty("to")
  public @Nullable OffsetDateTime getTo() {
    return to;
  }

  @JsonProperty("to")
  public void setTo(@Nullable OffsetDateTime to) {
    this.to = to;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    FulfillmentTaskRequestScopeTimeRange fulfillmentTaskRequestScopeTimeRange = (FulfillmentTaskRequestScopeTimeRange) o;
    return Objects.equals(this.from, fulfillmentTaskRequestScopeTimeRange.from) &&
        Objects.equals(this.to, fulfillmentTaskRequestScopeTimeRange.to);
  }

  @Override
  public int hashCode() {
    return Objects.hash(from, to);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class FulfillmentTaskRequestScopeTimeRange {\n");
    sb.append("    from: ").append(toIndentedString(from)).append("\n");
    sb.append("    to: ").append(toIndentedString(to)).append("\n");
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

