"""契约反序列化 + pydantic 守卫：docs/13 §2 的"机器守纪律"必须可执行。

核心断言：`extra="forbid"` 让 `allowed_doc_ids` 在机器层面加不回来。
这是整个权限设计里最关键的一条防线——权限是检索语句的一部分，不能靠人记。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import AnswerRequest, Code, FinishReason, SubjectContext


def test_subject_context_accepts_legal_fields(subject):
    assert subject.user_id == "u_2048"
    assert subject.dept_paths == ["D10", "D10.3"]
    assert subject.subject_epoch == 1743


def test_allowed_doc_ids_is_rejected():
    with pytest.raises(ValidationError):
        SubjectContext(
            user_id="u_1",
            account_status="active",
            dept_paths=[],
            roles=[],
            subject_epoch=1,
            allowed_doc_ids=["doc_881"],
        )


def test_account_status_required():
    with pytest.raises(ValidationError):
        SubjectContext(user_id="u_1", dept_paths=[], roles=[], subject_epoch=1)


def test_answer_request_roundtrip(subject):
    req = AnswerRequest(
        query="跨部门调拨要几级审批？", subject_ctx=subject, session_id="s_1", turn_seq=1
    )
    assert req.subject_ctx.dept_paths == ["D10", "D10.3"]


def test_unknown_error_code_rejected():
    with pytest.raises(ValidationError):
        from api.schemas import Error

        Error(code="SOMETHING_NEW", message="x", retryable=False)


def test_abstained_is_finish_reason_not_error():
    # abstained 是设计内行为（拒答），必须在 finish_reason 里，而不是错误码里
    assert "abstained" in [c.value for c in FinishReason]
    assert "ABSTAINED" not in [c.value for c in Code]
