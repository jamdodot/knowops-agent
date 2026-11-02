"""知识域进程入口。同一镜像的 A 入口（docs/01 §3.3）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import uvicorn  # noqa: E402

from api.app_knowledge import create_app  # noqa: E402
from runtime import tracing  # noqa: E402
from runtime.config import Settings  # noqa: E402

settings = Settings.from_env()
tracing.setup(settings.trace_export)
app = create_app(settings)


def main() -> None:
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8001")))


if __name__ == "__main__":
    main()
