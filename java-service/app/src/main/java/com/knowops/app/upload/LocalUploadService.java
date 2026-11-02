package com.knowops.app.upload;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;

/**
 * M0 骨架实现：落本地磁盘，文档记录与 outbox 投递在内存里模拟。
 *
 * <p>真实实现：落 MinIO + 写 kb_document + 写 outbox_event（同一事务）。
 * 这里把「投递解析任务」这一步保留成显式方法，M1 接真实解析链时只改这一处。
 */
@Service
public class LocalUploadService implements UploadService {

    private final Path uploadDir;

    public LocalUploadService(@Value("${knowops.upload.dir:./data/uploads}") String uploadDir) {
        this.uploadDir = Path.of(uploadDir).toAbsolutePath().normalize();
    }

    @Override
    public UploadResult upload(MultipartFile file, String ownerId, String ownerDept, String title) {
        if (file.isEmpty()) {
            throw new IllegalArgumentException("empty file");
        }
        String docId = "doc_" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        String fileKey = "uploads/" + docId + "/" + safeName(file.getOriginalFilename());
        String resolvedTitle = (title == null || title.isBlank()) ? safeName(file.getOriginalFilename()) : title;

        try {
            Path target = uploadDir.resolve(docId).resolve(safeName(file.getOriginalFilename()));
            Files.createDirectories(target.getParent());
            file.transferTo(target);
        } catch (IOException e) {
            throw new IllegalStateException("写入失败: " + e.getMessage(), e);
        }

        // M0：文档记录与解析任务投递为骨架占位（真实实现写 kb_document + outbox_event）
        enqueueParse(docId, fileKey);

        return new UploadResult(docId, "parsing", fileKey);
    }

    /** 投递解析任务：真实实现写 outbox_event(event_type='document.ingest')。 */
    private void enqueueParse(String docId, String fileKey) {
        // M0 骨架：解析链尚未接，这里只声明意图。M1 换成 outbox 写库。
    }

    private String safeName(String name) {
        if (name == null) {
            return "unnamed";
        }
        return name.replaceAll("[\\\\/:*?\"<>|]", "_");
    }
}
