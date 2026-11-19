# runtime/events.py 与 runtime/registry.py 逐行解读

> 阅读对象：`agent-service/runtime/events.py`（376 行）、`agent-service/runtime/registry.py`（251 行）
> 阅读日期：2026-11-19
> 说明：行号对应本次阅读时的文件状态。文中「⚠️」标记为代码与注释/文档不一致，或存在实际风险的位置。

---

## 目录

- [0. 两个文件的分工](#0-两个文件的分工)
- [一、runtime/events.py](#一runtimeeventspy376-行)
  - [1.1 模块 docstring（L1-12）](#11-模块-docstringl1-12为什么要有-eventtsink)
  - [1.2 导入与传输层类型（L14-47）](#12-导入与传输层类型l14-47)
  - [1.3 Ledger 预算账本（L50-119）](#13-ledger-预算账本l50-119)
  - [1.4 RunContext 运行时上下文（L122-145）](#14-runcontext-运行时上下文l122-145)
  - [1.5 EventSink 状态机与状态位（L148-184）](#15-eventsink-状态机与状态位l148-184)
  - [1.6 内部工具 `_put` / `_ensure_running`（L186-198）](#16-内部工具-_put--_ensure_runningl186-198)
  - [1.7 起始 / 降级 / delivered（L200-230）](#17-起始--降级--deliveredl200-230)
  - [1.8 内容事件 citation / delta（L233-262）](#18-内容事件-citation--deltal233-262)
  - [1.9 排障域事件（L265-286）](#19-排障域事件l265-286)
  - [1.10 input_required 暂停态（L289-310）](#110-input_required-暂停态l289-310)
  - [1.11 done / error / 终止唯一性（L312-352）](#111-done--error--终止唯一性l312-352)
  - [1.12 close() 与只读视图（L355-374）](#112-close-与只读视图l355-374)
- [二、runtime/registry.py](#二runtimeregistrypy251-行)
  - [2.1 模块 docstring（L1-11）](#21-模块-docstringl1-11六件事--两个取舍)
  - [2.2 导入与阈值（L13-33）](#22-导入与阈值l13-33)
  - [2.3 ToolSpec（L36-45）](#23-toolspecl36-45)
  - [2.4 ToolResult（L48-61）](#24-toolresultl48-61)
  - [2.5 注册（L64-96）](#25-注册l64-96)
  - [2.6 自检与元信息（L98-119）](#26-自检与元信息l98-119)
  - [2.7 call() 前半段：查表与校验（L122-138）](#27-call-前半段查表与校验l122-138)
  - [2.8 call() 三条失败路径（L139-163）](#28-call-三条失败路径l139-163)
  - [2.9 call() 成功路径（L165-189）](#29-call-成功路径l165-189)
  - [2.10 close 与两个小工具（L191-203）](#210-close-与两个小工具l191-203)
  - [2.11 _compress 的 L1 压缩（L206-241）](#211-_compress-的-l1-压缩l206-241)
  - [2.12 _short / _truncate（L244-251）](#212-_short--_truncatel244-251)
- [三、值得注意的问题清单](#三值得注意的问题清单)
- [四、两张速查表](#四两张速查表)

---

## 0. 两个文件的分工

| 文件 | 守的是什么维度 | 核心手段 | 唯一实现点 |
|---|---|---|---|
| `runtime/events.py` | 契约的**时间**维度：谁先谁后、谁只能一次 | `EventSink` 集中出口 + 状态机 + `ContractViolation` | `_put()`（所有事件唯一出口）、`_ensure_running()`、`_terminal_once()` |
| `runtime/registry.py` | 工具的**空间**维度：统一声明、校验、计时、压缩 | `ToolRegistry` 强制注册 + 线程池调度 | `call()`（所有工具唯一入口）、`_compress()` |

两者的共同前提：这些约束**只能有一处实现**。允许任何一个域/工具绕过，就会退化成"看实现者心情"。

---

## 一、runtime/events.py（376 行）

### 1.1 模块 docstring（L1-12）：为什么要有 EventSink

- **L3** 一句话定位：只干一件事——把 `docs/13 §4.3` 的顺序契约变成**运行时抛异常**的代码不变量。
- **L5-7** 三条一旦靠"自觉"就必然出事的约束：
  1. `meta` 必须第一帧
  2. `citation` 必须先于引用它的 `delta`
  3. `done` / `error` 有且仅有一个
- **L8-9** 所以所有事件必须过 `EventSink`，违反时**当场抛 `ContractViolation`**，而不是"发出去让前端猜"。
- **L11** 代价 / 收益写明：多一层对象 vs `tests/test_event_order.py` 能**证明**约束成立，而不是文档里写着成立。

> 这一段的写法值得单独记：它把"为什么不能各域自己 yield"写进了代码注释，而不是留在设计文档里。改这块的人第一眼就能看到约束来源。

### 1.2 导入与传输层类型（L14-47）

| 行 | 内容 | 说明 |
|---|---|---|
| L14 | `from __future__ import annotations` | 全部注解延迟求值：`EventSink` 在 `RunContext` 里前向引用、`float \| None` 等新语法在低版本也能写 |
| L16 | `import json` | payload 序列化 |
| L17 | `import time` | `time.monotonic()` 用于截止时间，不受系统时钟回拨影响 |
| L18 | `from collections.abc import Callable` | 回调签名 |
| L19 | `from dataclasses import dataclass, field` | `Ledger` / `RunContext` |
| L20 | `from typing import Any` | 宽松类型 |
| L22-41 | `from api.schemas import (...)` | **导入了 17 个契约对象**——说明本文件是"契约的唯一出口"：事件字段结构由 pydantic 模型定义，这里只负责校验顺序 + 序列化 |
| L43 | `from .config import StopCfg` | 预算默认值来源 |
| L44 | `from .errors import BudgetStop, ContractViolation, KwError` | 预算耗尽 / 契约违反 / 业务错误基类（能 `.to_contract(trace_id)`） |
| L47 | `EmitFn = Callable[[int, str, str], None]` | **关键解耦点**：签名是 `(seq, event_type, data_json)`，runtime 完全不知道 FastAPI / SSE 的存在。换成 WebSocket、换成写文件都不需要改本文件 |

### 1.3 Ledger 预算账本（L50-119）

**docstring（L52-56）**
- 四类预算：步数 / token / 成本 / 时间，任一超限**硬停**。
- L54-55 解释分层：`runtime` 只管"超了要停"，"超了落成什么语义"是域策略 `stop.on_budget`。

**字段（L58-65）**

| 字段 | 含义 |
|---|---|
| `max_steps` / `max_tokens` / `max_cost_usd` | 上限，成本可为 `None` = 不限制 |
| `deadline_at` | **绝对**单调时钟点（不是剩余时长） |
| `steps` / `tokens_in` / `tokens_out` / `cost_usd` | 累计值 |

**`for_run()` 工厂（L67-82）**
- L78-79 `max_steps or stop.max_steps`：调用方可覆盖配置。⚠️ 副作用：传 `0` 会被当成"没传"回落到配置值（`or` 对 0 / 0.0 的经典坑）。
- L80 `max_cost_usd=max_cost_usd`：直接取参数，**不从 `stop` 读**——成本上限没有配置默认值，只有调用方显式传才生效，默认等于**不限制成本**。
- L81 `deadline_at = monotonic() + deadline_ms/1000 if deadline_ms else None`：`X-Deadline-Ms` 换算成绝对时间点。

**`enter_node()`（L85-90）**
- L86 每次进图节点调用，`steps += 1`。
- L87-88 **先自增再判 `> max_steps`**，所以"恰好 max_steps 步"合法（允许 N 步，不是 N-1）。
- L89 返回步号。
- L89 `check_clock()` 顺手把时间预算也判一次。

**`charge(usage)`（L92-103）**
- L93-94 `usage is None` 直接返回：LLM 调用可能没有 usage（缓存命中），不能因此崩。
- L95-97 累加，`or 0` 防契约模型里该字段为 `None`。
- L98-101 token 超限抛 `BudgetStop("tokens", ...)`。**先累加再判断**——超限的这次用量仍被记账，`usage()` 报出去的是真实消耗而非截断值，否则账单对不上。
- L102-103 成本同理，且只在配置了上限时判。

**`check_clock()`（L105-107）**
- `monotonic() > deadline_at` → `BudgetStop("deadline")`。

**`tokens` 属性 / `usage()`（L109-119）**
- L110-111 `tokens` = in + out。
- L113-119 `usage()` 转成契约 `Usage`，cost 保留 6 位小数避免浮点尾巴。

### 1.4 RunContext 运行时上下文（L122-145）

**字段**

| 行 | 字段 | 说明 |
|---|---|---|
| L126-129 | `domain` / `trace_id` / `sink` / `ledger` | **四个必填**：构造它就必须同时有事件出口和账本，不可能"跑图但没预算" |
| L130 | `session_id` | 会话 |
| L131 | `turn_seq` | 多轮序号 |
| L132 | `config_version` | 配置版本（进 meta） |
| L133 | `subject` | 主体权限上下文，工具鉴权靠它 |
| L134 | `acl_fingerprint` | 权限指纹，用于缓存键 / 审计 |
| L135 | `task_id` | 暂停态恢复用 |
| L136-137 | `scratch` | 域自定义中间态（如"已产生的引用"），**不参与契约**、不进 meta，只在本进程内传 |
| L138 | `degraded` | 本次运行的降级标记列表 |

**`degrade()`（L140-145）**
- L141 注释强调"唯一入口，禁止静默降级"（纪律来自 `config/models.yaml`）。
- L142-143 去重后追加。
- L144-145 **关键分支**：只有 `sink.started` 为真（meta 已发）才通知 sink。原因很实在——meta 里已经带了 `preflight_degraded`，**发完 meta 之后的降级没地方放，只能攒到 `done` 事件里**（见 L322、L330）。这就是 `note_degraded` 存在的意义。

### 1.5 EventSink 状态机与状态位（L148-184）

**docstring 状态机（L149-154）**

```
(init) --meta--> running --done/error--> closed
                  └── input_required --> paused（流关闭但不是失败）
```

注意 `paused` 是 `closed` 的子态（L303-304 同时置真），区别只在"算不算失败"。

**构造参数（L156-166）**
`sink(emit, *, trace_id, contract_version, config_version, prompt_set_sha=None, acl_fingerprint=None, degraded=None)`——前三个必填，后三个可选，全都是要写进 meta 的元信息。

**状态位（L167-184）**

| 行 | 成员 | 作用 |
|---|---|---|
| L167 | `_emit` | 传输层回调，只在 `_put` 用 |
| L168-172 | 元信息 | 进 meta |
| L173 | `preflight_degraded` | meta 前已知，**进 meta** |
| L174 | `late_degraded` | meta 后才发生，**进 done** |
| L176 | `_seq` | 从 0 开始，L188 先自增 → 第一帧是 1 |
| L177-179 | `started` / `closed` / `paused` | 三个布尔 |
| L180 | `_terminal` | 记 `"done"` 或 `"error"`，用于"只能有一个终止事件" |
| L181 | `_eids` | 已发出的证据 id 集合，`delta` 靠它校验引用合法性 |
| L182-183 | `_citation_sent` / `_delta_started` | 服务顺序校验 + `delivered` |
| L184 | `_citations` | 攒起来的引用，`done` 时若未显式传就回填 |

> L173-174 的"两个桶"是整个"降级不丢"设计的核心：降级发生在 meta 之前还是之后，决定了它出现在哪个事件里。

### 1.6 内部工具 `_put` / `_ensure_running`（L186-198）

**`_put(event, payload)`（L187-189）**
- L188 `self._seq += 1`：**全局唯一且单调**，客户端靠它排序去重。
- L189 `json.dumps(payload, ensure_ascii=False, separators=(",", ":"))`：中文不转义（前端可读、体积小），紧凑分隔符省流量。
- 这是**唯一**真正调用传输层的地方——所有事件收敛到这一行。

**`_ensure_running(allow_paused=False)`（L191-198）**
- L192-193 已关闭 → `ContractViolation`，错误信息带 `terminal`，方便定位"是谁先发了 done 又发东西"。
- L194-195 已 paused 且不允许 → 违反 `docs/13 §2`：暂停后本流不得再发事件。
- L196-197 **懒启动**：任何内容事件之前若还没发 meta，自动补 `start()`。保证"meta 第一帧"不依赖调用顺序。
- `allow_paused` 目前全文件没有以 `True` 调用过——是给 `resume` 预留的口子。

### 1.7 起始 / 降级 / delivered（L200-230）

**`start()`（L200-215）**
- L203-204 幂等，`started` 已 True 直接返回。
- L207-214 构造 `MetaEvent`：`trace_id`、`contract_version`、`config_version`、`prompt_set_sha`、`acl_fingerprint`、`degraded=preflight_degraded`。
- `model_dump(mode="json", exclude_none=True)`：
  - `mode="json"` 让枚举 / 时间变成可序列化值
  - `exclude_none` 让可选字段**不出现**——契约里是 optional，不发就等于没值，避免前端拿到 `null` 还要额外判空

**`note_degraded()`（L217-219）**
- 与 L142-143 同款去重，写进 `late_degraded`。

**`delivered` 属性（L221-230）—— 这段注释比代码重要**

- L230 判定 = `_citation_sent and _delta_started`，即"引用和正文都发出去了"。
- L226-228 用途：收尾时判断"能不能反悔"。因为 `delta` 一旦发出**不可撤回**，此时再把 `finish_reason` 落成 `abstained`，就会出现：

  > API 说没有答案、用户屏幕上却是带引用的完整回答，下游还会据此转人工。

- L228 划清边界：它只回答"发没发出去"，**不判断答案质量**——质量是 verify 的事。

### 1.8 内容事件 citation / delta（L233-262）

**`citation(citations)`（L233-246）**

| 行 | 行为 |
|---|---|
| L234 | 先过守卫（会自动 `start()`） |
| L235-236 | 空列表直接返回。**注意：不置 `_citation_sent`**，所以"空引用 + 正文"仍算 `delivered=False` |
| L237-239 | **约束 1**：`_delta_started` 后不许补引用。理由见注释——此时前端已渲染正文，补引用必然编号错位 |
| L240-244 | 逐条登记 eid，重复即抛；同时攒进 `_citations` 供 `done` 回填 |
| L245 | 置 `_citation_sent` |
| L246 | 发出 |

**`delta(text, eids)`（L248-262）**

| 行 | 行为 |
|---|---|
| L250-251 | 空文本返回（空 delta 没有语义，还会污染 `_delta_started`） |
| L252-255 | **出口 5 的运行时兜底**：`set(eids) - self._eids` 非空 → 抛。即"模型不能引用没出现过的证据"，这是防幻觉引用的最后一道闸 |
| L256 | 置 `_delta_started`——**在 `_put` 之前**，保证即便序列化炸了也不会出现"发了正文但状态没记" |
| L257-262 | 发事件；`eids` 为空时传 `None` 而非 `[]`，配合 `exclude_none` 让字段直接消失 |

### 1.9 排障域事件（L265-286）

三个方法结构完全一致：`_ensure_running()` → 构造 pydantic 模型 → `_put` 序列化。

- **L265-270** `progress(node, step, note)`：节点进度。
- **L272-279** `evidence(hypothesis_id, verdict, summary)`：假设验证结果，`Verdict` 枚举。
- **L281-286** `action_proposal(approval)`：需要人审的动作提议，通常紧接 `input_required`。

> 设计取向：sink 提供"按事件类型命名的方法"而不是通用 `emit(type, **kw)`，好处是**字段拼错在构造时就被 pydantic 拦住**。

### 1.10 input_required 暂停态（L289-310）

- **L298-301** docstring 点明两件事：发出后本流关闭、task → `paused`；**它不是错误**（断流 ≠ 失败），调用方应 GET 或 resume 续接。
- **L302** 过守卫（必须是 running）。
- **L303-304** **先置 `paused` 和 `closed`，再发事件**。顺序讲究：此时守卫已过；若先发再置状态，中间抛异常会留下"发过暂停帧但状态没记"的半截状态。
- **L305-310** 发 `InputRequiredEvent`。
- **L309 用了 `by_alias=True`**：因为 pydantic 的 `BaseModel` 自带 `schema()` 方法，字段名 `schema` 必须用 alias（通常 `schema_` + `alias="schema"`），才能既避开方法名冲突、又保证 JSON 键是 `schema`。其他事件不需要 `by_alias`。

### 1.11 done / error / 终止唯一性（L312-352）

**`done(finish_reason, usage, *, citations=None, task_id=None)`（L312-333）**

| 行 | 行为 |
|---|---|
| L320 | 过守卫 |
| L321 | `_terminal_once("done")` 占位，后面再发 error 就炸 |
| L322 | **降级合并**：`preflight + late 中不在 preflight 的部分`。meta 已发出去的 preflight 客户端已知，但为让 `done` 自成一份完整记录，这里再全量带一遍（顺序稳定：先预检后晚期） |
| L328 | 引用回填三态：`citations is not None` 用显式传的（允许传 `[]` 覆盖）；否则用攒下的 `_citations`；空则 `None`（字段消失） |
| L330 | `merged or None` |
| L333 | 发完才 `closed = True` |

**`error(exc)`（L335-347）**

| 行 | 行为 |
|---|---|
| L336-337 | 已关闭直接抛——"done 与 error 只能有一个" |
| L338-339 | **如果连 meta 都没发就出错，先 `start()`**，保证客户端收到的第一帧永远是 meta，能正常渲染错误卡片 |
| L340 | `_terminal_once("error")` |
| L341-346 | `exc.to_contract(self.trace_id)` 把 `KwError` 转成契约错误体 |
| L347 | 关闭 |

> 注意：这里**不能**用 `_ensure_running`（`done` 用的是它），因为 `error` 是唯一允许在"未 running"状态下发的终止事件。

**`_terminal_once(kind)`（L349-352）**
- `_terminal` 非空 → 抛"重复终止"。这是"有且仅有一个"的**唯一实现点**。

### 1.12 close() 与只读视图（L355-374）

**`close()`（L355-361）**
- L358-359 连 meta 都没发就结束 → 主动 `start()`。
  ⚠️ **注释与代码不一致**：注释说"补一个 `error` 更友好"，代码补的是 `meta`。实际效果是保证第一帧存在，但 `_terminal` 仍为 `None`、`paused` 仍为 `False`，所以 L360 仍会抛 `ContractViolation`——客户端拿不到 `error` 帧。
- L360-361 没有终止事件且不是暂停态 → 抛：

  > 图执行结束但没有 done/error/input_required——客户端会一直挂着

  这是**最值钱的一行**：把"前端永久 loading"这类线上事故变成启动 / 测试期就能暴露的异常。

**只读视图**
- L364-366 `sequence`：已发事件数，测试里用来断言"第几帧是什么"。
- L368-370 `citations`：返回**副本**，防止外部改内部状态。
- L372-374 `unknown_code`：`pragma: no cover`，纯调试用，固定返回 `Code.INTERNAL`。

---

## 二、runtime/registry.py（251 行）

### 2.1 模块 docstring（L1-11）：六件事 + 两个取舍

- **L3** 引 `docs/01 §3.1`：工具要"统一 schema 声明、参数校验、超时重试、幂等键、结果压缩、成本标注"。
- **L4-5** 为什么必须集中：允许某个工具绕过注册表，这六件事就变成"看实现者心情"，而工具层三个归因项（**F3 取证失败 / F4 工具选错 / 结果噪声**）全靠它们才有数据。
- **L8** 取舍 1：参数校验在注册表做——工具里各写一套迟早漏。
- **L9-10** 取舍 2：结果压缩在注册表做——放到节点里就会出现"这个节点忘了压"，然后上下文爆掉。

### 2.2 导入与阈值（L13-33）

| 行 | 内容 | 说明 |
|---|---|---|
| L15-17 | `hashlib` / `json` / `time` | `_args_hash`、序列化、`perf_counter` |
| L18 | `Callable` | 装饰器返回类型 |
| L19 | `ThreadPoolExecutor` | 工具跑线程池 |
| L20 | `TimeoutError as FutureTimeout` | 别名避免和内置 `TimeoutError` 撞名 |
| L21 | `copy_context` | **contextvars 跨线程手动传递**（见 L137） |
| L22-23 | `dataclass, field` / `Any` | — |
| L25 | `jsonschema` | 参数校验。**不是 pydantic**——工具 `input_schema` 是给 LLM 看的 JSON Schema（直接塞进 function calling 的 `parameters`），用 jsonschema 校验同一份 schema 才能保证"模型看到的约束" = "执行时校验的约束" |
| L27-29 | 内部依赖 | `tracing` / `KwError` / `Code` |
| L32 | `_COMPRESS_AT = 1500` | 注释"约等于 token 的 1/1.5~1/2"→ 1500 字符 ≈ 750~1000 token，对应 07 §2.1 的 1500 token 阈值。**用字符近似 token，是有意的粗估** |
| L33 | `_KEEP_ROWS = 20` | 压缩后保留的明细行数 |

### 2.3 ToolSpec（L36-45）

- **L36** `@dataclass(frozen=True)`：规格一旦注册**不可变**，防运行时被偷偷改 schema。
- **L38-41** `name`、`description`、`input_schema`、`fn`、`timeout_ms`（默认 3s）、`idempotent`。
  - `description` **给 LLM 看**——工具描述也算 prompt（见 L118）。
  - `fn` 签名约定 `fn(**args)`。
- **L44-45** `cost_hint`：只用于排序与告警，**不参与计费**。

### 2.4 ToolResult（L48-61）

| 字段 | 说明 |
|---|---|
| `ok` | 是否成功 |
| `data` | **原始**结果，压缩不影响它 |
| `summary` | 给 LLM 的压缩文本 |
| `rows` | 行数，用于 telemetry |
| `error` | 错误串 |
| `attempt` | 重试次数（当前恒为 1，见问题清单） |
| `elapsed_ms` | 耗时 |
| `args_hash` | 入参指纹——**幂等键 / 缓存 / 去重的天然候选** |
| `compressed` | 是否被压缩 |

- **L60-61** `text()`：`summary` 优先，否则 JSON 化 `data`；`data is None` 返回空串而不是 `"null"`。

### 2.5 注册（L64-96）

- **L65-67** `__init__`：内存字典 + 固定 **8 线程**池。一个进程一个 registry、一个池（单例共享）。
- **L70-96** `register()` 装饰器工厂：
  - L80 docstring 明确签名约定 `fn(**args) -> Any`——所以 `input_schema` 的 properties 名**必须等于形参名**。
  - L82 内层 `deco`。
  - L83-84 重名 → `ValueError`。**启动期崩**，而不是运行时"后注册的悄悄覆盖前一个"。
  - L85-93 落库 `ToolSpec`。
  - L94 **返回原函数**——注册后仍可直接 `my_tool(x=1)` 调用（测试里很有用），不是被包装成别的对象。

### 2.6 自检与元信息（L98-119）

- **L98-107** `require(names)`：pack / domain.yaml 声明的工具必须全注册，缺一个就启动失败。
  - L101-102 注释说明它救的是"改了 domain.yaml 但忘了写工具"：这种错不会启动时报，只会在跑到那个节点时 500，**而那时你会误以为是模型问题**。→ 把运行时故障前移到启动期。
- **L108-109** `names()`：排序返回，便于断言 / 日志稳定。
- **L111-115** `spec(name)`：未知工具 → `KwError(Code.INTERNAL, ...)` 并 `from exc` 保留链。走 `KwError` 而不是 `KeyError`，是为了让上层能统一转成契约 `error` 事件。
- **L117-119** `descriptions()`：生成 `"name: description"` 列表给 prompt 用，按名字排序保证 prompt 稳定（影响缓存命中率）。

### 2.7 call() 前半段：查表与校验（L122-138）

| 行 | 行为 |
|---|---|
| L123 | 先取 spec（未知工具在这里就变成 `KwError`） |
| L124 | `args or {}`：`None` 归一成空 dict，后面 `fn(**args)` 才不炸 |
| L125 | `args_hash = _args_hash(args)`：错误 / 超时 / 成功三条路径都会带上，方便把"同一个坏入参反复失败"串起来 |
| L128 | `jsonschema.validate(instance=args, schema=spec.input_schema)` |
| L130-131 | 失败 → `ToolResult(ok=False, error="invalid_args: ...")`，**不重试、不抛** |
| L133 | `perf_counter()`：高精度计时，不受时钟调整影响 |
| L137 | `copied = copy_context()` |
| L138 | `self._pool.submit(copied.run, lambda: spec.fn(**args))` |

**L130-131 的归因理由**：参数不合法是**调用方（模型）bug**，既不是工具故障（F3）也不是选错工具（F4），三者必须分得开，否则归因统计全糊。

**L134-137 的注释是全文件最值钱的部分之一**：`contextvars` 不会自动跨线程传递，工具要读 `RunContext`（`rt.current()` 拿到的 `subject` 做权限过滤）和当前 OTel span，必须显式复制。漏了的表现是：

> 工具里取不到 subject，于是不召回——**一个静默的空结果，比报错难查得多**。

L138 用 `copied.run` 作为线程入口，把复制好的上下文带进线程池；`lambda` 是为了把 `**args` 绑成无参可调用。

### 2.8 call() 三条失败路径（L139-163）

**超时分支（L141-158）**

| 行 | 行为 |
|---|---|
| L139-140 | `future.result(timeout=spec.timeout_ms / 1000)`：**阻塞等待，超时抛 `FutureTimeout`** |
| L142 | `future.cancel()` ⚠️ 见下 |
| L143 | 算耗时 |
| L144-155 | 起 `tracing.span("shared.tool.call", {...})`，属性带 `tool.name / args_hash / args_keys / timeout_ms / attempt / rows` |
| L155 | `sp.set_status(_error_status())`：把 span 标成 OTel `StatusCode.ERROR`，超时在链路追踪里是红的 |
| L156-158 | 返回 `ok=False, error="timeout"`，**不含 `summary`** |

⚠️ **L142 `future.cancel()` 只在任务尚未开始执行时有效**。已经跑起来的线程取消不了 Python 线程，会继续占着 8 个池位。极端情况（工具 hang 住）下 8 个槽位被吃光，后续所有工具调用全部超时——**超时不是硬杀，只是"不再等"**。

**通用异常分支（L159-163）**

- `except Exception`（带 `noqa: BLE001`，即"我就是要全捕获"）。
- `error` 里带 `type(exc).__name__`——**工具内部异常类型本身就是归因信息**（`psycopg.OperationalError` 和 `KeyError` 完全不是一回事）。
- ⚠️ **这条路径没有起 span**，与超时分支不对称，失败工具在 trace 里会缺一个 `shared.tool.call` 记录。

### 2.9 call() 成功路径（L165-189）

| 行 | 行为 |
|---|---|
| L165 | 计时 |
| L166 | `_compress(data)` → `(summary, rows, compressed)`。**压缩统一在这里做**，节点拿到 `ToolResult.summary` 就是已压缩的 |
| L167-180 | 起 span，属性比超时分支多 `elapsed_ms` 和 `compressed`；**`with ...: pass` 空体**——只靠退出 `with` 时自动结束 span（不设 status 即默认 OK） |
| L181-189 | 返回 `ok=True` 的 `ToolResult`：**`data` 保留原始对象，`summary` 是压缩文本**，两者用途不同（前者可再加工，后者进上下文） |

### 2.10 close 与两个小工具（L191-203）

- **L191-192** `close()`：`shutdown(wait=False, cancel_futures=True)`——不阻塞等待在跑的工具，但取消排队中的。避免进程退出卡住。
- **L195-198** `_error_status()`：**函数内 import OTel**。注释说"避免在 call 里重复 import"，但更实际的理由是把 OTel 变成**软依赖**：没装 / 没启用 opentelemetry 时，只有走到"超时"路径才 `ImportError`，而不是整个模块导入就崩。（隐患：真触发时会在超时分支里抛 `ImportError`。）
- **L201-203** `_args_hash()`：
  - `sort_keys=True` 保证 `{"a":1,"b":2}` 与 `{"b":2,"a":1}` 同哈希
  - 紧凑分隔符
  - `sha256` 取前 **32 位十六进制**（128 bit，碰撞可忽略且日志友好）

### 2.11 _compress 的 L1 压缩（L206-241）

**docstring（L207-211）**
- **L1 压缩 = 保留列名 + 前 N 行 + 行数**。
- L210-211 给出反例：

  > 直接截断字符串是最差的做法：会截在半行 JSON 上，模型会把它当成完整数据。

  —— 这句是这段代码的全部理由。

**list 分支（L212-229）**

| 行 | 行为 |
|---|---|
| L212-213 | 只处理 list，**先记录真实 `rows`**（即使后面截断，行数是真的，用于 telemetry 和"还有多少没看"的提示） |
| L214-215 | 空列表 → `"（空结果）", 0, False`。显式告诉模型"确实是空的"，而不是让它拿到 `"[]"` 自己猜 |
| L216 | `all(isinstance(r, dict))`：全 dict 走表格化，否则走标量列表 |
| L217 | `cols = sorted({k for r in data for k in r})`：**全集去重 + 排序**，保证同种查询每次列顺序一致（否则 prompt 不稳定、缓存失效）；`type: ignore` 是因为 L216 的 `all()` 没帮 mypy 收窄类型 |
| L218 | `head = data[:_KEEP_ROWS]` |
| L219 | 首行 `共 N 行；列：a, b, c`——**先给"总量 + 结构"再给明细**，模型才有正确的数据规模感 |
| L220-222 | 逐行渲染 `1. k=v, k=v`，单元格走 `_short()` 截到 60 字符 |
| L223-224 | 超 20 行补一句「其余 X 行已省略（明细可用同一入参重新取回）」——**让模型知道"可以再查"而不是"数据只有这些"**。配合 `args_hash` / `idempotent` 才有可操作性 |
| L226-227 | 非 dict 列表：`共 N 项` + `- value` 列表 |
| L228 | `compressed = len(text) > _COMPRESS_AT` ⚠️ 见下 |
| L229 | `return _truncate(text), rows, compressed` |

⚠️ **L228 语义**:`_COMPRESS_AT = 1500` 只用来判"算不算被压缩过"这个**布尔标记**；真正的截断阈值在 `_truncate()` 里是 **2000**。1500~2000 字符的结果会被标 `compressed=True` 但内容其实**一字没删**——指标略偏高，功能无害。

**dict 分支（L231-238）**

- L232-236 注释解释 `default=str`——**这是兜底不是设计**：

  > 接了业务库之后 `datetime` / `Decimal` 会从 psycopg 直接漏出来，`json.dumps` 当场 `TypeError`，表现是"工具调用失败"，查半天才发现是时间对象没转。兜底后最坏是时间格式不好看，而不是整条诊断链断在取证。

- L237 `indent=None`：不美化，省 token。
- L238 dict 的 `rows` 记 **1**（一个对象 = 一条记录）。

**其他类型（L240-241）**：`str(data)`，rows=1。

### 2.12 _short / _truncate（L244-251）

- **L244-246** `_short(v)`：非 str 先 JSON 化（同样 `default=str` 兜底），超 60 字符截成 `前 57 + "…"`。**用省略号而不是静默截断**，让模型知道值不完整。
- **L249-250** `_truncate(text, limit=2000)`：`text[:2000] + "\n…（已截断，原长 N 字符）"`。**截断后补"原长"**——这是和"裸截断"的关键差别：模型知道丢了多少信息。

---

## 三、值得注意的问题清单

| # | 严重度 | 位置 | 现象 | 建议 |
|---|---|---|---|---|
| 1 | 高 | `registry.py:139-142` | `future.cancel()` **杀不掉已在运行的线程**。工具 hang 住会永久占用 8 个池位中的 1 个，累积 8 次后整个 registry 瘫痪。`timeout_ms` 只是"放弃等待"，不是硬超时 | 换子进程执行，或在工具内部做可中断设计（传入 cancel token / 分段检查 deadline） |
| 2 | 中 | `registry.py:159-163` | 通用异常分支**没有起 `shared.tool.call` span**，也不设 ERROR status。超时分支有、异常分支没有，链路追踪里失败工具会缺记录 | 把 span 提到 `try` 之前统一开，或异常分支补一个带 `error.type` 的 span |
| 3 | 中 | `events.py:358-359` | 注释说"补一个 error 更友好"，代码实际补的是 `start()`（只发 meta）。之后 L360 仍会抛 `ContractViolation`，客户端拿不到 `error` 帧 | 改成 `self.error(KwError(Code.INTERNAL, "图未产生任何终止事件"))`，或把注释改对 |
| 4 | 中 | `registry.py` docstring vs 实现 | docstring 声称实现"**超时重试**、**幂等键**"，但 `call()` 里**没有任何重试**，`ToolSpec.idempotent` 与 `cost_hint` **从未被读取**，`attempt` 恒为 1 | 要么补实现（按 `idempotent` 决定是否重试、用 `args_hash` 做幂等缓存），要么把 docstring 改成"待实现"，否则评审 / 文档会被当成已实现 |
| 5 | 低 | `events.py:78-79` | `max_steps or stop.max_steps`：传 `0` 被当成"没传" | 改 `max_steps if max_steps is not None else stop.max_steps` |
| 6 | 低 | `events.py:80` | `max_cost_usd` 没有来自 `StopCfg` 的默认值，默认等于**不限制成本** | 给 `StopCfg` 加 `budget_cost_usd`，或显式在开跑处必传 |
| 7 | 低 | `registry.py:228` vs `249` | `_COMPRESS_AT=1500` 只做布尔标记，实际截断在 2000，两者不一致导致 `compressed` 指标偏高 | 统一为一个常量，或把 `_COMPRESS_AT` 也作为 `_truncate` 的 `limit` |

---

## 四、两张速查表

### 4.1 EventSink 的守卫：谁在什么时候拦住你

| 想做 | 触发守卫 | 结果 |
|---|---|---|
| 发任何内容事件但 meta 未发 | `_ensure_running` → `start()` | 自动补 meta（懒启动） |
| 流已 `closed` 还发事件 | `_ensure_running` | `ContractViolation` |
| 已 `paused` 还发事件 | `_ensure_running` | `ContractViolation` |
| `delta` 之后才发 `citation` | `citation` L237-239 | `ContractViolation`（编号错位） |
| `delta` 引用了未发出的 eid | `delta` L252-255 | `ContractViolation`（防幻觉引用） |
| 同一 eid 发两次 | `citation` L241-242 | `ContractViolation` |
| `done` 之后又 `error` | `error` L336-337 | `ContractViolation` |
| `done` 之后再 `done` | `_terminal_once` | `ContractViolation` |
| 图跑完但没有终止事件 | `close` L360-361 | `ContractViolation`（防"客户端一直挂着"） |

### 4.2 registry 一次调用的完整路径

```
call(name, args)
 ├─ spec(name)                  未注册 → KwError(INTERNAL)
 ├─ _args_hash(args)            sha256[:32]，三条路径都带
 ├─ jsonschema.validate        失败 → ok=False, "invalid_args"（不重试）
 ├─ copy_context()             ★ 必须，否则工具里拿不到 subject / span
 ├─ pool.submit(copied.run, fn(**args))
 ├─ timeout  → cancel + span(ERROR) + ok=False "timeout"
 ├─ exception → ok=False "{ExcType}: {msg}"（⚠️ 无 span）
 └─ success  → _compress → span(OK, 带 rows/elapsed_ms/compressed)
              → ToolResult(data=原始, summary=压缩文本)
```

---

## 五、一句话总结

- `events.py` 守的是**契约的时间维度**（谁先谁后、谁只能有一次），代价是多一层 `EventSink`；
- `registry.py` 守的是**工具的空间维度**（统一声明、校验、计时、压缩），代价是所有工具必须走注册表。

两者共享同一个前提：**这些约束只能有一处实现**，否则就会退化成"看实现者心情"。
