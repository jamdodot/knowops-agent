"""runtime 零域名泄漏：docs/01 §3.4 自检题 + config.py 的硬约束。

抽象是否成立不看文档，看这个测试：runtime/ 源码里出现具体域名标识符即失败。
"""

from __future__ import annotations

from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent / "runtime"


def test_runtime_has_no_domain_identifiers():
    leaks = []
    for py in RUNTIME.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            for word in ("knowledge", "fulfillment"):
                # 允许注释/docstring 里的英文通用词 "fulfillment" 极少见，但
                # "knowledge" 可能出现在通用英文句子里；这里只拦标识符形态
                if word in line and (
                    f'"{word}' in line
                    or f"'{word}'" in line
                    or f"domains.{word}" in line
                    or f"name == {word!r}" in line
                    or word in line and ("_" + word in line or word + "_" in line)
                ):
                    leaks.append(f"{py.name}:{line_no}: {line.strip()}")
    assert not leaks, f"runtime/ 出现域名标识符：\n" + "\n".join(leaks)


def test_config_py_has_no_domain_names():
    # 更严格：config.py 的职责是"读 YAML 成强类型对象"，它连域名字符串都不该有
    text = (RUNTIME / "config.py").read_text(encoding="utf-8")
    for word in ("knowledge", "fulfillment"):
        assert word not in text, f"config.py 不应出现域名 {word!r}"
