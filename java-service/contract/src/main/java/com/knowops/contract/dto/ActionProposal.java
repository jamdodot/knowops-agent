package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
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
 * Agent 只提议不执行；真实执行由 Java 在审批通过后进行（docs/05 §7）。
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class ActionProposal {

  private String approvalId;

  private String actionType;

  private Map<String, Object> params = new HashMap<>();

  /**
   * Gets or Sets riskLevel
   */
  public enum RiskLevelEnum {
    LOW("low"),
    
    MEDIUM("medium"),
    
    HIGH("high");

    private final String value;

    RiskLevelEnum(String value) {
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
    public static RiskLevelEnum fromValue(String value) {
      for (RiskLevelEnum b : RiskLevelEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private RiskLevelEnum riskLevel;

  public ActionProposal() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public ActionProposal(String approvalId, String actionType, RiskLevelEnum riskLevel) {
    this.approvalId = approvalId;
    this.actionType = actionType;
    this.riskLevel = riskLevel;
  }

  public ActionProposal approvalId(String approvalId) {
    this.approvalId = approvalId;
    return this;
  }

  /**
   * Get approvalId
   * @return approvalId
   */
  @NotNull 
  @JsonProperty("approval_id")
  public String getApprovalId() {
    return approvalId;
  }

  @JsonProperty("approval_id")
  public void setApprovalId(String approvalId) {
    this.approvalId = approvalId;
  }

  public ActionProposal actionType(String actionType) {
    this.actionType = actionType;
    return this;
  }

  /**
   * Get actionType
   * @return actionType
   */
  @NotNull 
  @JsonProperty("action_type")
  public String getActionType() {
    return actionType;
  }

  @JsonProperty("action_type")
  public void setActionType(String actionType) {
    this.actionType = actionType;
  }

  public ActionProposal params(Map<String, Object> params) {
    this.params = params;
    return this;
  }

  public ActionProposal putParamsItem(String key, Object paramsItem) {
    if (this.params == null) {
      this.params = new HashMap<>();
    }
    this.params.put(key, paramsItem);
    return this;
  }

  /**
   * Get params
   * @return params
   */
  
  @JsonProperty("params")
  public Map<String, Object> getParams() {
    return params;
  }

  @JsonProperty("params")
  public void setParams(Map<String, Object> params) {
    this.params = params;
  }

  public ActionProposal riskLevel(RiskLevelEnum riskLevel) {
    this.riskLevel = riskLevel;
    return this;
  }

  /**
   * Get riskLevel
   * @return riskLevel
   */
  @NotNull 
  @JsonProperty("risk_level")
  public RiskLevelEnum getRiskLevel() {
    return riskLevel;
  }

  @JsonProperty("risk_level")
  public void setRiskLevel(RiskLevelEnum riskLevel) {
    this.riskLevel = riskLevel;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    ActionProposal actionProposal = (ActionProposal) o;
    return Objects.equals(this.approvalId, actionProposal.approvalId) &&
        Objects.equals(this.actionType, actionProposal.actionType) &&
        Objects.equals(this.params, actionProposal.params) &&
        Objects.equals(this.riskLevel, actionProposal.riskLevel);
  }

  @Override
  public int hashCode() {
    return Objects.hash(approvalId, actionType, params, riskLevel);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class ActionProposal {\n");
    sb.append("    approvalId: ").append(toIndentedString(approvalId)).append("\n");
    sb.append("    actionType: ").append(toIndentedString(actionType)).append("\n");
    sb.append("    params: ").append(toIndentedString(params)).append("\n");
    sb.append("    riskLevel: ").append(toIndentedString(riskLevel)).append("\n");
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

