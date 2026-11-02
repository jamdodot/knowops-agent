package com.knowops.app.upload;

import org.springframework.web.multipart.MultipartFile;

/**
 * 文档上传（docs/01 §数据流：上传 → 落盘 + kb_document(parsing) → 投递解析任务）。
 *
 * <p>M0 只做「落盘 + 建文档记录 + 写 outbox 投递解析任务」三步，
 * 解析本身在 M1（接真实解析链）才实现。
 */
public interface UploadService {

    /**
     * @param file      上传文件（PDF / Word / Markdown）
     * @param ownerId   上传人
     * @param ownerDept 上传人所属部门（决定初始 ACL 归属）
     * @param title     文档标题（可缺省，缺省取文件名）
     */
    UploadResult upload(MultipartFile file, String ownerId, String ownerDept, String title);
}
