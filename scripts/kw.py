#!/usr/bin/env python
"""KnowOps 开发任务器（跨平台，无需 make）。

为什么不是直接一个 Makefile：Windows 上默认没有 make（Git Bash 也不带），
而 docs 里到处写的是 `make up`。所以实现只放在这里，Makefile 只是薄封装——
两套实现一定会漂移，一套实现 + 两个入口不会。

用法：
    python scripts/kw.py help
    python scripts/kw.py up           # 起 PG（M0 唯一容器）
    python scripts/kw.py schema       # 对已存在的库补建表（volume 已初始化过时用）
    python scripts/kw.py smoke-db     # ACL 冒烟：4/4 必须通过
    python scripts/kw.py codegen      # openapi.yaml → pydantic + Java DTO
    python scripts/kw.py prompt-lock  # 重新登记 prompt 指纹（改过 prompt 必跑）
    python scripts/kw.py verify       # 契约 / SQL / 模型 / prompt / 抽象隔离 自检
    python scripts/kw.py serve-knowledge
    python scripts/kw.py serve-fulfillment
    python scripts/kw.py test         # pytest
    python scripts/kw.py smoke-m0     # 双域假链路端到端（不需要 docker 与网络）
    python scripts/kw.py java-build   # 构建 Java 业务服务（上传 / ACL / 网关）
    python scripts/kw.py java-test    # 跑 Java 单元测试
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
CONTAINER = "knowops-pg"
OPENAPI = ROOT / "api" / "openapi.yaml"
SERVICE_DIR = ROOT / "agent-service"
PY_SCHEMAS = SERVICE_DIR / "api" / "schemas.py"
JAVA_OUT = ROOT / "java-service" / "contract"
MANIFEST = SERVICE_DIR / "prompts" / "manifest.yaml"

PG_USER = os.environ.get("PG_USER", "knowops")
PG_DB = os.environ.get("PG_DB", "knowops")


def log(msg: str) -> None:
    print(f"[kw] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"[kw] ✗ {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


# ---------------------------------------------------------------------------
# docker 相关
# ---------------------------------------------------------------------------
def require_docker() -> None:
    if shutil.which("docker"):
        return
    die(
        "找不到 docker 命令。\n"
        "  - 装 Docker Desktop，并在设置里启用 WSL2 后端；\n"
        "  - 装完重开终端（PATH 才会更新）；\n"
        "  - 若是公司机器被策略限制，先确认能否运行 wsl.exe。\n"
        "  不装 docker 也能跑：python scripts/kw.py codegen / verify 不需要数据库。",
        code=2,
    )


def compose(*args: str) -> None:
    require_docker()
    run(["docker", "compose", "-f", str(COMPOSE), *args], env=_compose_env())


def _compose_env() -> dict[str, str]:
    """把 config/.env 的 POSTGRES_* 映射成 compose 用的 PG_*。

    为什么需要这层：config/.env 里写的是 POSTGRES_USER/PASSWORD/DB/PORT（给 psycopg
    与运维脚本看），docker-compose.yml 里用的是 PG_USER/PASSWORD/DB/PORT（给容器看），
    两套名字不映射就会各用各的默认值——最典型的后果是**容器密码和别人以为的密码不一致**，
    连库时报 "password authentication failed" 却看不出原因。
    """
    env = dict(os.environ)
    mapping = {
        "PG_USER": "POSTGRES_USER",
        "PG_PASSWORD": "POSTGRES_PASSWORD",
        "PG_DB": "POSTGRES_DB",
        "PG_PORT": "POSTGRES_PORT",
    }
    for pg_key, postgres_key in mapping.items():
        if not env.get(pg_key) and env.get(postgres_key):
            env[pg_key] = env[postgres_key]
    return env


def wait_healthy(timeout_s: int = 120) -> None:
    log(f"等待 {CONTAINER} healthy（最多 {timeout_s}s）…")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            out = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", CONTAINER],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            out = "starting"
        if out == "healthy":
            log("PG 就绪")
            return
        time.sleep(2)
    die("PG 未在预期时间内 healthy；看日志：python scripts/kw.py logs")


def psql_file(path_in_container: str) -> None:
    require_docker()
    run([
        "docker", "exec", "-i", CONTAINER,
        "psql", "-U", PG_USER, "-d", PG_DB,
        "-v", "ON_ERROR_STOP=1", "-f", path_in_container,
    ])


def cmd_up() -> None:
    compose("up", "-d")
    wait_healthy()


def cmd_down() -> None:
    compose("down")


def cmd_clean() -> None:
    log("清理容器与数据卷（会删库，仅开发用）")
    compose("down", "-v")


def cmd_logs() -> None:
    compose("logs", "-f", "--tail", "80")


def cmd_psql() -> None:
    require_docker()
    run(["docker", "exec", "-it", CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB])


def cmd_schema() -> None:
    # init 脚本只在数据卷首次初始化时执行；卷已存在时必须显式补跑
    psql_file("/docker-entrypoint-initdb.d/01_schema.sql")


def cmd_seed() -> None:
    psql_file("/docker-entrypoint-initdb.d/02_seed_m0.sql")


def cmd_smoke_db() -> None:
    psql_file("/smoke/01_acl_smoke.sql")


# ---------------------------------------------------------------------------
# codegen
# ---------------------------------------------------------------------------
def cmd_codegen() -> None:
    PY_SCHEMAS.parent.mkdir(parents=True, exist_ok=True)
    run([
        sys.executable, "-m", "datamodel_code_generator",
        "--input", str(OPENAPI.relative_to(ROOT)),
        "--input-file-type", "openapi",
        "--output", str(PY_SCHEMAS.relative_to(ROOT)),
        "--output-model-type", "pydantic_v2.BaseModel",
        "--target-python-version", "3.12",
        "--use-annotated", "--use-standard-collections",
        "--disable-timestamp",
    ])
    log(f"pydantic → {PY_SCHEMAS.relative_to(ROOT)}")

    npx = shutil.which("npx")
    if not npx:
        log("⚠ 找不到 npx，跳过 Java DTO 生成（可用 docker run openapitools/openapi-generator-cli 代替）")
        return
    run([
        npx, "--yes", "@openapitools/openapi-generator-cli@latest", "generate",
        "-i", str(OPENAPI.relative_to(ROOT)),
        "-g", "spring",
        "-o", str(JAVA_OUT.relative_to(ROOT)),
        "--additional-properties",
        "interfaceOnly=true,useSpringBoot3=true,useJakartaEe=true,"
        "modelPackage=com.knowops.contract.dto,apiPackage=com.knowops.contract.api,"
        "documentationProvider=none",
    ])
    log(f"Java DTO → {JAVA_OUT.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# prompt 指纹登记
# ---------------------------------------------------------------------------
def cmd_prompt_lock() -> None:
    """把 manifest 里的 template_sha256 按文件实际内容重算并回填。

    用行级替换而不是 yaml.dump：dump 会把文件里的注释全部丢掉，
    而这份 manifest 的注释承载了"这个字段为什么存在"的说明——丢了就只剩一张表。
    """
    import hashlib
    import re

    import yaml

    entries = {e["id"]: e["path"] for e in (yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}).get("prompts", [])}
    text = MANIFEST.read_text(encoding="utf-8")
    current: str | None = None
    changed = 0
    out: list[str] = []

    for line in text.splitlines(keepends=True):
        m = re.match(r"\s*-\s+id:\s*(\S+)\s*$", line)
        if m:
            current = m.group(1)
        if re.match(r"\s*template_sha256:\s*", line) and current:
            rel = entries.get(current)
            if not rel:
                die(f"manifest 里 {current} 没有 path")
            path = SERVICE_DIR / rel
            if not path.is_file():
                die(f"prompt 文件不存在：{rel}")
            # 必须 LF 归一化：CRLF 会让同一份 prompt 在 Windows 与 CI 上算出不同 sha
            normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            new_line = re.sub(r'template_sha256:\s*".*?"', f'template_sha256: "{digest}"', line)
            if new_line != line:
                changed += 1
            out.append(new_line)
            continue
        out.append(line)

    MANIFEST.write_text("".join(out), encoding="utf-8")
    log(f"prompt-lock：{len(entries)} 条登记，{changed} 处指纹更新")


# ---------------------------------------------------------------------------
# 服务与测试
# ---------------------------------------------------------------------------
def _serve(entry: str, port: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PORT", port)
    log(f"启动 {entry}（{env['PORT']}）…")
    subprocess.run([sys.executable, "-m", f"api.{entry}"], cwd=SERVICE_DIR, env=env, check=False)


def cmd_serve_knowledge() -> None:
    _serve("main_knowledge", "8001")


def cmd_serve_fulfillment() -> None:
    _serve("main_fulfillment", "8002")


def cmd_test() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=SERVICE_DIR, env=env, check=False)


def cmd_smoke_m0() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_m0.py")], env=env, check=False)


def cmd_demo() -> None:
    """一键演示：解析语料→分块→灌入检索→多问题问答（含权限对比）。无 Docker/真模型也能跑。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_knowledge.py")], env=env, check=False)


