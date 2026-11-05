"""相关性下限的测试。

这个开关挡的是一个具体故障：库里没有答案时，稀疏路仍会返回几条词面命中的文档，
模型于是用常识补出答案（`u-105` 就是这么答出受限文档原话的）。

但它**默认关闭**——A/B 实测代价大于收益（见 `tools.MIN_RELEVANCE_DEFAULT` 注释）。
所以这里测的不是"阈值好用"，而是四件事，缺一件就会在线上以另一种形式回来：
1. 打开时，低分证据该丢；
2. **没跑 rerank 时一条都不能丢**（RRF 分数量纲不同，硬套阈值等于关掉检索）；
3. 默认是关的（不能因为改了常量就把行为悄悄改回去）；
4. 环境变量能开能关能填错。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from domains.knowledge.tools import MIN_RELEVANCE_DEFAULT, _drop_irrelevant, _min_relevance


def _ev(score: float):
    return SimpleNamespace(score=score, doc_id=f"d{score}", to_row=lambda: {})


def _retriever(rerank: str = "ok:BAAI/bge-reranker-v2-m3"):
    return SimpleNamespace(last_backend_notes={"rerank": rerank})


def test_default_is_off() -> None:
    """默认必须关。

    0.2 那次上线的结果是：正确率 -1pt、拒答准确率 -12.5pt、泄漏率没变。
    常量如果被谁顺手改回去，这条会先炸。
    """
    assert MIN_RELEVANCE_DEFAULT == 0.0


def test_floor_off_by_default_keeps_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KW_MIN_RELEVANCE", raising=False)
    evs = [_ev(0.9), _ev(0.05), _ev(0.004)]
    kept, dropped = _drop_irrelevant(evs, _retriever())
    assert len(kept) == 3
    assert dropped == 0


def test_low_score_evidence_is_dropped_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_MIN_RELEVANCE", "0.2")
    evs = [_ev(0.93), _ev(0.71), _ev(0.05), _ev(0.004)]
    kept, dropped = _drop_irrelevant(evs, _retriever())
    assert [e.score for e in kept] == [0.93, 0.71]
    assert dropped == 2


def test_all_low_scores_means_no_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """全丢光要返回空列表而不是"留一条垫底"。

    留一条的结果是：模型仍拿到一条不相干的证据，仍会作答，只是答案更离谱。
    宁可让上游走拒答。
    """
    monkeypatch.setenv("KW_MIN_RELEVANCE", "0.2")
    kept, dropped = _drop_irrelevant([_ev(0.04), _ev(0.01)], _retriever())
    assert kept == []
    assert dropped == 2


def test_floor_is_ignored_without_rerank(monkeypatch: pytest.MonkeyPatch) -> None:
    """没重排时 score 是 RRF 融合值（0.0x 一档），套 0.2 会把证据全清空。

    这不是"更保守"，是把检索关掉了。关掉 rerank 的部署与 A/B 都会踩到。
    """
    monkeypatch.setenv("KW_MIN_RELEVANCE", "0.2")
    evs = [_ev(0.032), _ev(0.019)]
    kept, dropped = _drop_irrelevant(evs, _retriever(rerank="skipped:disabled_by_caller"))
    assert len(kept) == 2
    assert dropped == 0


def test_floor_is_ignored_when_rerank_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """重排调用失败时分数会回落到融合值，同样不该套阈值。

    这是真实踩过的坑：同一道题两次测量得到 0.06 和 0.93，差别不是题变了，
    是那一次 rerank 没成功。拿这种分数去标定阈值，阈值一定是错的。
    """
    monkeypatch.setenv("KW_MIN_RELEVANCE", "0.2")
    kept, dropped = _drop_irrelevant([_ev(0.06)], _retriever(rerank="error:timeout"))
    assert len(kept) == 1
    assert dropped == 0


def test_env_overrides_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_MIN_RELEVANCE", "0.5")
    kept, _ = _drop_irrelevant([_ev(0.4), _ev(0.6)], _retriever())
    assert [e.score for e in kept] == [0.6]


def test_bad_env_value_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """填错不该让服务起不来，也不该静默变成某个"看起来合理"的值。"""
    monkeypatch.setenv("KW_MIN_RELEVANCE", "零点二")
    assert _min_relevance() == MIN_RELEVANCE_DEFAULT
