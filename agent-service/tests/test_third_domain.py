"""第三域仅靠 YAML 接入：docs/01 §3.4 自检题的执行版。

证明"新增域不改 runtime/ 一行"。分两层验证：
1. `discover_packs` 扫描 domains/ 目录，新增目录自动被发现（不需要改任何注册代码）；
2. `DomainPack` 的 YAML 解析是强类型 + extra="forbid" 的，配置错误启动期就炸。

真实的 `load_pack`（含包导入）已有 knowledge/fulfillment 两个域覆盖，
这里不重复测，因为它依赖真实目录布局而非临时目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from runtime.config import DomainPack, discover_packs

SERVICE_ROOT = Path(__file__).resolve().parent.parent


def test_discover_packs_finds_builtin_domains():
    packs = discover_packs(SERVICE_ROOT / "domains")
    assert "knowledge" in packs
    assert "fulfillment" in packs


def test_domain_pack_parses_minimal_yaml():
    import yaml

    raw = yaml.safe_load(
        """
name: audit
entry: /v1/audit
graph: audit_graph
tools: []
prompts: []
output:
  schema: Result
"""
    )
    pack = DomainPack(**raw)
    assert pack.name == "audit"
    assert pack.graph == "audit_graph"


def test_domain_pack_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        DomainPack(
            name="audit",
            entry="/v1/audit",
            graph="audit_graph",
            output={"schema": "Result"},
            bogus_field=1,
        )


def test_domain_pack_requires_output_schema():
    with pytest.raises(ValidationError):
        DomainPack(name="audit", entry="/v1/audit", graph="audit_graph")
