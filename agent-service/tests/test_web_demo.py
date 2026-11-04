"""演示页与评测只读接口的冒烟测试。

这些断言不验证"页面好不好看"，只钉住三件容易悄悄坏掉的事：
1. 静态页挂在 `/` 上，且没被前面的路由抢走；
2. `/v1/eval/runs` 的归一化结构（检索层一次跑批出两个子键，不能按文件名找）；
3. tag 校验挡住路径穿越。

创建 app 会 build 一次 runtime（含 prompt 指纹），所以整个模块共用一个 client。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app_knowledge import EVAL_RUNS, WEB_ROOT, create_app
from runtime.config import Settings

_CACHE: dict[str, TestClient] = {}


@pytest.fixture(scope="module")
def client() -> TestClient:
    if "c" not in _CACHE:
        _CACHE["c"] = TestClient(create_app(Settings()))
    return _CACHE["c"]


# ------------------------------------------------------------------ 静态页
def test_index_served_at_root(client: TestClient) -> None:
    if not WEB_ROOT.is_dir():
        pytest.skip("web/ 目录不存在")
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # 三个视图的容器必须在，否则 tab 切换会静默失效
    for anchor in ("view-chat", "view-compare", "view-eval"):
        assert anchor in r.text


def test_static_assets(client: TestClient) -> None:
    if not WEB_ROOT.is_dir():
        pytest.skip("web/ 目录不存在")
    for path in ("/styles.css", "/app.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert len(r.text) > 500, f"{path} 内容过短，可能没挂上"


# ------------------------------------------------------------------ 评测接口
def test_eval_runs_list_shape(client: TestClient) -> None:
    r = client.get("/v1/eval/runs")
    assert r.status_code == 200
    runs = r.json()["runs"]
    if not runs:
        pytest.skip("eval/runs 下没有产物")

    for item in runs:
        # key/kind 是前端分流依据；缺了页面会退化成"全部显示成端到端"
        assert item["key"], item
        assert item["kind"] in ("e2e", "retrieval"), item
        assert isinstance(item["summary"], dict)
        # 列表刻意不带 cases：一份 run 30KB，全带到列表页没意义
        assert "cases" not in item


def test_eval_runs_kind_split(client: TestClient) -> None:
    """检索层必须是它自己的 kind，不能被当成端到端。

    这是回归点：检索层 run 的顶层 tag 是 `ret-baseline`（字符串），
    真正的 payload 在 `ret-baseline__rerank0/1` 两个子键里。
    早先按 `doc[tag]` 取会拿到字符串，summary 静默变空。
    """
    runs = client.get("/v1/eval/runs").json()["runs"]
    if not runs:
        pytest.skip("eval/runs 下没有产物")

    e2e = [r for r in runs if r["kind"] == "e2e"]
    ret = [r for r in runs if r["kind"] == "retrieval"]
    for item in e2e:
        assert item["summary"].get("golden"), f"{item['key']} 标成 e2e 却没有 golden 段"
    for item in ret:
        assert item["summary"].get("overall"), f"{item['key']} 标成 retrieval 却没有 overall 段"


def test_eval_run_detail_carries_cases(client: TestClient) -> None:
    runs = client.get("/v1/eval/runs").json()["runs"]
    if not runs:
        pytest.skip("eval/runs 下没有产物")

    key = runs[0]["key"]
    r = client.get(f"/v1/eval/runs/{key}")
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == key
    assert isinstance(body["cases"], list) and body["cases"]


def test_eval_run_missing_and_traversal(client: TestClient) -> None:
    assert client.get("/v1/eval/runs/__not_exist__").status_code == 404
    # 编码后的 ../ 会在路由层就不匹配；验的是"不会读到 eval/runs 外的文件"
    for bad in ("..%2F..%2Fconfig", "..", "a%2Fb"):
        assert client.get(f"/v1/eval/runs/{bad}").status_code in (400, 404)


def test_eval_root_points_at_repo_eval_dir() -> None:
    """页面的评测数字必须来自项目内的 eval/runs，不是别处的缓存目录。"""
    assert EVAL_RUNS.name == "runs"
    assert EVAL_RUNS.parent.name == "eval"


# ------------------------------------------------------------------ 契约未被页面破坏
def test_answer_route_still_precedes_static_mount(client: TestClient) -> None:
    """`/` 上挂了 StaticFiles，但 API 路由必须先匹配——否则问答应变成 404/405。"""
    r = client.post(
        "/v1/knowledge/answers",
        json={
            "query": "测试",
            "subject_ctx": {
                "user_id": "u_1",
                "account_status": "active",
                "dept_paths": ["D10"],
                "roles": ["employee"],
                "groups": [],
                "approval_doc_ids": [],
                "subject_epoch": 1,
            },
            "session_id": "s_web_smoke",
            "turn_seq": 1,
        },
        headers={"X-Contract-Version": "1.0.0"},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
