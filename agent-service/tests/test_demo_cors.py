"""演示页跨域：排障域是独立进程，页面在 :8001，所以必须显式放行。

这些断言看起来琐碎，但每一条都对应一个真实的失败方式：
- 不放行 Origin   → 浏览器直接把 POST 拦在预检，页面表现成"请求失败"，看不出原因
- 不漏 X-Contract-Version → 预检 400，同样只在浏览器里可见
- 用 "*" 放通配符 → 这个服务能触发审批与动作，等于把本地便利带进生产
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app_fulfillment import create_app
from runtime.config import Settings

PAGE = "http://localhost:8001"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Settings(llm_mode="mock", trace_export="memory")))


def test_preflight_allows_demo_page(client: TestClient) -> None:
    resp = client.options(
        "/v1/fulfillment/tasks",
        headers={
            "Origin": PAGE,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-contract-version",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == PAGE
    # 自定义头不列出来的话，浏览器会认为预检没通过
    assert "x-contract-version" in resp.headers["access-control-allow-headers"].lower()


def test_other_origin_is_not_echoed(client: TestClient) -> None:
    """非白名单 Origin 不能回写 allow-origin：回写等于放通配符。"""
    resp = client.options(
        "/v1/fulfillment/tasks",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example"


def test_origins_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """部署时页面可能不在 8001（比如 8080 的反代），所以要能从环境变量改。"""
    monkeypatch.setenv("KW_DEMO_ORIGINS", "http://localhost:8080")
    app = create_app(Settings(llm_mode="mock", trace_export="memory"))
    resp = TestClient(app).options(
        "/v1/fulfillment/tasks",
        headers={"Origin": "http://localhost:8080", "Access-Control-Request-Method": "POST"},
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:8080"
