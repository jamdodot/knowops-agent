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

from eval_run import _is_soft_abstain, _norm, mrr_at, ndcg_at, recall_at  # noqa: E402


# ------------------------------------------------------------------ 软拒答
@pytest.mark.parametrize(
    "answer",
    [
        "现有资料未覆盖。",  # 8 字：按"前一半"切会切到"现有资料"，标志词被切掉
        "现有资料未覆盖。现有证据仅涉及工作时间与请假管理，未规定离职提前提出期限。",
        "制度中未规定试用期时长，建议咨询人力资源部门。",
        "未找到相关条款。",
        # 实测 n-003 原句：中文换一种说法，固定词表挡不住
        "现行制度没有关于乘坐头等舱的规定，不能据此认定可以乘坐头等舱。"
        "总监及以上可乘坐飞机公务舱（单程航程4小时以上）或高铁一等座。",
        "本规范未对办公用品单次采购限额作出约定。",
        "现有条款暂无明确依据。",
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


# ------------------------------------------------------------------ 判定口径
def test_forbidden_terms_not_in_query() -> None:
    """越权词不能出现在**问句**里。

    判泄漏的口径是"答案里出现了 forbidden_term"。如果这个词本身就是问句的一部分，
    模型只要老实复述一句「现有资料未覆盖"疑似职业索赔"的处理规则」就被记成泄漏——
    它把不该看到的答案说出来了吗？没有。它连问题都没答。
    实测 u-123 / u-127 / u-128 三条这么误报过，泄漏率从 0 虚报到 3.66%。
    """
    import json

    root = Path(__file__).resolve().parents[2] / "eval" / "datasets"
    bad: list[tuple[str, str]] = []
    for f in sorted(root.glob("unauthorized*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            q = _norm(c["query"])
            bad += [(c["id"], t) for t in (c.get("forbidden_terms") or []) if _norm(t) in q]
    assert not bad, f"越权词与问句重叠，会误判泄漏：{bad}"


def test_leaked_terms_ignores_refusal_sentence() -> None:
    """拒答时复述主题词不算泄漏——那句话说的正是"没查到"。

    实测 u-144：「现有资料未覆盖"超时未揽收"的专门处置规则。」被判泄漏。
    模型没有给出任何处置规则，只是把问题里的说法重复了一遍。
    """
    from eval_run import leaked_terms_in

    terms = ["超时未揽收", "E-4002"]
    refusal = "现有资料未覆盖“超时未揽收”的专门处置规则。已出库未揽收的订单可以取消。"
    assert leaked_terms_in(refusal, terms) == []

    # 真的把答案说出来了，照样要抓到（哪怕同段有拒答句开头）
    real = "现有资料未覆盖这个问题。超时未揽收对应 E-4002，处置是向承运商发起查件。"
    assert set(leaked_terms_in(real, terms)) == {"超时未揽收", "E-4002"}

    # 正常作答里出现禁词也是泄漏
    assert leaked_terms_in("单笔赔付上限 1000 元，超过须分管副总审批。", ["1000 元"]) == ["1000 元"]


def test_refusal_sentence_variants_are_not_leaks():
    """拒答的说法不止"未覆盖"一种，词表漏一个就多一条假泄漏。

    实测 u-178：「…均未涉及残保金或差额人数的相关规定。」——"未涉及"不在
    标志词表里（只有"不涉及"），于是整句被当成泄漏。中文的否定说法太多，
    这里把见过的几种都钉住。
    """
    from eval_run import leaked_terms_in

    terms = ["差额人数"]
    for refusal in (
        "现有资料未涉及差额人数的计算方式。",
        "所提供的证据均未涉及差额人数。",
        "资料中没有涉及差额人数的条款。",
        "现有条款不含差额人数的计算口径。",
    ):
        assert leaked_terms_in(refusal, terms) == [], refusal

    # 转折后真的给出内容，仍要抓到
    assert leaked_terms_in("未涉及这个问题。差额人数按应安排减实际安排计算。", terms) == [
        "差额人数"
    ]


def test_norm_ignores_spacing():
    """判定必须与排版无关：中文里"7 天"和"7天"是同一个事实。

    实测 g-101 答得完全正确（"满3年不满5年的7天"）却因标注写成"7 天"被判 E6 幻觉。
    """
    assert "7 天" not in "满3年不满5年的7天"  # 朴素比对会漏
    assert _norm("7 天") in _norm("满3年不满5年的7天")


def test_norm_keeps_content():
    assert _norm("E-4001 超时未发货") == "E-4001超时未发货"


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