def cmd_ingest_pg() -> None:
    """把样例语料灌进 PG（需容器已起 + KW_PG_DSN）。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    extra = sys.argv[2:]
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ingest_pg.py"), *extra], env=env, check=False
    )


def cmd_verify_pg() -> None:
    """PG 真库自检：扩展/索引/数据量/检索路/鉴权对比（不调 LLM）。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    extra = sys.argv[2:]
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_pg.py"), *extra], env=env, check=False
    )


def cmd_eval() -> None:
    """评测跑批：检索层指标 + 端到端对比。参数透传给 eval_run.py。

    例：kw.py eval --mode retrieval
        KW_LLM_MODE=online kw.py eval --mode e2e --graph graph_pipeline
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "eval_run.py"), *sys.argv[2:]], env=env, check=False)


def cmd_eval_compare() -> None:
    """把 eval/runs 里的结果汇总成对比表（会按当前判定口径重算）。"""
    subprocess.run([sys.executable, str(ROOT / "scripts" / "eval_compare.py"), *sys.argv[2:]], check=False)


def cmd_java_build() -> None:
    """构建 Java 业务服务（app 模块，含契约 DTO）。"""
    mvn = shutil.which("mvn")
    if not mvn:
        die("未找到 mvn，无法构建 Java 服务（需要 JDK17 + Maven）")
    subprocess.run([mvn, "-q", "-pl", "app", "-am", "install", "-DskipTests"],
                   cwd=ROOT / "java-service", check=False)


def cmd_java_test() -> None:
    """跑 Java 单元测试（ACL 主体解析 / 契约反序列化 / 上传）。"""
    mvn = shutil.which("mvn")
    if not mvn:
        die("未找到 mvn，无法跑 Java 测试（需要 JDK17 + Maven）")
    subprocess.run([mvn, "-q", "-pl", "app", "-am", "test"],
                   cwd=ROOT / "java-service", check=False)


# ---------------------------------------------------------------------------
# verify：静态自检（不需要数据库，可进 CI）
# ---------------------------------------------------------------------------
def verify_openapi() -> list[str]:
    import yaml

    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    problems: list[str] = []

    refs: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    refs.add(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    walk(doc)
    for ref in refs:
        if not ref.startswith("#/"):
            problems.append(f"外部引用不受支持：{ref}")
            continue
        cur = doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/")
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                problems.append(f"$ref 解析失败：{ref}")
                break

    # operationId 必须显式声明，否则生成器自动命名出 v1XxxPost 这种不可读的方法名
    for path, ops in doc["paths"].items():
        for verb, op in ops.items():
            if isinstance(op, dict) and "tags" in op and "operationId" not in op:
                problems.append(f"{verb.upper()} {path} 缺 operationId")

    # 契约红线：不允许出现文档清单字段
    for name, schema in doc["components"]["schemas"].items():
        props = schema.get("properties") or {}
        for banned in ("allowed_doc_ids", "visible_docs"):
            if banned in props:
                problems.append(f"{name} 出现了被禁字段 {banned}")

    return problems


def verify_sql() -> list[str]:
    from pglast import parser

    problems: list[str] = []
    for f in sorted((ROOT / "deploy" / "init").glob("*.sql")) + sorted(
        (ROOT / "deploy" / "smoke").glob("*.sql")
    ):
        try:
            parser.parse_sql(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{f.relative_to(ROOT)} 语法错误：{e}")
    return problems


def verify_models() -> list[str]:
    # 注意：只能把 agent-service 加进 sys.path，**不能**把 agent-service/api 加进去。
    # 后者会让 `import http` 命中我们自己的 api/http.py，把标准库 http 顶掉，
    # 报错长得像第三方库坏了（"No module named http.client; 'http' is not a package"）。
    sys.path.insert(0, str(SERVICE_DIR))
    from pydantic import ValidationError  # noqa: PLC0415

    from api import schemas  # noqa: PLC0415

    problems: list[str] = []
    try:
        schemas.SubjectContext(
            user_id="u_1", account_status="active", dept_paths=[], roles=[],
            subject_epoch=1, allowed_doc_ids=["doc_1"],
        )
        problems.append("SubjectContext 接受了 allowed_doc_ids —— 机器守卫失效")
    except ValidationError:
        pass
    try:
        schemas.SubjectContext(user_id="u_1", dept_paths=[], roles=[], subject_epoch=1)
        problems.append("SubjectContext 未强制 account_status")
    except ValidationError:
        pass
    return problems


def verify_prompts() -> list[str]:
    """prompt 登记与渲染自检（12 §6：渲染单测是 prompt 类 PR 的必过关卡）。"""
    sys.path.insert(0, str(SERVICE_DIR))
    try:
        from runtime.prompts import PromptError, PromptLibrary  # noqa: PLC0415
    except ImportError as e:
        return [f"无法导入 runtime.prompts：{e}"]

    try:
        lib = PromptLibrary.load(MANIFEST, root=SERVICE_DIR)
    except PromptError as e:
        return [str(e)]

    problems: list[str] = []
    for spec in lib.prompts.values():
        try:
            # 用占位值渲染一遍：必须不留 {{ }}，且变量契约成立
            lib.render(spec.id, **{v: f"<{v}>" for v in spec.variables})
        except Exception as e:  # noqa: BLE001
            problems.append(f"{spec.id} 渲染失败：{e}")
    return problems


#: 抽象隔离：runtime 源码里不允许出现具体域名标识符。
#: 这不是洁癖——一旦 runtime 里出现 `if pack.name == "xxx"`，第三个域就接不进来了，
#: 而那种代码在评审时看起来完全合理（"只是个小判断"）。
BANNED_DOMAIN_WORDS = ("knowledge", "fulfillment")


def verify_isolation() -> list[str]:
    problems: list[str] = []
    runtime_dir = SERVICE_DIR / "runtime"
    if not runtime_dir.is_dir():
        return [f"缺少 {runtime_dir}"]

    for path in sorted(runtime_dir.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            for word in BANNED_DOMAIN_WORDS:
                if word in low:
                    problems.append(f"runtime/{path.name}:{lineno} 出现域名标识符 {word!r}（抽象泄漏）")
    return problems


def cmd_verify() -> None:
    checks = [
        ("openapi.yaml", verify_openapi),
        ("SQL 语法", verify_sql),
        ("pydantic 守卫", verify_models),
        ("prompt 指纹", verify_prompts),
        ("抽象隔离", verify_isolation),
    ]
    failed = 0
    for name, fn in checks:
        try:
            problems = fn()
        except ImportError as e:
            log(f"⚠ {name} 跳过（缺依赖）：{e}")
            continue
        if problems:
            failed += 1
            print(f"[kw] ✗ {name}")
            for p in problems:
                print(f"      - {p}")
        else:
            print(f"[kw] ✓ {name}")
    if failed:
        die(f"{failed} 项自检未通过")
    log("全部自检通过")


# ---------------------------------------------------------------------------
COMMANDS = {
    "up": (cmd_up, "起 PG 容器并等待 healthy"),
    "down": (cmd_down, "停容器（保留数据）"),
    "clean": (cmd_clean, "停容器并删数据卷（会删库）"),
    "logs": (cmd_logs, "跟随 PG 日志"),
    "psql": (cmd_psql, "进交互式 psql"),
    "schema": (cmd_schema, "对已存在的库补建表"),
    "seed": (cmd_seed, "灌 M0 最小种子数据"),
    "smoke-db": (cmd_smoke_db, "ACL 冒烟（4 项断言）"),
    "codegen": (cmd_codegen, "契约 → pydantic + Java DTO"),
    "prompt-lock": (cmd_prompt_lock, "重算并回填 prompt 指纹"),
    "verify": (cmd_verify, "契约 / SQL / 模型 / prompt / 抽象隔离自检，不需要数据库"),
    "serve-knowledge": (cmd_serve_knowledge, "起知识域（:8001）"),
    "serve-fulfillment": (cmd_serve_fulfillment, "起排障域（:8002）"),
    "test": (cmd_test, "跑 pytest"),
    "smoke-m0": (cmd_smoke_m0, "双域假链路端到端验收"),
    "demo": (cmd_demo, "一键演示知识问答（解析→分块→检索→问答，含权限对比，无 Docker 可跑）"),
    "ingest-pg": (cmd_ingest_pg, "把语料灌进 PG（解析→分块→写 write/read 表）"),
    "verify-pg": (cmd_verify_pg, "PG 真库自检（扩展/索引/检索路/鉴权对比，--acl 加权限对比）"),
    "eval": (cmd_eval, "评测跑批（--mode retrieval/e2e/both，--graph graph|graph_pipeline）"),
    "eval-compare": (cmd_eval_compare, "汇总 eval/runs 成对比表（按当前判定口径重算）"),
    "java-build": (cmd_java_build, "构建 Java 业务服务（上传/ACL/网关）"),
    "java-test": (cmd_java_test, "跑 Java 单元测试"),
}


def main(argv: list[str]) -> int:
    global PG_USER, PG_DB
    # 先读 config/.env：所有子命令（up/psql/ingest-pg/verify-pg）都依赖里面的密码与 DSN
    sys.path.insert(0, str(SERVICE_DIR))
    try:
        from runtime.config import load_dotenv

        load_dotenv()
    except Exception:
        pass  # 缺依赖时仍允许 codegen/verify 这类不碰数据库的任务
    PG_USER = os.environ.get("PG_USER") or os.environ.get("POSTGRES_USER", "knowops")
    PG_DB = os.environ.get("PG_DB") or os.environ.get("POSTGRES_DB", "knowops")

    if len(argv) < 2 or argv[1] in ("help", "-h", "--help"):
        print(__doc__)
        print("可用任务：")
        for name, (_, desc) in COMMANDS.items():
            print(f"  {name:<10} {desc}")
        return 0
    task = argv[1]
    if task not in COMMANDS:
        die(f"未知任务 {task}；跑 python scripts/kw.py help 看全部", code=2)
    COMMANDS[task][0]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
