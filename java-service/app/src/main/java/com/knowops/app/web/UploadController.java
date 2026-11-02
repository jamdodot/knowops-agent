package com.knowops.app.web;

import com.knowops.app.upload.UploadResult;
import com.knowops.app.upload.UploadService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.Map;

/**
 * 文档上传（docs/04 §2 M0 验收项：上传接口）。
 *
 * <p>面向业务前端的接口，非调 Python 的通道。上传 → 落盘 + 建文档记录
 * （status=parsing）→ 投递解析任务（outbox），解析在 M1 才真正发生。
 */
@RestController
@RequestMapping("/v1/documents")
public class UploadController {

    private final UploadService uploadService;

    public UploadController(UploadService uploadService) {
        this.uploadService = uploadService;
    }

    @PostMapping
    public ResponseEntity<Map<String, Object>> upload(
            @RequestParam("file") MultipartFile file,
            @RequestParam("owner_id") String ownerId,
            @RequestParam("owner_dept") String ownerDept,
            @RequestParam(value = "title", required = false) String title) {

        UploadResult r = uploadService.upload(file, ownerId, ownerDept, title);
        return new ResponseEntity<>(Map.of(
                "doc_id", r.docId(),
                "status", r.status(),
                "file_key", r.fileKey()), HttpStatus.ACCEPTED);
    }
}
