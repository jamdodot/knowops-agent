"""评测判定口径的回归测试（不依赖数据库、不调模型）。

这几条钉的都是"跑出来了才发现"的坑：
- 软拒答按前一半切字符会漏判（8 字答案的标志词刚好落在切口外）
- 硬拒答与软拒答必须互斥，否则两个比例加起来超过 1
- rerank 没有 key 时必须返回 None（表示"没生效"）而不是空列表（表示"确实没东西"）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from eval_run import _is_soft_abstain, mrr_at, ndcg_at, recall_at  # noqa: E402


# ------------------------------------------------------------------ 软拒答
@pytest.mark.parametrize(
    "answer",
    [
        "现有资料未覆盖。",  # 8 字：按"前一半"切会切到"现有资料"，标志词被切掉
        "现有资料未覆盖。现有证据仅涉及工作时间与请假管理，未规定离职提前提出期限。",
        "制度中未规定试用期时长，建议咨询人力资源部门。",
        "未找到相关条款。",
    ],
)
def test_soft_abstain_recognized(answer: str) -> None:
    assert _is_soft_abstain(answer) is True


@pytest.mark.parametrize(
    "answer",
    [
        # 转折后长篇展开 = 真的在编，不能算软拒答
        "现有资料未覆盖，但根据一般企业做法，" + "通常参照劳动合同法执行，" * 20 + "所以是三个月。",
        # 正常作答，压根没提"未覆盖"
        "单笔超过 5 万元的费用报销，须报财务总监审批，并附专项说明。",
        "",
        "你好，我是制度问答助手。",
    ],
)
def test_soft_abstain_rejected(answer: str) -> None:
    assert _is_soft_abstain(answer) is False


# ------------------------------------------------------------------ 检索指标
def test_recall_at_partial_hit() -> None:
    # 多跳题两个金标只召回一个 → 0.5，不是 0 也不是 1
    assert recall_at(["a", "b"], ["x", "a", "y"], 10) == 0.5


def test_recall_at_empty_gold_is_none() -> None:
    # 无答案集没有金标，返回 None 才不会被当成 0 分拉低均值
    assert recall_at([], ["x"], 10) is None


def test_mrr_takes_first_hit_rank() -> None:
    assert mrr_at(["b"], ["x", "y", "b"], 10) == pytest.approx(1 / 3)
    assert mrr_at(["z"], ["x", "y"], 10) == 0.0
    assert mrr_at([], ["x"], 10) is None


def test_ndcg_perfect_and_zero() -> None:
    assert ndcg_at(["a"], ["a", "b"], 10) == pytest.approx(1.0)
    assert ndcg_at(["z"], ["a", "b"], 10) == 0.0


def test_ndcg_rewards_both_gold_when_multi_hop() -> None:
    """两条金标都排在前面，应该明显高于只把一条排前面。"""
    both = ndcg_at(["a", "b"], ["a", "b", "x"], 10)
    one = ndcg_at(["a", "b"], ["a", "x", "b"], 10)
    assert both > one


# ------------------------------------------------------------------ rerank 降级
def test_rerank_without_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 key 时返回 None = "rerank 没生效"，调用方必须保留原顺序。

    返回空列表是另一种语义（"rerank 生效但没候选"），两者在日志里混了
    就分不清"检索坏了"和"确实没东西"。
    """
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    from retrieval.rerank import rerank

    class Doc:
        content = "任意正文"

    out, status = rerank("查询", [Doc()])
    assert out is None
    assert status.startswith("skipped")


def test_rerank_empty_candidates_is_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    from retrieval.rerank import rerank

    out, status = rerank("查询", [])
    assert out == []
    assert status == "empty"
