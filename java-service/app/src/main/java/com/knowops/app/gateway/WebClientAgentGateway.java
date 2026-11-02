package com.knowops.app.gateway;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import reactor.core.publisher.Flux;

import java.util.Map;
import java.util.function.Consumer;

/**
 * WebClient 版 SSE 转发。
 *
 * <p>落地 docs/13 §5 的两条硬规则：
 * <ul>
 *   <li><b>传剩余预算</b>：X-Deadline-Ms 是相对毫秒，不是绝对时间戳（避免时钟漂移）；</li>
 *   <li><b>重试只在第一帧之前</b>：一旦收到 meta/delta，绝不重发——用户已看到内容。</li>
 * </ul>
 *
 * <p>M0 骨架用 WebClient 的 SSE 订阅把上游帧转成 SseEmitter 事件；
 * 幂等键与重试的完整落地在 M1（接真实存储做 24h 窗口），这里先固定接口与帧回调点。
 */
@Service
public class WebClientAgentGateway implements AgentGateway {

    private final Map<String, String> baseUrls;
    private final WebClient webClient;

    public WebClientAgentGateway(
            @Value("${knowops.agent.knowledge-base-url:http://localhost:8001}") String knowledgeBaseUrl,
            @Value("${knowops.agent.fulfillment-base-url:http://localhost:8002}") String fulfillmentBaseUrl) {
        this.baseUrls = Map.of(
                "knowledge", knowledgeBaseUrl,
                "fulfillment", fulfillmentBaseUrl);
        this.webClient = WebClient.builder().build();
    }

    @Override
    public SseEmitter stream(String domain, String path, Object body, Consumer<SseFrame> onFrame) {
        String base = baseUrls.getOrDefault(domain, baseUrls.get("knowledge"));
        SseEmitter emitter = new SseEmitter(120_000L); // 连接超时兜底，由 X-Deadline-Ms 主导

        Flux<ServerSentEventLine> events = webClient.post()
                .uri(base + path)
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(String.class)
                .map(ServerSentEventLine::new);

        events.subscribe(
                line -> {
                    SseFrame frame = line.toFrame();
                    if (frame != null) {
                        onFrame.accept(frame);
                        try {
                            emitter.send(SseEmitter.event()
                                    .name(frame.event())
                                    .data(frame.data(), MediaType.APPLICATION_JSON));
                        } catch (Exception ignored) {
                            // 客户端已断开：停止推（取消 = 停止产生新 token，不回滚已产生内容，13 §5.4）
                        }
                    }
                },
                error -> {
                    // 上游异常：不重试（可能已流出部分帧），以 error 事件收尾
                    try {
                        emitter.send(SseEmitter.event()
                                .name("error")
                                .data("{\"code\":\"UPSTREAM_UNAVAILABLE\",\"message\":\"upstream error\",\"retryable\":true}"));
                        emitter.complete();
                    } catch (Exception ignored) {
                    }
                },
                emitter::complete);

        return emitter;
    }

    /** 把一行 SSE 文本解析成 event + data。 */
    private record ServerSentEventLine(String raw) {
        SseFrame toFrame() {
            String event = "message";
            String data = raw;
            // 简化解析：上游 Python 的帧格式是 "event: <type>\ndata: <json>"
            for (String part : raw.split("\n")) {
                if (part.startsWith("event:")) {
                    event = part.substring("event:".length()).trim();
                } else if (part.startsWith("data:")) {
                    data = part.substring("data:".length()).trim();
                }
            }
            if (raw.startsWith(":") || raw.isBlank()) {
                return null; // 心跳 / 空帧
            }
            return new SseFrame(event, data);
        }
    }
}
