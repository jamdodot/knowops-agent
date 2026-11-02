"""测试夹具：把 SERVICE_ROOT 放进 sys.path，并给出一套可复用的 mock 运行时。

所有测试都走 mock 模型，不发起真实网络请求——这保证测试在任何机器上可复现。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parent.parent

if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


@pytest.fixture
def settings():
    from runtime.config import Settings

    return Settings(llm_mode="mock", trace_export="memory")


@pytest.fixture
def subject():
    from api.schemas import SubjectContext

    return SubjectContext(
        user_id="u_2048",
        account_status="active",
        dept_paths=["D10", "D10.3"],
        roles=["employee", "fulfillment_ops"],
        groups=["sg_17"],
        approval_doc_ids=["doc_902"],
        subject_epoch=1743,
    )
