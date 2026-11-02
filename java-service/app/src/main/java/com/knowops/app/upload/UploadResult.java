package com.knowops.app.upload;

import java.util.List;

/**
 * 上传结果。
 *
 * @param docId   文档 id（kb_document.id）
 * @param status  初始状态（M0 恒为 parsing）
 * @param fileKey 落盘 key（M0 本地路径；M1 接 MinIO 后是对象 key）
 */
public record UploadResult(String docId, String status, String fileKey) {
}
