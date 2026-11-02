package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.knowops.contract.dto.Citation;
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
 * CitationEvent
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class CitationEvent {

  private List<@Valid Citation> citations = new ArrayList<>();

  public CitationEvent() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public CitationEvent(List<@Valid Citation> citations) {
    this.citations = citations;
  }

  public CitationEvent citations(List<@Valid Citation> citations) {
    this.citations = citations;
    return this;
  }

  public CitationEvent addCitationsItem(Citation citationsItem) {
    if (this.citations == null) {
      this.citations = new ArrayList<>();
    }
    this.citations.add(citationsItem);
    return this;
  }

  /**
   * Get citations
   * @return citations
   */
  @NotNull 
  @JsonProperty("citations")
  public List<@Valid Citation> getCitations() {
    return citations;
  }

  @JsonProperty("citations")
  public void setCitations(List<@Valid Citation> citations) {
    this.citations = citations;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    CitationEvent citationEvent = (CitationEvent) o;
    return Objects.equals(this.citations, citationEvent.citations);
  }

  @Override
  public int hashCode() {
    return Objects.hash(citations);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class CitationEvent {\n");
    sb.append("    citations: ").append(toIndentedString(citations)).append("\n");
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

