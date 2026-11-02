package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.knowops.contract.dto.ActionProposal;
import org.springframework.lang.Nullable;
import org.openapitools.jackson.nullable.JsonNullable;
import java.time.OffsetDateTime;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;


import java.util.*;
import jakarta.annotation.Generated;

/**
 * ActionProposalEvent
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class ActionProposalEvent {

  private ActionProposal approval;

  public ActionProposalEvent() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public ActionProposalEvent(ActionProposal approval) {
    this.approval = approval;
  }

  public ActionProposalEvent approval(ActionProposal approval) {
    this.approval = approval;
    return this;
  }

  /**
   * Get approval
   * @return approval
   */
  @NotNull @Valid 
  @JsonProperty("approval")
  public ActionProposal getApproval() {
    return approval;
  }

  @JsonProperty("approval")
  public void setApproval(ActionProposal approval) {
    this.approval = approval;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    ActionProposalEvent actionProposalEvent = (ActionProposalEvent) o;
    return Objects.equals(this.approval, actionProposalEvent.approval);
  }

  @Override
  public int hashCode() {
    return Objects.hash(approval);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class ActionProposalEvent {\n");
    sb.append("    approval: ").append(toIndentedString(approval)).append("\n");
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

