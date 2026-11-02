package com.knowops.app.gateway;

import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.function.Consumer;

/**
 * Agent 网关：把前端请求转发到 Python 的 SSE 入口。
 *
 * <p>三个 SSE 方法（知识问答 / 创建任务 / resume）在契约里生成的是
 * {@code ResponseEntity<String>}（openapi-generator 不会从 text/event-stream
 * 推断出 SseEmitter），所以手写——但请求体与事件 DTO 仍来自 codegen，
 * 契约一致性保在载荷层（docs/13 §7）。
 *
 * <p>本接口封装「转发 + SSE 帧逐条吐出」的公共逻辑，三个 Controller 只关心
 * 各自的请求体与目标入口，不重复写流式转发。
 */
public interface AgentGateway {

    /**
     * 把一条 SSE 流式请求转发到 Python，逐帧回吐给 SseEmitter。
     *
     * @param domain  目标域（knowledge / fulfillment），决定 base-url
     * @param path    目标路径（如 /v1/knowledge/answers）
     * @param body    请求体 JSON
     * @param onFrame 每收到一帧（event + data）的回调；用于幂等记账与降级标记采集
     * @return 已连到上游的 SseEmitter
     */
    SseEmitter stream(String domain, String path, Object body, Consumer<SseFrame> onFrame);

    /** 一帧 SSE 事件。 */
    record SseFrame(String event, String data) {
    }
}
