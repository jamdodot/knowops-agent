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
 * Citation
 */

@Generated(value = "org.openapitools.codegen.languages.SpringCodegen", date = "2026-11-02T22:39:26.284618600+08:00[Asia/Shanghai]", comments = "Generator version: 7.27.0")
public class Citation {

  private String eid;

  private String docId;

  private String title;

  private @Nullable Integer pageNo;

  private @Nullable String sectionPath;

  private @Nullable Long aclEpoch;

  public Citation() {
    super();
  }

  /**
   * Constructor with only required parameters
   */
  public Citation(String eid, String docId, String title) {
    this.eid = eid;
    this.docId = docId;
    this.title = title;
  }

  public Citation eid(String eid) {
    this.eid = eid;
    return this;
  }

  /**
   * 证据 id。正文只能引用已出现过的 eid，编号由前端渲染（引用是数据不是文本）。
   * @return eid
   */
  @NotNull 
  @JsonProperty("eid")
  public String getEid() {
    return eid;
  }

  @JsonProperty("eid")
  public void setEid(String eid) {
    this.eid = eid;
  }

  public Citation docId(String docId) {
    this.docId = docId;
    return this;
  }

  /**
   * Get docId
   * @return docId
   */
  @NotNull 
  @JsonProperty("doc_id")
  public String getDocId() {
    return docId;
  }

  @JsonProperty("doc_id")
  public void setDocId(String docId) {
    this.docId = docId;
  }

  public Citation title(String title) {
    this.title = title;
    return this;
  }

  /**
   * Get title
   * @return title
   */
  @NotNull 
  @JsonProperty("title")
  public String getTitle() {
    return title;
  }

  @JsonProperty("title")
  public void setTitle(String title) {
    this.title = title;
  }

  public Citation pageNo(@Nullable Integer pageNo) {
    this.pageNo = pageNo;
    return this;
  }

  /**
   * Get pageNo
   * minimum: 1
   * @return pageNo
   */
  @Min(value = 1) 
  @JsonProperty("page_no")
  public @Nullable Integer getPageNo() {
    return pageNo;
  }

  @JsonProperty("page_no")
  public void setPageNo(@Nullable Integer pageNo) {
    this.pageNo = pageNo;
  }

  public Citation sectionPath(@Nullable String sectionPath) {
    this.sectionPath = sectionPath;
    return this;
  }

  /**
   * Get sectionPath
   * @return sectionPath
   */
  
  @JsonProperty("section_path")
  public @Nullable String getSectionPath() {
    return sectionPath;
  }

  @JsonProperty("section_path")
  public void setSectionPath(@Nullable String sectionPath) {
    this.sectionPath = sectionPath;
  }

  public Citation aclEpoch(@Nullable Long aclEpoch) {
    this.aclEpoch = aclEpoch;
    return this;
  }

  /**
   * 引用时刻该文档的权限版本；与当前值不一致则置灰\"原文已失权\"（docs/07 §3.3）。
   * @return aclEpoch
   */
  
  @JsonProperty("acl_epoch")
  public @Nullable Long getAclEpoch() {
    return aclEpoch;
  }

  @JsonProperty("acl_epoch")
  public void setAclEpoch(@Nullable Long aclEpoch) {
    this.aclEpoch = aclEpoch;
  }

  @Override
  public boolean equals(Object o) {
    if (this == o) {
      return true;
    }
    if (o == null || getClass() != o.getClass()) {
      return false;
    }
    Citation citation = (Citation) o;
    return Objects.equals(this.eid, citation.eid) &&
        Objects.equals(this.docId, citation.docId) &&
        Objects.equals(this.title, citation.title) &&
        Objects.equals(this.pageNo, citation.pageNo) &&
        Objects.equals(this.sectionPath, citation.sectionPath) &&
        Objects.equals(this.aclEpoch, citation.aclEpoch);
  }

  @Override
  public int hashCode() {
    return Objects.hash(eid, docId, title, pageNo, sectionPath, aclEpoch);
  }

  @Override
  public String toString() {
    StringBuilder sb = new StringBuilder();
    sb.append("class Citation {\n");
    sb.append("    eid: ").append(toIndentedString(eid)).append("\n");
    sb.append("    docId: ").append(toIndentedString(docId)).append("\n");
    sb.append("    title: ").append(toIndentedString(title)).append("\n");
    sb.append("    pageNo: ").append(toIndentedString(pageNo)).append("\n");
    sb.append("    sectionPath: ").append(toIndentedString(sectionPath)).append("\n");
    sb.append("    aclEpoch: ").append(toIndentedString(aclEpoch)).append("\n");
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

