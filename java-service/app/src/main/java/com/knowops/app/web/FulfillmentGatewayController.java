package com.knowops.app.web;

import com.knowops.app.acl.SubjectResolver;
import com.knowops.app.gateway.AgentGateway;
import com.knowops.contract.dto.FulfillmentTaskRequest;
import com.knowops.contract.dto.ResumeRequest;
import com.knowops.contract.dto.Task;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * 排障诊断任务网关（docs/13 §2：有状态 Task，断流 ≠ 失败）。
 *
 * <p>创建 / resume 是 SSE 流（手写 SseEmitter）；GET 状态与 cancel 走 JSON。
 * 「审批中断 → input_required → 流正常关闭 → resume」的完整状态机在 Python 侧，
 * 这里只透传并做主体一票否决校验。
 */
@RestController
@RequestMapping("/v1/fulfillment")
public class FulfillmentGatewayController {

    private final SubjectResolver subjectResolver;
    private final AgentGateway gateway;

    public FulfillmentGatewayController(SubjectResolver subjectResolver, AgentGateway gateway) {
        this.subjectResolver = subjectResolver;
        this.gateway = gateway;
    }

    @PostMapping(value = "/tasks", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter createTask(
            @RequestHeader(value = "X-Contract-Version", required = false) String contractVersion,
            @Valid @RequestBody FulfillmentTaskRequest request) {

        subjectResolver.resolve(request.getSubjectCtx().getUserId());
        return gateway.stream("fulfillment", "/v1/fulfillment/tasks", request, frame -> {
        });
    }

    @PostMapping(value = "/tasks/{task_id}/resume", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter resume(
            @PathVariable("task_id") String taskId,
            @Valid @RequestBody ResumeRequest request) {

        // docs/13 §5 出口 6 / resume 描述：恢复必须重传 subject_ctx 并重新校验
        subjectResolver.resolve(request.getSubjectCtx().getUserId());
        return gateway.stream("fulfillment", "/v1/fulfillment/tasks/" + taskId + "/resume", request, frame -> {
        });
    }

    @GetMapping("/tasks/{task_id}")
    public ResponseEntity<Task> getTask(
            @PathVariable("task_id") String taskId) {
        // M0 骨架：GET 状态由 Python 侧维护 Task 表；这里占位返回，M1 接真实查询
        return ResponseEntity.notFound().build();
    }

    @PostMapping("/tasks/{task_id}/cancel")
    public ResponseEntity<Task> cancel(@PathVariable("task_id") String taskId) {
        // M0 骨架：取消 = 停止产生新 token，不回滚已产生内容（13 §5.4）
        return ResponseEntity.notFound().build();
    }
}
