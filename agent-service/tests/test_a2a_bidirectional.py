"""双向 A2A 与回环门禁的测试。

单向时"谁会调谁"是写死的，不可能成环。一旦双向，知识域问句里带订单号、
排障域又问条款，就是 A→B→A 的天然环。所以这里重点验证的不是 happy path，
而是**三道门禁里最容易被绕过的那些**：

1. `chain_of` 对脏输入的清洗（传字符串、传 dict 都不能被当成合法链）
2. `may_delegate` 的回环与跳数上限
3. 服务端在 handler 之前就拒绝绕回来的请求（而不是跑完再发现）

端到端跑图（真 LLM + PG）不放在这里：那需要业务库和模型 key，
一进测试套件就会变成"有环境的机器绿、CI 红"。协议层的价值本来也只在协议。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from a2a.client import ANSWER_SKILL, ask_knowledge
from a2a.types import (
    MAX_HOPS,
    SOURCE_FULFILLMENT,
    SOURCE_KNOWLEDGE,
    chain_of,
    extend_scope,
    may_delegate,
)
from api.app_knowledge import create_app
from runtime.config import Settings

BASE = "http://127.0.0.1:8001"
SUBJECT = {
    "user_id": "u_2048",
    "account_status": "active",
    "dept_paths": ["D10"],
    "roles": ["fulfillment_ops"],
    "groups": [],
    "approval_doc_ids": [],
    "subject_epoch": 1,
}


@pytest.fixture(scope="module")
def knowledge_client() -> TestClient:
    # 只建 app 不跑图：本文件的断言都在协议层，起图等于给测试引入 DB/模型依赖。
    return TestClient(create_app(Settings(llm_mode="mock", trace_export="memory")))


# ------------------------------------------------------------------ 链的工具函数
def test_chain_of_sanitizes_dirty_input() -> None:
    """字符串不能被当成链。

    传 `{"chain": "knowledge"}` 时，若直接 `"knowledge" in chain` 会为真、
    `len(chain)` 却按字符算——校验看起来生效，实际行为取决于对端传了什么类型。
    """
    assert chain_of({"chain": "knowledge"}) == []
    assert chain_of({"chain": {"a": 1}}) == []
    assert chain_of(None) == []
    assert chain_of({"chain": ["knowledge", 3, None]}) == ["knowledge", "3"]


def test_extend_scope_appends_self() -> None:
    """出站时把本域追加到链上，且不改原 dict。

    改原 dict 的后果：同一个 scope 被复用两次，链里出现重复域，
    对端会误判成回环——一次正常委派被拒，而且只在第二次调用时才复现。
    """
    original = {"purpose": "policy_text"}
    out = extend_scope(original, domain=SOURCE_FULFILLMENT)
    assert out["chain"] == [SOURCE_FULFILLMENT]
    assert "chain" not in original
    assert extend_scope(out, domain=SOURCE_KNOWLEDGE)["chain"] == [SOURCE_FULFILLMENT, SOURCE_KNOWLEDGE]


def test_may_delegate_rejects_loop() -> None:
    ok, why = may_delegate({"chain": [SOURCE_KNOWLEDGE]}, domain=SOURCE_KNOWLEDGE)
    assert not ok
    assert why.startswith("a2a_loop")


def test_may_delegate_rejects_too_many_hops() -> None:
    chain = [f"d{i}" for i in range(MAX_HOPS)]
    ok, why = may_delegate({"chain": chain}, domain="another")
    assert not ok
    assert why.startswith("a2a_hops_exceeded")


def test_may_delegate_allows_first_hop() -> None:
    ok, why = may_delegate({}, domain=SOURCE_FULFILLMENT)
    assert ok and why == ""


# ------------------------------------------------------------------ 知识域作为被调方
def test_knowledge_agent_card_is_discoverable(knowledge_client: TestClient) -> None:
    card = knowledge_client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "knowledge-agent"
    assert card["protocolVersion"] == "1.0"
    # skill id 必须是调用方写死的那个常量，改名等于单方面毁约
    assert ANSWER_SKILL in {s["id"] for s in card["skills"]}


def test_knowledge_rejects_loop_before_running_graph(knowledge_client: TestClient) -> None:
    """链里已有 knowledge 就拒，且**不执行 handler**。

    这是防环的主门禁。如果放到 handler 内部去判，图已经跑了一半——
    一次回环要烧掉几步 LLM 才被拦下，而拦下时已经污染了 trace 和预算。
    """
    body = {
        "jsonrpc": "2.0",
        "id": "req_loop",
        "method": "SendMessage",
        "params": {
            "message": {"role": "user", "parts": [{"text": "赔付条款怎么规定的？"}]},
            # 真实链路里排障域转发时会把两级都带上
            "metadata": {"subject": SUBJECT, "scope": {"chain": [SOURCE_KNOWLEDGE, SOURCE_FULFILLMENT]}},
        },
    }
    payload = knowledge_client.post("/a2a", json=body).json()
    assert payload["error"]["code"] == -32602
    assert "回环" in payload["error"]["message"]


def test_knowledge_requires_subject(knowledge_client: TestClient) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": "req_nosub",
        "method": "SendMessage",
        "params": {"message": {"role": "user", "parts": [{"text": "条款？"}]}, "metadata": {}},
    }
    payload = knowledge_client.post("/a2a", json=body).json()
    assert payload["error"]["code"] == -32602


# ------------------------------------------------------------------ 排障域 → 知识域 客户端
def _knowledge_peer(
    answer: str = "超时 48h 且非不可抗力才赔付。",
    finish_reason: str = "answered",
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/agent-card.json":
            return httpx.Response(200, json={
                "name": "knowledge-agent",
                "protocolVersion": "1.0",
                "skills": [{"id": ANSWER_SKILL, "name": "制度条款问答"}],
            })
        # 出站请求必须带链，否则对端无法判断这次调用是不是绕回来的
        sent = request.read().decode("utf-8")
        assert '"chain"' in sent and SOURCE_FULFILLMENT in sent
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "result": {
            "id": "a2a_1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [
                {"data": {"answer": answer, "citations": [{"eid": "e_1"}],
                          "finish_reason": finish_reason}},
                {"text": answer},
            ],
        }})

    return httpx.MockTransport(handler)


def test_ask_knowledge_happy_path() -> None:
    reply = ask_knowledge("订单 O1 超时了，赔付条款怎么规定？", subject=SUBJECT,
                          base_url=BASE, transport=_knowledge_peer())
    assert reply.ok
    assert "48h" in reply.data["answer"]
    assert reply.data["citations"]


def test_ask_knowledge_treats_abstain_as_failure() -> None:
    """弃答必须判失败。

    知识域证据不足时走 abstain：state 仍是 COMPLETED、artifacts 里也有话，
    但那句话是"文档中没有找到依据"。判据落在 `answer` 为空上——
    否则排障域会把一次弃答当成"查到条款了"，然后拿空依据去下结论。
    """
    reply = ask_knowledge("订单 O1 超时了，赔付条款怎么规定？", subject=SUBJECT,
                          base_url=BASE, transport=_knowledge_peer(answer=""))
    assert not reply.ok
    assert reply.degraded == "a2a_failed"


def test_ask_knowledge_treats_abstain_text_as_failure() -> None:
    """弃答时 `answer` 里**有字**，不能按非空判成功。

    这条用例复刻的是真实返回形状：知识域的 abstain 节点会把"现有资料未覆盖…"
    写进 `answer`（前端要显示这句话），所以 `required_key="answer"` 恒为真。
    判据必须落在 `finish_reason` 上，否则排障域会把弃答当成"查到条款了"。
    """
    reply = ask_knowledge(
        "订单 O1 超时了，赔付条款怎么规定？", subject=SUBJECT, base_url=BASE,
        transport=_knowledge_peer(
            answer="现有资料未覆盖这个问题，无法给出有依据的答案。",
            finish_reason="abstained",
        ),
    )
    assert not reply.ok
    assert reply.degraded == "a2a_abstained"
    assert reply.data["finish_reason"] == "abstained"


def test_ask_knowledge_degrades_when_peer_lacks_skill() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/agent-card.json":
            # 对端把 skill 改名了：必须拒绝，而不是发过去等一个空结果
            return httpx.Response(200, json={"name": "knowledge-agent",
                                             "skills": [{"id": "chat"}]})
        return httpx.Response(500)

    reply = ask_knowledge("条款？", subject=SUBJECT, base_url=BASE,
                          transport=httpx.MockTransport(handler))
    assert not reply.ok
    assert reply.degraded == "a2a_capability_mismatch"


# ------------------------------------------------------------------ 工具层的前置判定
def test_tool_ask_knowledge_skips_loop_without_http(monkeypatch, tmp_path) -> None:
    """链里已经有本域（或已达两跳）时，工具直接返回，不发请求。

    这里用**不可达的 base_url**：如果实现漏了前置判定，工具会真的去连，
    测试会因为连接失败而露出 `a2a_unreachable` 而不是 `a2a_loop`——
    比断言"没有发起请求"更直接。

    注意正常单跳是**允许**的：知识域（chain=[]）→ 排障域（收到 chain=[knowledge]）
    再查条款，链长 1 < 2，应当发出；由知识域入口按"链里有 knowledge"拒绝。
    两道判定分工不同：本域这道管"我还能不能再发"，对端那道管"这是不是绕回来了"。
    """
    monkeypatch.setenv("KW_A2A_KNOWLEDGE_URL", "http://127.0.0.1:1")

    from pathlib import Path

    from runtime import build_runtime, new_run_context
    from runtime.events import EventSink

    domain_dir = Path(__file__).resolve().parent.parent / "domains" / "fulfillment"
    rt = build_runtime(domain_dir, Settings(llm_mode="mock", trace_export="memory"))
    sink = EventSink(lambda *_: None, trace_id="t-test", contract_version="v1", config_version="c")
    ctx = new_run_context(domain="fulfillment", sink=sink, ledger=rt.new_ledger(deadline_ms=5_000))
    ctx.scratch["delegation_chain"] = [SOURCE_FULFILLMENT, SOURCE_KNOWLEDGE]

    with rt.use(ctx):
        result = rt.tools.call("ask_knowledge", {"question": "赔付条款？"})

    assert result.ok
    assert str(result.data["reason"]).startswith("a2a_loop")
    assert result.elapsed_ms < 1_000      # 没走网络，不该有秒级耗时
