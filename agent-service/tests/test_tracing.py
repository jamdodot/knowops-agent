"""traceparent 跨进程传播 + OTel 埋点骨架。

docs/14 §3：跨进程用 W3C traceparent。这里证明两件事：
1. inject 能生成合法的 traceparent 头；
2. extract 能还原出同一个 trace_id（跨进程关联的前提）。
"""

from __future__ import annotations

from runtime import tracing


def test_inject_produces_traceparent():
    tracing.setup("memory")
    with tracing.span("test.inject"):
        headers = tracing.inject({})
    assert "traceparent" in headers
    # W3C 格式：version-traceid-spanid-flags
    parts = headers["traceparent"].split("-")
    assert len(parts) == 4
    assert len(parts[1]) == 32  # trace id
    assert len(parts[2]) == 16  # span id


def test_extract_roundtrip_same_trace_id():
    tracing.setup("memory")
    with tracing.span("producer"):
        headers = tracing.inject({})
        original = tracing.current_trace_id()
    # 模拟跨进程：新上下文里 extract，还原出同一 trace
    ctx = tracing.extract(headers)
    token = tracing.attach(ctx)
    try:
        assert tracing.current_trace_id() == original
    finally:
        tracing.detach(token)


def test_span_tree_shows_hierarchy():
    tracing.setup("memory")
    trace_id = None
    with tracing.span("root"):
        with tracing.span("child"):
            pass
        trace_id = tracing.current_trace_id()
    tree = tracing.span_tree(trace_id)
    assert tree, "内存导出模式下 span_tree 不应为空"
    names = {n["name"] for n in tree}
    assert "root" in names
