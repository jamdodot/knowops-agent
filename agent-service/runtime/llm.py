"""模型接入。

配置的唯一真相源是 `config/models.yaml`（08 / ADR-0003）。这里只做三件事：
按角色路由、算成本、把 mock 与真实调用藏在同一个接口后面。

为什么 M0 必须有 mock 而不是"先接真模型再说"：
1. 图逻辑与契约正确性不该依赖网络和额度；
2. `fallback.mock` 本来就要求"按 hash(prompt) 命中录制响应"，这是离线演示与 CI 的前提；
3. 真模型接进来后，「同一条链路的 token/成本」才有对照组。

一个诚实的边界：mock 的 usage 是**估算**（按字符数折算），不是真实计费。
所以 mock 模式下 `usage.cost_usd` 只能用于链路自检，绝不能进成本报表。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from api.schemas import Code, Usage

from .errors import KwError
from .prompts import Rendered


@dataclass(frozen=True)
class RoleCfg:
    provider: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 1024
    timeout_s: float = 30.0


@dataclass(frozen=True)
class ProviderCfg:
    kind: str
    base_url: str
    api_key: str = ""
    timeout_s: float = 30.0
    chat_path: str = "/chat/completions"


@dataclass
class LLMResult:
    text: str
    usage: Usage
    role: str
    model: str
    cache_hit: bool = False


class LLMProvider(Protocol):
    def chat(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> LLMResult: ...


class ModelConfig:
    """`config/models.yaml` 的只读视图。"""

    def __init__(self, roles: dict[str, RoleCfg], providers: dict[str, ProviderCfg], prices: dict[str, dict[str, float]]):
        self.roles = roles
        self.providers = providers
        self.prices = prices

    @classmethod
    def load(cls, path: Path | str) -> ModelConfig:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        providers = {
            name: ProviderCfg(
                kind=cfg.get("kind", "openai_compat"),
                base_url=str(cfg.get("base_url", "")).rstrip("/"),
                api_key=_expand(cfg.get("api_key", "")),
                timeout_s=float(cfg.get("timeout_s", 30)),
                chat_path=str((cfg.get("endpoints") or {}).get("chat", "/chat/completions")),
            )
            for name, cfg in (doc.get("providers") or {}).items()
        }
        roles = {
            name: RoleCfg(
                provider=cfg["provider"],
                model=_expand(cfg["model"]),
                temperature=float(cfg.get("temperature", 0.2)),
                max_tokens=int(cfg.get("max_tokens", 1024)),
                timeout_s=float(cfg.get("timeout_s", 30)),
            )
            for name, cfg in (doc.get("roles") or {}).items()
            if "provider" in cfg and "model" in cfg
        }
        prices = doc.get("cost_ref") or {}
        return cls(roles=roles, providers=providers, prices=prices)

    def role(self, name: str) -> RoleCfg:
        try:
            return self.roles[name]
        except KeyError as exc:
            raise KwError(Code.INTERNAL, f"models.yaml 未定义角色 {name}") from exc

    def price(self, model: str) -> tuple[float, float, float] | None:
        """(input_miss, input_hit, output) 每百万 token 美元；查不到返回 None。

        查不到就返回 None，不做"取平均价"这类猜测——猜出来的成本比没有成本更危险，
        因为它会让预算控制看起来在工作。
        """
        key = model.replace("-", "_").replace(".", "_").replace("/", "_")
        entry = self.prices.get(key)
        if not entry:
            return None
        return (
            float(entry.get("input_miss_usd_per_m", 0)),
            float(entry.get("input_hit_usd_per_m", 0)),
            float(entry.get("output_usd_per_m", 0)),
        )

    def cost_usd(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        price = self.price(model)
        if price is None:
            return None
        miss, _hit, out = price
        return round((input_tokens * miss + output_tokens * out) / 1_000_000, 8)


class MockProvider:
    """录制回放：优先按 render 指纹命中，其次按 prompt id 的默认应答。"""

    def __init__(self, models: ModelConfig, recordings: Path | str, default_role: str = "generate") -> None:
        self._models = models
        self._dir = Path(recordings)
        self._default_role = default_role
        self._by_id: dict[str, Any] = {}
        mapping = self._dir / "mock_llm.yaml"
        if mapping.is_file():
            self._by_id = (yaml.safe_load(mapping.read_text(encoding="utf-8")) or {}).get("responses", {}) or {}

    def chat(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> LLMResult:
        try:
            cfg = self._models.role(role)
        except KwError:
            cfg = RoleCfg(provider="mock", model="mock", max_tokens=max_tokens or 1024)

        text = self._lookup(prompt)
        cfg_max = max_tokens or cfg.max_tokens
        text = text[: max(cfg_max * 4, 64)]

        in_tok = sum(_estimate_tokens(m["content"]) for m in prompt.messages)
        out_tok = _estimate_tokens(text)
        return LLMResult(
            text=text,
            usage=Usage(input_tokens=in_tok, output_tokens=out_tok, cost_usd=0.0),
            role=role,
            model=f"mock:{cfg.model}",
            cache_hit=True,
        )

    def _lookup(self, prompt: Rendered) -> str:
        captured = self._dir / f"{prompt.render_sha256}.json"
        if captured.is_file():
            data = json.loads(captured.read_text(encoding="utf-8"))
            return str(data.get("text", ""))

        entry = self._by_id.get(prompt.prompt_id)
        if entry is None:
            # 兜底：返回空 JSON 对象。节点必须能容忍字段缺失——
            # 这不是"容错设计"，是因为模型本来就可能少给字段，节点不防御就是生产事故。
            return "{}"
        if isinstance(entry, str):
            return entry

        # 规则式录制：只用于本地假链路的分支验证，不参与任何评测分数
        user_text = prompt.messages[-1]["content"] if prompt.messages else ""
        for rule in entry.get("rules", []) or []:
            if str(rule.get("contains", "")) in user_text:
                return str(rule.get("text", ""))
        return str(entry.get("default", "{}"))


class OpenAICompatProvider:
    """OpenAI 兼容的 chat 调用。只有 mode != mock 时才会被实例化。"""

    def __init__(self, models: ModelConfig) -> None:
        import httpx  # 延迟导入：mock 模式下不需要网络栈

        self._models = models
        self._client = httpx.Client(timeout=None)
        self._httpx = httpx

    def chat(self, role: str, prompt: Rendered, *, max_tokens: int | None = None) -> LLMResult:
        cfg = self._models.role(role)
        try:
            provider = self._models.providers[cfg.provider]
        except KeyError as exc:
            raise KwError(Code.UPSTREAM_UNAVAILABLE, f"未配置 provider {cfg.provider}") from exc

        url = provider.base_url + provider.chat_path
        payload: dict[str, Any] = {
            "model": cfg.model,
            "messages": prompt.openai_messages(),
            "temperature": cfg.temperature,
            "max_tokens": max_tokens or cfg.max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"

        try:
            resp = self._client.post(url, json=payload, headers=headers, timeout=cfg.timeout_s)
        except self._httpx.TimeoutException as exc:
            raise KwError(Code.MODEL_TIMEOUT, "模型调用超时", internal=str(exc)) from exc
        except self._httpx.HTTPError as exc:
            raise KwError(Code.UPSTREAM_UNAVAILABLE, "模型服务不可用", internal=str(exc)) from exc

        if resp.status_code == 429:
            raise KwError(Code.RATE_LIMITED, "模型限流", retry_after_ms=1_000)
        if resp.status_code >= 500:
            raise KwError(Code.UPSTREAM_UNAVAILABLE, "模型服务异常", internal=f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise KwError(Code.INVALID_SCHEMA, "模型请求被拒绝", internal=resp.text[:300])

        body = resp.json()
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        raw_usage = body.get("usage") or {}
        input_tokens = int(raw_usage.get("prompt_tokens", 0))
        output_tokens = int(raw_usage.get("completion_tokens", 0))
        cost = self._models.cost_usd(cfg.model, input_tokens, output_tokens)
        return LLMResult(
            text=text,
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost),
            role=role,
            model=cfg.model,
            cache_hit=bool(raw_usage.get("prompt_cache_hit_tokens")),
        )

    def close(self) -> None:
        self._client.close()


def build_provider(mode: str, models: ModelConfig, recordings: Path | str) -> LLMProvider | None:
    """按 KW_LLM_MODE 选实现。online 但没 key 时**不静默降级成 mock**。

    静默降级是线上事故最常见的形态（models.yaml §4 原话）：
    功能还在跑，效果掉一半，没人知道。所以宁可起不来。
    """
    if mode == "mock":
        return MockProvider(models, recordings)
    if mode == "none":
        return None
    if mode in ("online", "hybrid", "local"):
        missing = [n for n, p in models.providers.items() if not p.api_key]
        if missing:
            raise KwError(
                Code.UPSTREAM_UNAVAILABLE,
                f"provider 缺 API key：{missing}；要么补 key，要么把 KW_LLM_MODE 设为 mock",
            )
        return OpenAICompatProvider(models)
    raise KwError(Code.INTERNAL, f"未知 KW_LLM_MODE={mode}")


def _expand(value: Any) -> str:
    """支持 `${VAR}` 与 `${VAR:default}`，与 models.yaml 的写法一致。"""
    text = str(value)
    if not text.startswith("${"):
        return text
    inner = text[2:-1]
    name, _, default = inner.partition(":")
    return os.environ.get(name, default)


def _estimate_tokens(text: str) -> int:
    """粗估：CJK 约 1 字 1 token，ASCII 约 4 字符 1 token。只用于 mock。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return max(1, cjk + math.ceil(rest / 4))
