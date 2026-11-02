"""SSE 传输层。

只做三件事：帧格式化、线程 → 事件循环的桥接、空闲心跳。

心率那条不是装饰：反代（Nginx / 网关）默认 60s 空闲就断流，
而排障域的审批可能等几分钟——没有心跳，连接会先于业务被掐掉（docs/13 §4.1）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

#: 心跳间隔。低于网关空闲超时的一半比较稳（60s / 2）
HEARTBEAT_S = 15

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # 关掉 Nginx 的缓冲，否则流会被攒成一次性响应，"流式"名存实亡
    "X-Accel-Buffering": "no",
}


def frame(seq: int, event: str, data: str) -> str:
    """一帧：id / event / data(JSON)。data 一定是 JSON，不做裸文本（docs/13 §4.1）。"""
    return f"id: {seq}\nevent: {event}\ndata: {data}\n\n"


class Channel:
    """同步回调 ↔ 异步生成器。

    图在**线程**里跑（LangGraph 与工具都是同步的），事件却要在事件循环里 yield。
    直接跨线程操作 asyncio.Queue 是不安全的，所以用 `call_soon_threadsafe`。
    """

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[tuple[int, str, str] | None] = asyncio.Queue()

    # 线程侧调用
    def emit(self, seq: int, event: str, data: str) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (seq, event, data))

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    # 事件循环侧消费
    async def stream(self) -> AsyncIterator[str]:
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=HEARTBEAT_S)
            except (TimeoutError, asyncio.TimeoutError):
                yield ": ping\n\n"
                continue
            if item is None:
                break
            yield frame(*item)


def parse_frames(body: str) -> list[dict[str, Any]]:
    """把 SSE 文本解析回事件列表。只给测试与调试用——

    之所以写在这里而不是测试里，是因为"解析器与被解析的格式必须同源"，
    测试自己实现一遍解析，就测不出格式回归。
    """
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        block = block.strip("\n")
        if not block or block.startswith(":"):
            continue
        seq, name, data = None, None, None
        for line in block.split("\n"):
            if line.startswith("id: "):
                seq = int(line[4:])
            elif line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if name is None:
            continue
        events.append({"seq": seq, "event": name, "data": json.loads(data or "{}")})
    return events
