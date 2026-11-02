package com.knowops.app.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 健康检查。契约里这两个端点挂在 Python 入口上，
 * Java 业务服务也提供同名端点，方便统一探活。
 */
@RestController
public class HealthController {

    @GetMapping("/healthz")
    public Map<String, Object> healthz() {
        return Map.of("status", "ok", "domain", "java-gateway");
    }

    @GetMapping("/readyz")
    public Map<String, Object> readyz() {
        return Map.of("status", "ready");
    }
}
