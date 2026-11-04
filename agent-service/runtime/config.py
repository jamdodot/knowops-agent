"""Domain Pack 定义与加载。

`docs/01` §3.4 的自检题是："新增第三个域需要改什么？答案是写一个 domain.yaml +
若干工具 + 一个图定义，runtime/ 不动。"

这个文件是那道自检题的**执行者**：它把 YAML 读成强类型对象，再从 pack 目录里导入图定义。
所以本包源码里不允许出现任何一个具体的域名标识符——出现了就说明在代码里分支了，
`tests/test_domain_isolation.py` 会当场失败。
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SERVICE_ROOT = Path(__file__).resolve().parent.parent


class PackError(RuntimeError):
    """pack 配置错误。启动期失败，绝不带病运行。"""


class MemoryCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    working: bool = False
    semantic: bool = False
    episodic: bool = False


class StopCfg(BaseModel):
    """停止条件。

    两域的字段集不同（知识域是证据阈值，排障域是置信度与假设数上限），
    所以这里全部可选，缺省由域自己的图决定；公共的预算项由 runtime 强制。
    """

    model_config = ConfigDict(extra="forbid")
    max_steps: int = 6
    budget_tokens: int = 12_000
    evidence_threshold: float | None = None
    confidence_threshold: float | None = None
    max_hypotheses: int | None = None
    #: 超预算后落成什么终止语义：拒答 还是 转人工（docs/13 §6 口径 1）
    on_budget: Literal["abstain", "escalate"] = "abstain"


class GuardCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    injection_check: bool = False
    action_whitelist: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)


class OutputCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: str = Field(alias="schema")

    # 允许 pydantic 用 schema_ 承接 YAML 的 schema 关键字
    def schema_name(self) -> str:
        return self.schema_


class EvalCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    datasets: list[str] = Field(default_factory=list)


class DomainPack(BaseModel):
    """一个域的静态声明。字段与 docs/01 §3.2 的样例一一对应。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    version: int = 1
    entry: str
    graph: str
    tools: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    stop: StopCfg = Field(default_factory=StopCfg)
    guard: GuardCfg = Field(default_factory=GuardCfg)
    output: OutputCfg
    eval: EvalCfg = Field(default_factory=EvalCfg)

    #: pack 目录（不进模型序列化，避免把本机路径写进任何快照）
    root: Path = Field(default=Path("."), exclude=True)

    def prompt_ids(self) -> list[str]:
        return list(self.prompts)


@dataclass(frozen=True)
class LoadedPack:
    pack: DomainPack
    graph_module: ModuleType
    tools_module: ModuleType | None


def load_pack(pack_dir: Path | str) -> LoadedPack:
    """读 pack 目录：domain.yaml + graph.py + 可选 tools.py。

    pack 按**包**加载（`domains.<name>.graph`），不是按文件路径动态执行。原因很具体：
    两个域都有 `nodes.py`，按文件路径加载会出现 `import nodes` 命中上一个域的模块，
    于是知识域跑出了排障域的节点——而且不报错，只是行为莫名其妙。
    """
    root = Path(pack_dir).resolve()
    cfg_path = root / "domain.yaml"
    if not cfg_path.is_file():
        raise PackError(f"找不到 {cfg_path}")

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        pack = DomainPack(**raw, root=root)
    except ValidationError as exc:
        raise PackError(f"{cfg_path} 不合规：{exc}") from exc

    if str(SERVICE_ROOT) not in sys.path:
        sys.path.insert(0, str(SERVICE_ROOT))

    try:
        graph_module = importlib.import_module(f"domains.{pack.name}.graph")
    except ImportError as exc:
        raise PackError(f"无法导入 domains.{pack.name}.graph：{exc}") from exc

    builder = getattr(graph_module, "build", None)
    if not callable(builder):
        raise PackError(f"{pack.name}/graph.py 必须暴露 build(rt)")

    declared = getattr(graph_module, "NAME", None)
    if declared != pack.graph:
        # 名字对不上说明 YAML 与代码已漂移；这种错误拖到运行期就是"跑的不是你以为的图"
        raise PackError(f"{pack.name}: domain.yaml 的 graph={pack.graph!r} 与 graph.py 的 NAME={declared!r} 不一致")

    tools_module = None
    if (root / "tools.py").is_file():
        try:
            tools_module = importlib.import_module(f"domains.{pack.name}.tools")
        except ImportError as exc:
            raise PackError(f"无法导入 domains.{pack.name}.tools：{exc}") from exc
    return LoadedPack(pack=pack, graph_module=graph_module, tools_module=tools_module)


def discover_packs(domains_root: Path | str) -> dict[str, Path]:
    """扫描 domains/ 下所有含 domain.yaml 的目录。新增域只需加目录，不改这里。"""
    root = Path(domains_root)
    return {
        d.name: d
        for d in sorted(root.iterdir())
        if d.is_dir() and (d / "domain.yaml").is_file()
    }


def load_dotenv(path: Path | None = None) -> list[str]:
    """把 config/.env 读进 os.environ（已存在的变量不覆盖）。

    为什么要有这个：文档里到处写"填 config/.env"，但代码若不自读，用户就必须
    在每次开终端的先 `set -a; source config/.env`——这是纯粹的摩擦，且忘一次
    就会得到"配了 key 却还在 mock"的假故障。放在这里而不是各脚本里，是因为
    API 进程、demo、ingest 三条入口都要用。

    只支持 KEY=VALUE 与 # 注释这种最朴素的语法：.env 里不该放复杂的 shell 逻辑。
    """
    env_path = path or (Path(__file__).resolve().parents[2] / "config" / ".env")
    if not env_path.is_file():
        return []
    loaded: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


@dataclass(frozen=True)
class Settings:
    """进程级设置。全部来自环境变量，便于 compose / k8s 注入。"""

    contract_version: str = "1.0.0"
    llm_mode: str = "mock"
    trace_export: str = "memory"
    default_deadline_ms: int = 15_000
    debug_endpoints: bool = True
    pg_dsn: str | None = None
    service_version: str = "0.1.0-m0"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        return cls(
            contract_version=os.environ.get("KW_CONTRACT_VERSION", "1.0.0"),
            llm_mode=os.environ.get("KW_LLM_MODE", "mock").lower(),
            trace_export=os.environ.get("KW_TRACE_EXPORT", "memory").lower(),
            default_deadline_ms=int(os.environ.get("KW_DEFAULT_DEADLINE_MS", "15000")),
            debug_endpoints=os.environ.get("KW_DEBUG_ENDPOINTS", "1") not in ("0", "false", "False"),
            pg_dsn=os.environ.get("KW_PG_DSN") or None,
            service_version=os.environ.get("KW_SERVICE_VERSION", "0.1.0-m0"),
        )
