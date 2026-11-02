"""预算硬停 + 记忆命名空间隔离 + acl_fingerprint。

这三条是 runtime 内核的公共策略，不能靠各域自觉。
"""

from __future__ import annotations

import pytest

from api.schemas import Code, DegradedEnum, SubjectContext, Usage
from runtime.errors import BudgetStop, KwError
from runtime.events import Ledger
from runtime.config import StopCfg
from runtime import acl_fingerprint, namespace_for


def _stop(**kw):
    cfg = dict(max_steps=6, budget_tokens=12000)
    cfg.update(kw)
    return StopCfg(**cfg)


# ------------------------------------------------------------------ 预算
def test_budget_steps_hard_stop():
    ledger = Ledger.for_run(_stop(max_steps=2))
    ledger.enter_node()
    ledger.enter_node()
    with pytest.raises(BudgetStop):
        ledger.enter_node()


def test_budget_tokens_hard_stop():
    ledger = Ledger.for_run(_stop(max_steps=100, budget_tokens=10))
    with pytest.raises(BudgetStop):
        ledger.charge(Usage(input_tokens=6, output_tokens=6))


def test_budget_cost_hard_stop():
    ledger = Ledger.for_run(_stop(max_steps=100), max_cost_usd=0.01)
    with pytest.raises(BudgetStop):
        ledger.charge(Usage(input_tokens=1, cost_usd=0.02))


def test_budget_deadline():
    import time

    ledger = Ledger.for_run(_stop(max_steps=100), deadline_ms=1)
    time.sleep(0.01)
    with pytest.raises(BudgetStop):
        ledger.enter_node()


# ------------------------------------------------------------------ 命名空间
def test_semantic_namespace_is_user_scoped():
    assert namespace_for("u_2048") == "user:u_2048"
    assert namespace_for("u_999") != namespace_for("u_2048")


def test_memory_only_stores_success():
    from runtime.memory import InMemoryMemory

    m = InMemoryMemory()
    assert m.write("episodic", "domain:x", "失败案例", success=False) is None
    ok = m.write("episodic", "domain:x", "成功案例", success=True)
    assert ok is not None
    assert len(m.all_of("episodic", "domain:x")) == 1


def test_memory_recall_is_namespace_isolated():
    from runtime.memory import InMemoryMemory

    m = InMemoryMemory()
    m.write("semantic", "user:a", "A 的偏好是简洁回答", success=True)
    m.write("semantic", "user:b", "B 的偏好是详细回答", success=True)
    # A 召回时不能命中 B 的内容
    hits = m.recall("semantic", "user:a", "简洁", k=5)
    assert all(h.namespace == "user:a" for h in hits)


# ------------------------------------------------------------------ 指纹
def test_acl_fingerprint_changes_with_epoch():
    base = SubjectContext(
        user_id="u_1", account_status="active", dept_paths=["D10"], roles=["employee"],
        groups=[], approval_doc_ids=[], subject_epoch=1,
    )
    changed = base.model_copy(update={"subject_epoch": 2})
    assert acl_fingerprint(base) != acl_fingerprint(changed)


def test_acl_fingerprint_stable_with_field_order():
    a = SubjectContext(
        user_id="u_1", account_status="active", dept_paths=["D10", "D10.3"], roles=["a", "b"],
        groups=["g1"], approval_doc_ids=["d1"], subject_epoch=1,
    )
    b = a.model_copy(update={"dept_paths": ["D10.3", "D10"], "roles": ["b", "a"]})
    assert acl_fingerprint(a) == acl_fingerprint(b)
