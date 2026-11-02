"""Prompt 资产库。

治理规则见 docs/12-Prompt治理.md，这里实现其中**必须由代码守住**的部分：

1. 三个 hash 分工不能混（12 §4.1）：`template_sha256` 是源码指纹、`render_sha256` 是请求指纹、
   `prompt_set_sha` 是环境指纹。混用会让 mock 回放命中错误录制、让评测分数不可比。
2. 变量严格（12 §6 render_test）：缺变量、多变量都抛错。宽松渲染的后果是
   "少传一个变量，模型照常回答"，评测时根本发现不了。
3. prompt 正文只能来自文件，不允许代码里出现裸字符串（`config/prompts.yaml` 的 forbidden 项）。

一个实现细节：hash 一律在 **LF 归一化后**计算。Windows 上 CRLF 会让同一个 prompt
在不同机器上算出不同 sha，评测基线一换机器就对不上——这是最容易被忽略的坑。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_LEFTOVER = "{{"

REQUIRED_FIELDS = ("id", "path", "version", "role", "owner", "variables", "gate")


class PromptError(RuntimeError):
    """prompt 配置错误。启动期就该失败，不能等到请求进来才炸。"""


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: int
    role: str
    path: str
    variables: tuple[str, ...]
    gate: str
    owner: str
    system: str
    body: str
    template_sha256: str
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rendered:
    """一次渲染的结果，同时携带渲染指纹（进 span 与 mock 回放键）。"""

    prompt_id: str
    version: int
    template_sha256: str
    render_sha256: str
    messages: tuple[dict[str, str], ...]
    tool_descriptions: tuple[str, ...] = ()

    def openai_messages(self) -> list[dict[str, str]]:
        return [dict(m) for m in self.messages]


@dataclass
class PromptLibrary:
    prompts: dict[str, PromptSpec] = field(default_factory=dict)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, manifest_path: Path | str, root: Path | str | None = None) -> PromptLibrary:
        manifest_path = Path(manifest_path)
        root = Path(root) if root is not None else manifest_path.parent.parent
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        entries = manifest.get("prompts")
        if not isinstance(entries, list) or not entries:
            raise PromptError(f"{manifest_path} 里没有 prompts 列表")

        lib = cls()
        for entry in entries:
            missing = [f for f in REQUIRED_FIELDS if f not in entry]
            if missing:
                raise PromptError(f"manifest 条目缺字段 {missing}: {entry}")

            rel = entry["path"]
            path = (root / rel).resolve()
            if not path.is_file():
                raise PromptError(f"prompt 文件不存在：{rel}")
            spec = _parse_spec(entry, path)
            if spec.template_sha256 != str(entry.get("template_sha256", "")):
                raise PromptError(
                    f"{rel} 的 template_sha256 与 manifest 不一致"
                    f"（manifest={entry.get('template_sha256')!r} 实际={spec.template_sha256}）。"
                    f" 跑 `python scripts/kw.py prompt-lock` 重新登记。"
                )
            if spec.id in lib.prompts:
                raise PromptError(f"prompt id 重复：{spec.id}")
            lib.prompts[spec.id] = spec
        return lib

    # ---------------------------------------------------------------- query
    def __contains__(self, prompt_id: object) -> bool:
        return prompt_id in self.prompts

    def get(self, prompt_id: str) -> PromptSpec:
        try:
            return self.prompts[prompt_id]
        except KeyError as exc:
            raise PromptError(f"未登记的 prompt：{prompt_id}") from exc

    def prompt_set_sha(self) -> str:
        """环境指纹：按 id 排序后的 `id:template_sha` 集合的 sha256（12 §4.1）。"""
        payload = "\n".join(
            f"{p.id}:{p.template_sha256}" for p in sorted(self.prompts.values(), key=lambda x: x.id)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def gated(self, gate: str) -> list[PromptSpec]:
        return [p for p in self.prompts.values() if p.gate == gate]

    # --------------------------------------------------------------- render
    def render(
        self,
        prompt_id: str,
        tool_descriptions: list[str] | tuple[str, ...] = (),
        **variables: Any,
    ) -> Rendered:
        spec = self.get(prompt_id)

        declared = set(spec.variables)
        given = set(variables)
        missing = declared - given
        extra = given - declared
        if missing:
            raise PromptError(f"{prompt_id} 缺变量：{sorted(missing)}（严格模式，不允许空渲染）")
        if extra:
            raise PromptError(f"{prompt_id} 收到未声明变量：{sorted(extra)}（说明调用方与模板已漂移）")

        values = {k: _stringify(v) for k, v in variables.items()}
        body = _substitute(spec.body, values, prompt_id)
        system = _substitute(spec.system, values, prompt_id) if spec.system else ""

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": body})

        # render 指纹 = 真正会被发出去的东西（含工具描述，12 §2：工具描述也算 prompt）
        material = {
            "system": system,
            "messages": messages,
            "tool_descriptions": list(tool_descriptions),
        }
        render_sha = hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        return Rendered(
            prompt_id=prompt_id,
            version=spec.version,
            template_sha256=spec.template_sha256,
            render_sha256=render_sha,
            messages=tuple(messages),
            tool_descriptions=tuple(tool_descriptions),
        )


# --------------------------------------------------------------------- 内部
def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_front(raw: str, path: Path) -> tuple[dict[str, Any], str]:
    text = _normalize(raw)
    if not text.startswith("---\n"):
        raise PromptError(f"{path.name} 缺 frontmatter（必须以 --- 开头）")
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise PromptError(f"{path.name} frontmatter 未闭合")
    front = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n").rstrip() + "\n"
    return front, body


def _parse_spec(entry: dict[str, Any], path: Path) -> PromptSpec:
    raw = path.read_text(encoding="utf-8")
    front, body = _split_front(raw, path)

    for key in ("id", "version", "role"):
        if key not in front:
            raise PromptError(f"{path.name} frontmatter 缺 {key}")
        if front[key] != entry[key]:
            raise PromptError(
                f"{path.name} 的 {key}={front[key]!r} 与 manifest 的 {entry[key]!r} 不一致"
                "（两处都要改，否则登记表是假的）"
            )

    variables = tuple(entry["variables"])
    used = {m.group(1) for m in _VAR.finditer(body)}
    used |= {m.group(1) for m in _VAR.finditer(str(front.get("system", "")))}
    undeclared = used - set(variables)
    if undeclared:
        raise PromptError(f"{path.name} 用了未声明变量 {sorted(undeclared)}，manifest 里补上")
    unused = set(variables) - used
    if unused:
        # 声明了却不用，会让调用方每次多传一个"传了也没用"的变量，
        # 而契约里写着它是必填——这种空洞的必填项是契约腐化的起点
        raise PromptError(f"{path.name} 声明了未使用的变量 {sorted(unused)}，从 manifest 里删掉")

    template_sha = hashlib.sha256(_normalize(raw).encode("utf-8")).hexdigest()
    return PromptSpec(
        id=entry["id"],
        version=int(entry["version"]),
        role=entry["role"],
        path=entry["path"],
        variables=variables,
        gate=entry["gate"],
        owner=entry["owner"],
        system=str(front.get("system", "")),
        body=body,
        template_sha256=template_sha,
        tools=tuple(entry.get("tools", ())),
    )


def _substitute(template: str, values: dict[str, str], prompt_id: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return values[m.group(1)]

    out = _VAR.sub(repl, template)
    if _LEFTOVER in out:
        # 残留占位符意味着变量名拼错或声明不全，渲染单测必须拦住（12 §6）
        raise PromptError(f"{prompt_id} 渲染后残留占位符：{out[max(0, out.index(_LEFTOVER) - 40):][:80]!r}")
    return out


def _stringify(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return "\n".join(f"- {i}" for i in v)
    return json.dumps(v, ensure_ascii=False)
