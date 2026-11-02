"""错误码与异常。

错误码的唯一真相源是 api/openapi.yaml 的 `Error.code` 枚举（docs/13 §6）。
本模块只做两件事：把异常映射成契约里的 code，以及把 code 映射成 HTTP 状态与 retryable。

不要在别处再定义一套错误码——两套码表一定会漂移，而漂移的表现是
"线上 500 但监控面板看不到"，比直接报错难查得多。
"""

from __future__ import annotations

from http import HTTPStatus

from api.schemas import Code, Error

#: code → (HTTP 状态, 是否可重试)。retryable 决定调用方要不要退避重试，
#: 这是错误码唯一真正有用的维度（docs/13 §6）。
CODE_TABLE: dict[Code, tuple[int, bool]] = {
    Code.INVALID_SCHEMA: (HTTPStatus.BAD_REQUEST, False),
    Code.SUBJECT_MISSING: (HTTPStatus.UNAUTHORIZED, False),
    Code.SUBJECT_DISABLED: (HTTPStatus.FORBIDDEN, False),
    Code.CONTRACT_VERSION_UNSUPPORTED: (HTTPStatus.UPGRADE_REQUIRED, False),
    Code.RATE_LIMITED: (HTTPStatus.TOO_MANY_REQUESTS, True),
    Code.UPSTREAM_UNAVAILABLE: (HTTPStatus.BAD_GATEWAY, True),
    Code.MODEL_TIMEOUT: (HTTPStatus.GATEWAY_TIMEOUT, True),
    Code.DEADLINE_EXCEEDED: (HTTPStatus.GATEWAY_TIMEOUT, False),
    Code.BUDGET_EXCEEDED: (HTTPStatus.PAYMENT_REQUIRED, False),
    Code.ACL_SNAPSHOT_STALE: (HTTPStatus.CONFLICT, False),
    Code.INJECTION_BLOCKED: (HTTPStatus.UNPROCESSABLE_ENTITY, False),
    Code.DUPLICATE_INFLIGHT: (HTTPStatus.CONFLICT, False),
    Code.TASK_NOT_FOUND: (HTTPStatus.NOT_FOUND, False),
    Code.TASK_STATE_CONFLICT: (HTTPStatus.CONFLICT, False),
    Code.INTERNAL: (HTTPStatus.INTERNAL_SERVER_ERROR, False),
}


class KwError(Exception):
    """可映射到契约错误码的业务异常。

    message 会直接进响应体，所以**必须脱敏**（docs/13 §6「message 必须脱敏」）。
    需要保留原始细节的，写 `internal` 字段——它只进日志与 span，不进响应。
    """

    def __init__(
        self,
        code: Code,
        message: str,
        *,
        internal: str | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.internal = internal
        self.retry_after_ms = retry_after_ms

    @property
    def http_status(self) -> int:
        return CODE_TABLE[self.code][0]

    @property
    def retryable(self) -> bool:
        return CODE_TABLE[self.code][1]

    def to_contract(self, trace_id: str | None = None) -> Error:
        return Error(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            retry_after_ms=self.retry_after_ms,
            trace_id=trace_id,
        )


class BudgetStop(Exception):
    """预算硬停（docs/01 §6 停止条件）。

    它**不是** KwError：超预算不是"出错"，是设计内的终止路径，
    由域声明的 `stop.on_budget` 决定落成 abstained 还是 escalated（docs/13 §6 口径 1）。
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"budget stop: {reason}")
        self.reason = reason  # steps | tokens | cost | deadline
        self.detail = detail


class ContractViolation(Exception):
    """契约不变量被破坏（例如 SSE 少终止事件、两帧 meta）。

    这类问题出现即是 bug，不允许被业务代码 catch 后继续——
    静默吞掉它的结果就是"客户端永远挂着"或"状态机打架"（docs/13 §4.1）。
    """
