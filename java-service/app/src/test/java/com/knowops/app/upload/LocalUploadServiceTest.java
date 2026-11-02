package com.knowops.app.upload;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 上传接口单测：验证「落盘 + 建文档记录（parsing）」的基本契约。
 */
class LocalUploadServiceTest {

    @Test
    void upload_returns_parsing_status_and_doc_id() {
        LocalUploadService service = new LocalUploadService("./target/test-uploads");
        MockMultipartFile file = new MockMultipartFile(
                "file", "sop.pdf", "application/pdf", "hello".getBytes());

        UploadResult r = service.upload(file, "u_2048", "D10", null);

        assertThat(r.docId()).startsWith("doc_");
        assertThat(r.status()).isEqualTo("parsing");
        assertThat(r.fileKey()).contains("uploads/").contains("sop.pdf");
    }

    @Test
    void empty_file_is_rejected() {
        LocalUploadService service = new LocalUploadService("./target/test-uploads");
        MockMultipartFile file = new MockMultipartFile("file", "empty.pdf", "application/pdf", new byte[0]);

        org.assertj.core.api.Assertions.assertThatThrownBy(
                        () -> service.upload(file, "u_2048", "D10", null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("empty");
    }
}
