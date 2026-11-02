package com.knowops.app.web;

import com.knowops.app.acl.SubjectResolver;
import com.knowops.app.gateway.AgentGateway;
import com.knowops.contract.dto.AnswerRequest;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 知识问答网关（docs/13 §2：无状态，一条 SSE 流）。
 *
 * <p>流程：校验 subject_ctx（账号一票否决）→ 转发 Python → 逐帧回吐。
 * 「引用先于正文」「done 与 error 有且仅有一个」由 Python 侧保证，
 * 这里只做透传与首帧记账（幂等/重试的挂点）。
 */
@RestController
@RequestMapping("/v1/knowledge")
public class KnowledgeGatewayController {

    private final SubjectResolver subjectResolver;
    private final AgentGateway gateway;

    public KnowledgeGatewayController(SubjectResolver subjectResolver, AgentGateway gateway) {
        this.subjectResolver = subjectResolver;
        this.gateway = gateway;
    }

    @PostMapping(value = "/answers", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter answer(
            @RequestHeader(value = "X-Contract-Version", required = false) String contractVersion,
            @Valid @RequestBody AnswerRequest request) {

        // ADR-0001：主体失效一票否决，优先于任何转发
        subjectResolver.resolve(request.getSubjectCtx().getUserId());

        AtomicBoolean firstFrameSeen = new AtomicBoolean(false);
        return gateway.stream("knowledge", "/v1/knowledge/answers", request, frame -> {
            // 首帧（meta）记账点：重试只允许在此之前（13 §5.2），完整落地 M1
            if (!firstFrameSeen.getAndSet(true)) {
                onFirstFrame(frame);
            }
        });
    }

    private void onFirstFrame(AgentGateway.SseFrame frame) {
        // M1：记幂等键 -> 首帧已发出，后续重复请求返回 DUPLICATE_INFLIGHT
    }
}
