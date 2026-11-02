"""HTTP 层的共用件：契约版本、截止时间、错误映射、请求体校验。

放在一处的理由：这三个都是**每个入口都要做，且做错就一直错**的事。
比如截止时间，忘了解析 `X-Deadline-Ms` 不会报错，只会让 P95 慢慢烂掉。
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from api.schemas import Code, Error
from runtime.errors import KwError

T = TypeVar("T", bound=BaseModel)

#: 认这个主版本；次版本不做严格比对（兼容性靠"只增可选字段"保证，13 §7.1）
SUPPORTED_MAJOR = "1"


def check_contract(headers: dict[str, str], supported: str) -> None:
    """`X-Contract-Version` 双向声明；不匹配直接 426，不做静默兼容。

    静默兼容会把"版本不一致"变成"数据错乱"——后者排查成本高一个数量级（docs/13 §7）。
    请求头缺省时放行，是因为本地 curl 调试不带它；生产由网关统一注入。
    """
    declared = headers.get("x-contract-version")
    if not declared:
        return
    if declared.split(".")[0] != supported.split(".")[0]:
        raise KwError(
            Code.CONTRACT_VERSION_UNSUPPORTED,
            f"契约版本不兼容：客户端 {declared}，服务端 {supported}",
        )


def deadline_ms(headers: dict[str, str], default_ms: int) -> int:
    """`X-Deadline-Ms` 传的是**剩余毫秒**，不是绝对时间戳（docs/13 §5.1）。"""
    raw = headers.get("x-deadline-ms")
    if not raw:
        return default_ms
    try:
        value = int(raw)
    except ValueError as exc:
        raise KwError(Code.INVALID_SCHEMA, "X-Deadline-Ms 必须是整数毫秒") from exc
    if value <= 0:
        raise KwError(Code.DEADLINE_EXCEEDED, "剩余预算已耗尽")
    return min(value, default_ms)


async def parse_body(request: Request, model: type[T]) -> T:
    """JSON → pydantic。任何结构问题都落 `INVALID_SCHEMA`。

    注意 `extra="forbid"` 在这里生效：传 `allowed_doc_ids` 会得到 400，
    而不是"跑得通但设计塌了"（docs/13 §3.2 的机器守卫）。
    """
    try:
        payload: Any = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KwError(Code.INVALID_SCHEMA, "请求体不是合法 JSON") from exc
    try:
        return model(**payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        raise KwError(
            Code.INVALID_SCHEMA,
            f"请求体不合契约：{loc} {first.get('msg', '')}",
            internal=json.dumps(exc.errors()[:3], ensure_ascii=False, default=str),
        ) from exc


def error_response(exc: KwError, trace_id: str | None = None) -> JSONResponse:
    body: Error = exc.to_contract(trace_id)
    return JSONResponse(
        status_code=exc.http_status,
        content=body.model_dump(mode="json", exclude_none=True),
        headers={"X-Trace-Id": trace_id} if trace_id else None,
    )


def internal_response(message: str, trace_id: str | None = None) -> JSONResponse:
    exc = KwError(Code.INTERNAL, message)
    return error_response(exc, trace_id)
