"""工具注册表。

`docs/01` §3.1 要求工具"统一 schema 声明、参数校验、超时重试、幂等键、结果压缩、成本标注"。
这六件事都在这里做，理由是：只要允许某个工具绕过注册表，这六件事就变成"看实现者心情"，
而工具层的三个归因项（F3 取证失败 / F4 工具选错 / 结果噪声）全靠它们才有数据。

两个刻意的取舍：
1. **参数校验在注册表做，不在工具里做。** 工具里校验等于每个工具各写一套，迟早漏。
2. **结果压缩在注册表做，不在节点里做。** 07 §2.1 说工具结果超 1500 token 要立刻压——
   放到节点里做，就会出现"这个节点忘了压"，然后上下文爆掉。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from . import tracing
from .errors import KwError
from api.schemas import Code

#: 单条工具结果的压缩阈值（字符，约等于 token 的 1/1.5～1/2）
_COMPRESS_AT = 1500
_KEEP_ROWS = 20


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Any]
    timeout_ms: int = 3_000
    idempotent: bool = False
    #: 成本提示（例如"一次 SQL 扫 200 万行"），只用于排序与告警，不参与计费
    cost_hint: str | None = None


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    summary: str = ""
    rows: int = 0
    error: str | None = None
    attempt: int = 1
    elapsed_ms: float = 0.0
    args_hash: str = ""
    compressed: bool = False

    def text(self) -> str:
        return self.summary or ("" if self.data is None else json.dumps(self.data, ensure_ascii=False))


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")

    # ------------------------------------------------------------------ 注册
    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        *,
        timeout_ms: int = 3_000,
        idempotent: bool = False,
        cost_hint: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """装饰器式注册。工具函数签名约定：fn(**args) -> Any。"""

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            if name in self._specs:
                raise ValueError(f"工具重复注册：{name}")
            self._specs[name] = ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema,
                fn=fn,
                timeout_ms=timeout_ms,
                idempotent=idempotent,
                cost_hint=cost_hint,
            )
            return fn

        return deco

    def require(self, names: list[str]) -> None:
        """pack 声明的工具必须全部已注册，缺一个就启动失败。

        这条检查救的是"改了 domain.yaml 但忘了写工具"这类错误——它不会在启动时报错，
        只会在跑到那个节点时 500，而那时你已经以为是模型问题了。
        """
        missing = [n for n in names if n not in self._specs]
        if missing:
            raise ValueError(f"domain.yaml 声明了未注册的工具：{missing}")

    def names(self) -> list[str]:
        return sorted(self._specs)

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KwError(Code.INTERNAL, f"未注册的工具 {name}") from exc

    def descriptions(self) -> list[str]:
        """给 prompt 用的工具说明。工具描述也算 prompt（docs/12 §2）。"""
        return [f"{s.name}: {s.description}" for s in sorted(self._specs.values(), key=lambda x: x.name)]

    # ------------------------------------------------------------------ 调用
    def call(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        spec = self.spec(name)
        args = args or {}
        args_hash = _args_hash(args)

        try:
            jsonschema.validate(instance=args, schema=spec.input_schema)
        except jsonschema.ValidationError as exc:
            # 参数不合法属于调用方 bug，不要重试、也不要当成工具故障（F3/F4 要分得开）
            return ToolResult(ok=False, error=f"invalid_args: {exc.message}", args_hash=args_hash)

        started = time.perf_counter()
        # contextvars 不会自动跨线程传递：工具要读运行上下文（主体权限）与当前 span，
        # 必须显式复制。漏了这一步的表现是"工具里取不到 subject，于是不召回"——
        # 一个静默的空结果，比报错难查得多。
        copied = copy_context()
        future = self._pool.submit(copied.run, lambda: spec.fn(**args))
        try:
            data = future.result(timeout=spec.timeout_ms / 1000)
        except FutureTimeout:
            future.cancel()
            elapsed = (time.perf_counter() - started) * 1000
            with tracing.span(
                "shared.tool.call",
                {
                    "knowops.tool.name": name,
                    "knowops.tool.args_hash": args_hash,
                    "knowops.tool.args_keys": sorted(args),
                    "knowops.tool.timeout_ms": spec.timeout_ms,
                    "knowops.tool.attempt": 1,
                    "knowops.tool.rows": 0,
                },
            ) as sp:
                sp.set_status(_error_status())
            return ToolResult(
                ok=False, error="timeout", elapsed_ms=elapsed, args_hash=args_hash, attempt=1
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - started) * 1000
            return ToolResult(
                ok=False, error=f"{type(exc).__name__}: {exc}", elapsed_ms=elapsed, args_hash=args_hash
            )

        elapsed = (time.perf_counter() - started) * 1000
        summary, rows, compressed = _compress(data)
        with tracing.span(
            "shared.tool.call",
            {
                "knowops.tool.name": name,
                "knowops.tool.args_hash": args_hash,
                "knowops.tool.args_keys": sorted(args),
                "knowops.tool.timeout_ms": spec.timeout_ms,
                "knowops.tool.attempt": 1,
                "knowops.tool.rows": rows,
                "knowops.tool.elapsed_ms": round(elapsed, 2),
                "knowops.tool.compressed": compressed,
            },
        ):
            pass
        return ToolResult(
            ok=True,
            data=data,
            summary=summary,
            rows=rows,
            elapsed_ms=elapsed,
            args_hash=args_hash,
            compressed=compressed,
        )

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _error_status():  # 小工具，避免在 call 里重复 import
    from opentelemetry.trace import Status, StatusCode

    return Status(StatusCode.ERROR)


def _args_hash(args: dict[str, Any]) -> str:
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _compress(data: Any) -> tuple[str, int, bool]:
    """L1 压缩：保留列名 + 前 N 行 + 行数（docs/07 §2.2）。

    明细不进上下文，但必须可回溯——所以给的是"共 N 行 + 前 20 行 + 一个可再取的标识"。
    直接截断字符串是最差的做法：会截在半行 JSON 上，模型会把它当成完整数据。
    """
    if isinstance(data, list):
        rows = len(data)
        if rows == 0:
            return "（空结果）", 0, False
        if all(isinstance(r, dict) for r in data):
            cols = sorted({k for r in data for k in r})  # type: ignore[union-attr]
            head = data[:_KEEP_ROWS]
            lines = [f"共 {rows} 行；列：{', '.join(cols)}"]
            for i, r in enumerate(head, 1):
                cells = ", ".join(f"{k}={_short(v)}" for k, v in r.items())
                lines.append(f"{i}. {cells}")
            if rows > _KEEP_ROWS:
                lines.append(f"…其余 {rows - _KEEP_ROWS} 行已省略（明细可用同一入参重新取回）")
            text = "\n".join(lines)
        else:
            text = f"共 {rows} 项\n" + "\n".join(f"- {_short(v)}" for v in data[:_KEEP_ROWS])
        compressed = len(text) > _COMPRESS_AT
        return _truncate(text), rows, compressed

    if isinstance(data, dict):
        text = json.dumps(data, ensure_ascii=False, indent=None)
        return _truncate(text), 1, len(text) > _COMPRESS_AT

    text = str(data)
    return _truncate(text), 1, len(text) > _COMPRESS_AT


def _short(v: Any) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 60 else s[:57] + "…"


def _truncate(text: str, limit: int = 2_000) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n…（已截断，原长 {len(text)} 字符）"
