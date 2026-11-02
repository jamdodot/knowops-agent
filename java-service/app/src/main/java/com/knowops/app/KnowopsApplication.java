package com.knowops.app;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * KnowOps 业务服务入口。
 *
 * <p>职责边界（docs/01 §2、docs/13 §1）：这是<b>应用</b>，不是 Agent——
 * 鉴权、上传、审批状态、部门变更等强事务逻辑留在这里，
 * Agent 执行（知识检索 / 履约诊断）在 Python 侧。
 *
 * <p>M0 骨架阶段：数据库访问走接口抽象（内存实现），不真连 PG/MySQL；
 * Agent 网关用 WebClient 转发到 Python 的 SSE 入口（默认 8001/8002）。
 */
@SpringBootApplication
public class KnowopsApplication {

    public static void main(String[] args) {
        SpringApplication.run(KnowopsApplication.class, args);
    }
}
