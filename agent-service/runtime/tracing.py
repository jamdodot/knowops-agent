"""OpenTelemetry 埋点。

规范见 docs/14-Span规范.md。这里只实现规范要求的骨架，不做"通用封装"：
- span 名 `{domain}.{layer}.{operation}`，维度一律进属性（14 §2）；
- 采样本地全采（14 §6，tail-based 采样要 collector，M5 才上）；
- 跨进程用 W3C traceparent，不自己做上下文传递（14 §3）。

一个刻意的设计：默认导出到内存。M0 没有 collector，但"树对不对"必须能当场验证——
所以提供 `span_tree()`，`/_debug/trace/{id}` 直接吐 JSON 树。
接 Jaeger/Langfuse 时把 KW_TRACE_EXPORT 设成 otlp 即可，代码不改。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_LOCK = threading.Lock()
_PROVIDER: TracerProvider | None = None
_MEMORY_EXPORTER: InMemorySpanExporter | None = None
_PROPAGATOR = TraceContextTextMapPropagator()

SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "knowops-agent")


def setup(export: str | None = None) -> TracerProvider:
    """初始化 TracerProvider（幂等）。

    export: memory | console | otlp | none，默认读 KW_TRACE_EXPORT，再默认 memory。
    注意 otlp 需要额外安装 opentelemetry-exporter-otlp；没装时降级为 memory 并打印原因，
    因为"埋点库缺失导致服务起不来"是不可接受的失败模式。
    """
    global _PROVIDER, _MEMORY_EXPORTER
    with _LOCK:
        if _PROVIDER is not None:
            return _PROVIDER

        provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
        mode = (export or os.environ.get("KW_TRACE_EXPORT", "memory")).lower()

        # 本地/CI 100% 全采（14 §6）；线上 head 采样要等 collector，M5 再改。
        exporter: SpanExporter | None = None
        if mode == "memory":
            _MEMORY_EXPORTER = InMemorySpanExporter()
            exporter = _MEMORY_EXPORTER
        elif mode == "console":
            exporter = ConsoleSpanExporter()
        elif mode == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter()
            except ImportError:
                print("[tracing] 未安装 opentelemetry-exporter-otlp，降级为内存导出")
                _MEMORY_EXPORTER = InMemorySpanExporter()
                exporter = _MEMORY_EXPORTER
        # mode == "none" → 不挂 processor，span 仍生成但不导出（用于压测关掉观测开销）

        if exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        _PROVIDER = provider
        return provider


def tracer() -> trace.Tracer:
    return trace.get_tracer("knowops")


def clean_attrs(kv: Mapping[str, Any]) -> dict[str, Any]:
    """丢掉 None / 空容器：OTel 不接受 None，而且空值进属性只会污染基数。"""
    out: dict[str, Any] = {}
    for k, v in kv.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            if not v:
                continue
            out[k] = [str(i) for i in v]
            continue
        out[k] = v
    return out


@contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
    """开一个 span。异常自动记 error 状态，不需要调用方手动处理。"""
    with tracer().start_as_current_span(name, attributes=clean_attrs(attributes or {})) as sp:
        try:
            yield sp
        except Exception as exc:  # noqa: BLE001
            sp.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def set_attrs(sp: Span, **kv: Any) -> None:
    for k, v in clean_attrs(kv).items():
        sp.set_attribute(k, v)


def current_span() -> Span:
    return trace.get_current_span()


def current_trace_id() -> str:
    """32 位十六进制；没有活跃 span 时返回空串，由调用方决定怎么兜底。"""
    ctx = current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else ""


def current_span_id() -> str:
    ctx = current_span().get_span_context()
    return format(ctx.span_id, "016x") if ctx.is_valid else ""


def extract(headers: Mapping[str, str]) -> otel_context.Context:
    """从入站请求头还原上游上下文（Java 侧的 traceparent）。"""
    return _PROPAGATOR.extract(carrier={k.lower(): v for k, v in headers.items()})


def inject(headers: dict[str, str]) -> dict[str, str]:
    """出站调用（A2A 等）注入 traceparent。"""
    _PROPAGATOR.inject(carrier=headers)
    return headers


def attach(ctx: otel_context.Context) -> object:
    return otel_context.attach(ctx)


def detach(token: object) -> None:
    """容忍跨上下文 detach。

    SSE 的 span 在事件循环里 attach，却在工作线程里 end——
    而线程拿到的是复制出来的 context，token 属于原 context，直接 detach 会抛
    "Token was created in a different Context"。日志里刷这一条非常误导人
    （看起来像 tracing 坏了），所以这里显式吞掉：span.end() 才是真正要紧的事。
    """
    try:
        otel_context.detach(token)  # type: ignore[arg-type]
    except (ValueError, RuntimeError):
        pass


def begin_http_span(
    method: str,
    route: str,
    headers: Mapping[str, str],
    *,
    domain: str,
    service_version: str = "",
) -> tuple[Span, object]:
    """手写 http 层 span，而不是引 opentelemetry-instrumentation-fastapi。

    两个理由：
    1. 自动埋点的 span 名是 `POST /v1/...`，不符合 14 §2 的命名规范；
    2. SSE 的 span 必须覆盖整个流的生命周期，instrumentor 会在响应返回时就结束 span，
       那样 http 层耗时永远是 1ms，等于没埋。

    返回 (span, context_token)：调用方负责在**流结束时** end + detach。
    """
    ctx = extract(headers)
    # 注意必须把 span 挂进上下文：只 start_span 不 set_span_in_context 的话，
    # 后续 `current_trace_id()` 拿不到它，整条 trace 会变成"有 span 但 trace_id 为空"。
    sp = tracer().start_span(
        f"{domain}.http.serve",
        context=ctx,
        attributes=clean_attrs(
            {
                "knowops.domain": domain,
                "http.request.method": method,
                "http.route": route,
                "service.version": service_version or None,
            }
        ),
    )
    token = otel_context.attach(trace.set_span_in_context(sp, ctx))
    return sp, token


def end_http_span(sp: Span, token: object, *, status_code: int = 200) -> None:
    """结束 http span。

    刻意**不 detach**：SSE 的 span 在事件循环里 attach，在工作线程里 end，
    而线程拿到的是复制出来的 context——token 属于原 context，detach 必然失败，
    且 OTel 自己会打一条 "Failed to detach context" 的误导性日志。
    span.end() 才是导出所需；被 attach 的上下文随请求结束一起消失，不构成泄漏。
    """
    with trace.use_span(sp, end_on_exit=False):
        set_attrs(sp, **{"http.response.status_code": status_code})
        if status_code >= 500:
            sp.set_status(Status(StatusCode.ERROR))
    sp.end()


def span_tree(trace_id: str) -> list[dict[str, Any]]:
    """把一条 trace 的 span 还原成树（给 /_debug/trace 用，也用于验收脚本）。

    只支持内存导出模式。生产接 Jaeger/Langfuse 后这个接口自动返回空——
    它是开发期断言工具，不是产品接口。
    """
    if _MEMORY_EXPORTER is None:
        return []
    spans = [s for s in _MEMORY_EXPORTER.get_finished_spans() if format(s.context.trace_id, "032x") == trace_id]
    return _build_tree(spans)


def _build_tree(spans: list[ReadableSpan]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for s in spans:
        start_ns = s.start_time or 0
        end_ns = s.end_time or start_ns
        nodes[format(s.context.span_id, "016x")] = {
            "name": s.name,
            "span_id": format(s.context.span_id, "016x"),
            "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
            "duration_ms": round((end_ns - start_ns) / 1e6, 3),
            "status": "error" if s.status.status_code is StatusCode.ERROR else "ok",
            "attributes": dict(s.attributes or {}),
            "children": [],
        }
    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"] or "")
        (parent["children"] if parent else roots).append(node)

    def sort_rec(items: list[dict[str, Any]]) -> None:
        items.sort(key=lambda n: n["duration_ms"], reverse=True)
        for i in items:
            sort_rec(i["children"])

    sort_rec(roots)
    return roots
