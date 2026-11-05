"""引用标记的清洗：模型自造编号不能把整条链路打成 INTERNAL。

背景（2026-11-04，82 条 e2e 实测）：prompt 里用 `{eid}` 当格式占位符，模型直接把这三个字母
抄进了正文，`EventSink.delta` 的出口校验发现"引用了没发出的 eid"，抛 ContractViolation，
整个请求变成 INTERNAL。用户看到的是一次 500，而不是"这条答不上来"。

所以清洗放在 synthesize 里：无效标记从正文抹掉，剩下几条算几条；
一条有效引用都没有时由 verify 判 no_citation 拒答——语义正确，链路不炸。
"""

from __future__ import annotations

from domains.knowledge.nodes import _EID, _normalize_cite_chars, _strip_unknown_eids

KNOWN = {"e_费用报销管理办法-费用报销管理办法-c2"}


def test_template_placeholder_is_stripped():
    text = "费用发生 30 日内提交报销。{eid}"
    assert _strip_unknown_eids(text, KNOWN) == "费用发生 30 日内提交报销。"


def test_valid_eid_is_kept():
    text = "费用发生 30 日内提交报销。{e_费用报销管理办法-费用报销管理办法-c2}"
    assert _strip_unknown_eids(text, KNOWN) == text


def test_hallucinated_eid_is_stripped_not_raised():
    """编号编得像真的、但没发过 → 抹掉，不抛异常。"""
    text = "提前 30 日申请。{e_某不存在的文档-某不存在的文档-c9}"
    assert _strip_unknown_eids(text, KNOWN) == "提前 30 日申请。"


def test_mixed_keeps_only_valid():
    text = "A。{eid} B。{e_费用报销管理办法-费用报销管理办法-c2}"
    out = _strip_unknown_eids(text, KNOWN)
    assert "{eid}" not in out
    assert "费用报销管理办法-c2" in out


def test_regex_still_extracts_real_eid():
    """清洗依赖 _EID 的捕获组，别在收紧正则时把真实 eid 也排除了。"""
    assert _EID.findall("…{e_费用报销管理办法-费用报销管理办法-c2}") == [
        "e_费用报销管理办法-费用报销管理办法-c2"
    ]


def test_fullwidth_braces_are_normalized():
    """模型偶尔把引用打成全角 `｛ｅ_xxx｝`，不正则化就等于"没引用"→ 整题误判拒答。"""
    fw = "保密义务 3 年。｛ｅ_某文档_某文档-c1｝"
    assert _EID.findall(fw) == []  # 原样匹配不到
    assert _EID.findall(_normalize_cite_chars(fw)) == ["e_某文档_某文档-c1"]


def test_space_inside_braces_is_tolerated():
    """花括号内侧带空白也要认，并且输出统一重建为标准写法。

    实测 g-158 的正文是 `…人力资源部{ e_HR-离职交接管理办法_…-c5}`：
    原正则不容忍空格 → 一条引用都没识别出来 → verify 判无票作答 → 答对的题被判拒答。
    凡是"标记识别失败"的 bug，最终表现都是"答案对、结论错"，所以这类要逐个钉住。
    """
    known = {"e_HR-离职交接管理办法_HR-离职交接管理办法-c5"}
    text = "请假超过 7 天需加签分管副总{ e_HR-离职交接管理办法_HR-离职交接管理办法-c5}"
    assert _EID.findall(text) == ["e_HR-离职交接管理办法_HR-离职交接管理办法-c5"]

    cleaned = _strip_unknown_eids(text, known)
    assert "{e_HR-离职交接管理办法_HR-离职交接管理办法-c5}" in cleaned
    assert "{ e_" not in cleaned, "正文里不该留残缺写法"
