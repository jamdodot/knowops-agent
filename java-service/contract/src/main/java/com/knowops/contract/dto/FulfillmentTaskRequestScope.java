package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonTypeName;
import com.knowops.contract.dto.FulfillmentTaskRequestScopeTimeRange;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * 跨域/跨服务的最小数据范围，不是权限清单（docs/05 §7）。
 */

@JsonTypeName("FulfillmentTaskRequest_scope")
@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class FulfillmentTaskRequestScope {

  private List<String> warehouseIds = new ArrayList<>();

  private @Nullable FulfillmentTaskRequestScopeTimeRange timeRange;

  public FulfillmentTaskRequestScope warehouseIds(List<String> warehouseIds) {
    this.warehouseIds = warehouseIds;
    return this;
  }

  public FulfillmentTaskRequestScope addWarehouseIdsItem(String warehouseIdsItem) {
    if (this.warehouseIds == null) {
      this.warehouseIds = new ArrayList<>();
    }
    this.warehouseIds.add(warehouseIdsItem);
    return this;
  }

  /**
   * Get warehouseIds
   * @return warehouseIds
   */
  
  @JsonProperty("warehouse_ids")
  public List<String> getWarehouseIds() {
    return warehouseIds;
  }

  @JsonProperty("warehouse_ids")
  public void setWarehouseIds(List<String> warehouseIds) {
    this.warehouseIds = warehouseIds;
  }

  public FulfillmentTaskRequestScope timeRange(@Nullable FulfillmentTaskRequestScopeTimeRange timeRange) {
    this.timeRange = timeRange;
    return this;
  }

  /**
   * Get timeRange
   * @return timeRange
   */
  @Valid 
  @JsonProperty("time_range")
  public @Nullable FulfillmentTaskRequestScopeTimeRange getTimeRange() {
    return timeRange;
  }

  @JsonProperty("time_range")
  public void setTimeRange(@Nullable FulfillmentTaskRequestScopeTimeRange timeRange) {
    this.timeRange = timeRange;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    FulfillmentTaskRequestScope fulfillmentTaskRequestScope = (FulfillmentTaskRequestScope) o;
    return Objects.equals(this.warehouseIds, fulfillmentTaskRequestScope.warehouseIds) &&
        Objects.equals(this.timeRange, fulfillmentTaskRequestScope.timeRange);
  }

  @Override
  public int hashCode() {
    return Objects.hash(warehouseIds, timeRange);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class FulfillmentTaskRequestScope {\n");
    sb.append("    warehouseIds: ").append(toIndentedString(warehouseIds)).append("\n");
    sb.append("    timeRange: ").append(toIndentedString(timeRange)).append("\n");
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

