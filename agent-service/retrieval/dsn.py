"""PG 连接串的唯一来源。

为什么不让各脚本各写一条 `KW_PG_DSN`：密码会同时出现在 `POSTGRES_PASSWORD` 和
DSN 里，两份一旦不一致就是 `password authentication failed`——而且报错只说"密码错"，
不说"你配了两个密码"。2026-11-04 就栽在这上面。

规则：
- 有 `KW_PG_DSN` 就用它（CI / 远端库场景）；
- 否则用 `POSTGRES_HOST/PORT/DB/USER/PASSWORD` 拼，密码做 URL 转义；
- 都缺就返回 None（调用方回落内存检索）。
"""

from __future__ import annotations

import os
from urllib.parse import quote


def pg_dsn() -> str | None:
    dsn = (os.environ.get("KW_PG_DSN") or "").strip()
    if dsn:
        return dsn
    host = os.environ.get("POSTGRES_HOST")
    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    if not (host and user and password):
        return None
    port = os.environ.get("POSTGRES_PORT", "5433")
    db = os.environ.get("POSTGRES_DB", "knowops")
    return f"postgresql://{user}:{quote(password, safe='')}@{host}:{port}/{db}"


def mask(dsn: str) -> str:
    """脱敏后用于日志/终端输出。"""
    import re

    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", dsn)
