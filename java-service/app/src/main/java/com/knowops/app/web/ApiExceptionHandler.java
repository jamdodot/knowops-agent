package com.knowops.app.web;

import com.knowops.app.acl.SubjectDisabledException;
import com.knowops.contract.dto.Error;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 把业务异常映射成契约错误（docs/13 §6）。
 *
 * <p>注意：INJECTION_BLOCKED 是安全能力证据，不该与 5xx 混在错误率里；
 * SUBJECT_DISABLED 是 403 一票否决。这里只处理 Java 侧自己抛的异常，
 * 上游 Python 的 error 事件原样转发，不在此二次包装。
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(SubjectDisabledException.class)
    public ResponseEntity<Error> subjectDisabled(SubjectDisabledException ex) {
        return err(HttpStatus.FORBIDDEN, "SUBJECT_DISABLED",
                "账号已停用或不存在", false, ex);
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Error> badRequest(IllegalArgumentException ex) {
        return err(HttpStatus.BAD_REQUEST, "INVALID_SCHEMA", ex.getMessage(), false, ex);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Error> internal(Exception ex) {
        return err(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL", "内部错误", false, ex);
    }

    private ResponseEntity<Error> err(HttpStatus status, String code, String msg,
                                      boolean retryable, Exception ex) {
        Error e = new Error();
        e.setCode(Error.CodeEnum.valueOf(code));
        e.setMessage(msg);
        e.setRetryable(retryable);
        return new ResponseEntity<>(e, status);
    }
}
