package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
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
 * DeltaEvent
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class DeltaEvent {

  private String text;

  private List<String> eids = new ArrayList<>();

  public DeltaEvent() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public DeltaEvent(String text) {
    this.text = text;
  }

  public DeltaEvent text(String text) {
    this.text = text;
    return this;
  }

  /**
   * Get text
   * @return text
   */
  @NotNull 
  @JsonProperty("text")
  public String getText() {
    return text;
  }

  @JsonProperty("text")
  public void setText(String text) {
    this.text = text;
  }

  public DeltaEvent eids(List<String> eids) {
    this.eids = eids;
    return this;
  }

  public DeltaEvent addEidsItem(String eidsItem) {
    if (this.eids == null) {
      this.eids = new ArrayList<>();
    }
    this.eids.add(eidsItem);
    return this;
  }

  /**
   * 本段文本引用的证据 id（正文里是占位符，不是 [1][2]）。
   * @return eids
   */
  
  @JsonProperty("eids")
  public List<String> getEids() {
    return eids;
  }

  @JsonProperty("eids")
  public void setEids(List<String> eids) {
    this.eids = eids;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    DeltaEvent deltaEvent = (DeltaEvent) o;
    return Objects.equals(this.text, deltaEvent.text) &&
        Objects.equals(this.eids, deltaEvent.eids);
  }

  @Override
  public int hashCode() {
    return Objects.hash(text, eids);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class DeltaEvent {\n");
    sb.append("    text: ").append(toIndentedString(text)).append("\n");
    sb.append("    eids: ").append(toIndentedString(eids)).append("\n");
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

