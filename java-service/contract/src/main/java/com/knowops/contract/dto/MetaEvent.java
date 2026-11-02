package com.knowops.contract.dto;

import java.net.URI;
import java.util.Objects;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
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
 * MetaEvent
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class MetaEvent {

  private String traceId;

  private String contractVersion;

  private String configVersion;

  private @Nullable String promptSetSha;

  private @Nullable String aclFingerprint;

  /**
   * Gets or Sets degraded
   */
  public enum DegradedEnum {
    RERANK("rerank"),
    
    CLASSIFY("classify"),
    
    GENERATE("generate"),
    
    JUDGE("judge"),
    
    GRAPH("graph"),
    
    A2A("a2a");

    private final String value;

    DegradedEnum(String value) {
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
    public static DegradedEnum fromValue(String value) {
      for (DegradedEnum b : DegradedEnum.values()) {
        if (b.value.equals(value)) {
          return b;
        }
      }
      throw new IllegalArgumentException("Unexpected value '" + value + "'");
    }
  }

  private List<DegradedEnum> degraded = new ArrayList<>();

  public MetaEvent() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public MetaEvent(String traceId, String contractVersion, String configVersion, List<DegradedEnum> degraded) {
    this.traceId = traceId;
    this.contractVersion = contractVersion;
    this.configVersion = configVersion;
    this.degraded = degraded;
  }

  public MetaEvent traceId(String traceId) {
    this.traceId = traceId;
    return this;
  }

  /**
   * Get traceId
   * @return traceId
   */
  @NotNull 
  @JsonProperty("trace_id")
  public String getTraceId() {
    return traceId;
  }

  @JsonProperty("trace_id")
  public void setTraceId(String traceId) {
    this.traceId = traceId;
  }

  public MetaEvent contractVersion(String contractVersion) {
    this.contractVersion = contractVersion;
    return this;
  }

  /**
   * Get contractVersion
   * @return contractVersion
   */
  @NotNull 
  @JsonProperty("contract_version")
  public String getContractVersion() {
    return contractVersion;
  }

  @JsonProperty("contract_version")
  public void setContractVersion(String contractVersion) {
    this.contractVersion = contractVersion;
  }

  public MetaEvent configVersion(String configVersion) {
    this.configVersion = configVersion;
    return this;
  }

  /**
   * Get configVersion
   * @return configVersion
   */
  @NotNull 
  @JsonProperty("config_version")
  public String getConfigVersion() {
    return configVersion;
  }

  @JsonProperty("config_version")
  public void setConfigVersion(String configVersion) {
    this.configVersion = configVersion;
  }

  public MetaEvent promptSetSha(@Nullable String promptSetSha) {
    this.promptSetSha = promptSetSha;
    return this;
  }

  /**
   * 整套 prompt 的环境指纹（docs/12 §4.1）
   * @return promptSetSha
   */
  
  @JsonProperty("prompt_set_sha")
  public @Nullable String getPromptSetSha() {
    return promptSetSha;
  }

  @JsonProperty("prompt_set_sha")
  public void setPromptSetSha(@Nullable String promptSetSha) {
    this.promptSetSha = promptSetSha;
  }

  public MetaEvent aclFingerprint(@Nullable String aclFingerprint) {
    this.aclFingerprint = aclFingerprint;
    return this;
  }

  /**
   * subject_ctx 规范化后的 hash，用作语义缓存 key 的一部分。
   * @return aclFingerprint
   */
  
  @JsonProperty("acl_fingerprint")
  public @Nullable String getAclFingerprint() {
    return aclFingerprint;
  }

  @JsonProperty("acl_fingerprint")
  public void setAclFingerprint(@Nullable String aclFingerprint) {
    this.aclFingerprint = aclFingerprint;
  }

  public MetaEvent degraded(List<DegradedEnum> degraded) {
    this.degraded = degraded;
    return this;
  }

  public MetaEvent addDegradedItem(DegradedEnum degradedItem) {
    if (this.degraded == null) {
      this.degraded = new ArrayList<>();
    }
    this.degraded.add(degradedItem);
    return this;
  }

  /**
   * 显式降级标记，禁止静默降级（config/models.yaml 的纪律）。
   * @return degraded
   */
  @NotNull 
  @JsonProperty("degraded")
  public List<DegradedEnum> getDegraded() {
    return degraded;
  }

  @JsonProperty("degraded")
  public void setDegraded(List<DegradedEnum> degraded) {
    this.degraded = degraded;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    MetaEvent metaEvent = (MetaEvent) o;
    return Objects.equals(this.traceId, metaEvent.traceId) &&
        Objects.equals(this.contractVersion, metaEvent.contractVersion) &&
        Objects.equals(this.configVersion, metaEvent.configVersion) &&
        Objects.equals(this.promptSetSha, metaEvent.promptSetSha) &&
        Objects.equals(this.aclFingerprint, metaEvent.aclFingerprint) &&
        Objects.equals(this.degraded, metaEvent.degraded);
  }

  @Override
  public int hashCode() {
    return Objects.hash(traceId, contractVersion, configVersion, promptSetSha, aclFingerprint, degraded);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class MetaEvent {\n");
    sb.append("    traceId: ").append(toIndentedString(traceId)).append("\n");
    sb.append("    contractVersion: ").append(toIndentedString(contractVersion)).append("\n");
    sb.append("    configVersion: ").append(toIndentedString(configVersion)).append("\n");
    sb.append("    promptSetSha: ").append(toIndentedString(promptSetSha)).append("\n");
    sb.append("    aclFingerprint: ").append(toIndentedString(aclFingerprint)).append("\n");
    sb.append("    degraded: ").append(toIndentedString(degraded)).append("\n");
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

