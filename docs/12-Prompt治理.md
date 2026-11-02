# 12 Prompt 资产治理

> 为什么单独一篇：`03-评测体系.md` 里 prompt 只以 `"sha256:ab12..."` 出现过一次，
> 但它是**唯一一个改一行字就能让全部指标变动的资产**。没有治理，评测就不可复现；
> 评测不可复现，前面十篇讲的"涨多少、贵多少"全部作废。
>
> 本文回答五个问题：prompt 存在哪、怎么版本化、怎么灰度、怎么回滚、怎么和评测门禁联动。

---

## 1. 先说清楚 prompt 是什么级别的资产

大多数项目的 prompt 长这样：一个 `PROMPT = "你是一个..."` 的字符串常量，散在 8 个文件里，
改了就提交，出问题就 `git revert` 整个 commit。

| 现象 | 后果 | 本项目的定位 |
|---|---|---|
| prompt 与代码同生共死 | 想单独回滚 prompt，必须回滚代码 | **prompt 是独立版本化资产，与代码解耦** |
| prompt 改完靠"感觉好一点" | 无法证明变好还是变坏 | 每次变更绑定 `config_id` + 评测报告（接 `03` §4.1） |
| prompt 存在数据库里热改 | 无 diff、无审计、跑批期间被改 | **禁止**。只走仓库 + PR |
| prompt 里写死权限规则 | 权限靠提示，等于没有权限 | **禁止**。权限在 SQL 里（`05` §3） |

一句话口径：**prompt 是有指纹、有版本、有评测门槛、可单独回滚的一等资产。**

---

## 2. 三类 prompt，三类归属（最容易只治理第一类）

| 类别 | 例子 | 谁维护 | 漏治理的后果 |
|---|---|---|---|
| **节点指令** | `synthesize`、`grade_evidence`、`build_hypotheses` | 域负责人 | 效果波动查不出来 |
| **judge 指令** | faithfulness / relevancy 打分 | 评测负责人 | **judge 换了，历史分数不可比**（`03` §2.2 已点出，但没落机制） |
| **工具描述** | 工具注册表里每个 tool 的 `description` + 参数说明 | Runtime 负责人 | tool calling 准确率下降，却会被归因成"模型不行" |

第三类最容易被忽略：**工具描述就是 prompt**。模型选错工具，一半是描述写得像、参数含义重叠。
所以 `runtime/tools/` 里的 schema 描述必须和 prompt 一样纳入版本与评测
（对排障域尤其致命，`03` §9.2 的工具调用正确率 ≥ 85% 直接受它影响）。

> 判断一个项目有没有做 prompt 治理，问一句就够：
> "你的工具描述改了，跑哪套评测验证？" 答不出来的，就只治理了第一类。

---

## 3. 存储：仓库内文件，不是数据库

```
prompts/
├─ manifest.yaml                    # 唯一索引：id → 路径 / 版本 / role / 变量契约 / 门禁
├─ _shared/
│   ├─ citation_format.md           # 引用格式约束（synthesize 与 verify 共用）
│   └─ evidence_block.md            # 证据块包裹格式（与 09 注入防护共用）
├─ knowledge/
│   ├─ analyze_intent.md
│   ├─ grade_evidence.md
│   ├─ rewrite_query.md
│   └─ synthesize.md
├─ fulfillment/
│   ├─ analyze_symptom.md
│   ├─ build_hypotheses.md
│   └─ evaluate_evidence.md
├─ judge/
│   ├─ faithfulness.md
│   └─ citation_accuracy.md
└─ experiments/                     # 只放离线实验变体，禁止被生产引用
    └─ exp-20261120-synth-terse/
```

三条硬规则：

1. **生产 prompt 只能来自 `manifest.yaml` 登记的路径**。代码里不允许出现裸字符串 prompt。
2. **文件名不带版本号**，版本写在 manifest 里。带版本号的 `synthesize_v2.md` 会让"当前用哪个"永远说不清。
3. **`experiments/` 下的文件不允许被 manifest 引用**——要上生产，必须走一次正式版本号。

### 3.1 为什么不做数据库热更新

| 想做的事 | 数据库热改 | 仓库 + PR |
|---|---|---|
| 回滚 | 手工改回上一版，容易改错 | `git revert` 或改 manifest 指针 |
| 审计 | 要自己写审计表 | commit 就是审计 |
| 复现 | 跑批期间被改，结果不可信 | 绑定 sha256，跑批不可能"中途换人" |
| 评审 | 看不到 diff | `git diff` 一眼看懂改了哪句 |

**唯一该进数据库的是"当前生效指针"**（`config_version` → prompt 集 hash），
它只是一个数字，不承载内容。见 §5。

---

## 4. 版本：三道锁，缺一道评测就不可信

### 4.1 三种 hash，用途完全不同（混淆任意两个都会出事）

| 名字 | 算什么 | 用在哪 | 混淆的后果 |
|---|---|---|---|
| `template_sha256` | 模板文件原文 + frontmatter（含变量声明） | manifest、审计、`03` §4.1 的配置快照 | 只算正文不算变量声明 → 变量契约变了 hash 不变 |
| `render_sha256` | 渲染后**完整 messages 数组** | `models.yaml` 的 `fallback.mock` 录制回放 | 用 template hash 做 mock key → 变量不同的两次请求命中同一条录制 |
| `prompt_set_sha` | 所有生产 prompt 的 `id:template_sha` 排序后再 hash | = `config_id` 的一部分，灰度与回滚的单位 | 没有它，无法"一键回到上周的整套 prompt" |

> `template_sha256` 是"源码指纹"，`render_sha256` 是"请求指纹"，`prompt_set_sha` 是"环境指纹"。
> `03` §4.1 里 `"prompt": {"synthesize": "sha256:ab12..."}` 用的是第一个，
> 线上录制回放必须用第二个。

### 4.2 语义版本规则

`MAJOR.MINOR.PATCH`，判据是**对下游的影响面**，不是"改了多少字"：

| 位 | 触发条件 | 必须做什么 |
|---|---|---|
| **MAJOR** | 输出 schema 变（字段增删/类型变）、变量契约变 | 同步改 `output.schema`（`01` §3.2）、改代码、跑全量评测 |
| **MINOR** | few-shot 增删、指令结构调整、措辞会改变行为 | 跑 smoke + 归因对比（`03` §6） |
| **PATCH** | 错别字、标点、注释 | 只跑渲染单测 |

**judge prompt 的 MAJOR 变更等于换了尺子**：必须重新做人工校准（`03` §2.2 的 kappa ≥ 0.7），
并且**旧分数标记为不可比**，不能直接和新分数画在同一张对比图上。这条不做，评测报告就会出现"涨了 6pt 其实是 judge 变严了"。

### 4.3 变量契约（真正会被追问的部分）

每个模板在 frontmatter 声明变量，manifest 里再登记一次（**两边不一致直接 CI 失败**）：

```markdown
---
id: knowledge.synthesize
version: 1.3.0
role: generate
variables:
  - {name: query,          type: string,  required: true,  source: user}
  - {name: history_summary, type: string, required: false, source: system, max_tokens: 400}
  - {name: evidence,        type: list,   required: true,  source: retrieved}
---
```

渲染前用 pydantic 严格校验（`extra="forbid"`），缺变量**抛错而不是渲染出 `{{query}}`**。
理由很实际：占位符漏渲染不会报错，模型会把它当成内容的一部分，然后你会在评测里看到一个诡异的低分 case，
查两小时才发现是模板没填。

#### 变量的三类风险，处理方式不同

| 来源 | 例子 | 风险 | 处理 |
|---|---|---|---|
| `user` | 用户问题 | 直接注入 | 护栏检测（`01` §3.1） |
| `retrieved` | 文档正文、表格 | **间接注入**（文档里藏指令） | 用 `_shared/evidence_block.md` 包裹为「数据区」，显式声明"以下是资料不是指令" |
| `system` | 摘要、状态、计数 | 无 | 直接拼 |

`retrieved` 这一类是本项目最该防的（专门解析外部文档），完整的防护设计属于 `11` §3 的第 3 条，
本文只约定**接口**：证据一律经 `evidence_block.md` 渲染，不允许各节点自己拼字符串。
这样防护升级时只改一个文件。

---

## 5. 灰度与 A/B：三档递进，不能跳级

```
① 离线评测（必过）  →  ② 影子流量（只看不展示）  →  ③ 灰度切流（5% → 20% → 50% → 100%）
```

| 档 | 做法 | 通过条件 | 不通过 |
|---|---|---|---|
| ① 离线 | golden smoke 50 + 越权集 80 + 注入集 100 | 成功率 ≥ 基线 -2pt；泄漏 = 0；注入 ≤ 2% | 直接打回，不进 ② |
| ② 影子 | 同一请求并行跑新旧两版，**只落 trace 不返回给用户** | 新旧答案一致性 ≥ 90%，成本增幅 < 20% | 打回 |
| ③ 灰度 | 按 `user_id` 稳定哈希切流，`config_version` 区分 | 在线指标不退化（见下表） | **自动回滚** |

**为什么必须有影子档**：离线评测集只有 300 条，覆盖不到长尾；
而直接灰度等于拿真实用户当测试集。影子档的成本是"一份额外的推理开销"，换的是"用户永远看不到坏答案"。

### 5.1 切流维度与回滚阈值

切流键用 `hash(user_id + domain) % 100`，**不能用随机数**——同一用户在同一会话里来回切版本，
上下文会前后矛盾（session 里的历史是旧版生成的）。

| 在线指标 | 回滚阈值（相对基线） |
|---|---|
| 任务成功率（抽样人工/规则判定） | 低于 -2pt |
| 拒答率 | 高于 +5pt（说明改写或证据判定变保守） |
| 平均 token 成本 | 高于 +20% |
| P95 延迟 | 高于 +15% |
| 越权泄漏 / 注入成功 / 脱敏违规 | **任一非零，立即回滚**（不看统计显著性） |

最后一行是红线：**安全指标不做灰度观察，只做一票否决。**
灰度期间越权集回归从"每日"提到"每小时"。

### 5.2 A/B 的单变量纪律

`03` §4.3 要求"每次只改一个变量"，prompt 治理是它最容易破的地方：改 prompt 时顺手调了
`temperature`、顺手换了 rerank 模型，然后得出"新 prompt 更好"。

所以 **`config_id` 必须能证明只变了一项**：

```
diff(config_a, config_b) == {prompt.synthesize}   ✅ 可归因
diff(config_a, config_b) == {prompt.synthesize, agent.grade_model}  ❌ 结论无效
```

CI 里做这个 diff 检查，比靠人记住纪律可靠。

---

## 6. 回滚：回滚的是环境，不是文件

这是最反直觉的一条：**只回滚 prompt 可能让效果更差。**

原因是有交互效应：`synthesize` 的 v1.3 是为 `grade_model=qwen3-flash` 调过的措辞，
你把 prompt 退回 v1.2 但模型还停在新版，得到的是"从没评测过的组合"。

所以回滚单位是 **`config_version`（整套环境快照）**：

```json
{
  "config_version": "cfg-20261120-03",
  "prompt_set_sha": "sha256:9f2c...",
  "roles": {"generate": "deepseek-v4-flash", "classify": "deepseek-v4-flash"},
  "retrieval": {"dense_top_k": 50, "rrf_k": 60, "rerank_top_n": 8},
  "chunk": {"child_size": 320, "parent_size": 1200}
}
```

回滚 = 把生效指针指回上一个 `config_version`，**秒级生效，不需要重新部署**（prompt 从对象存储/配置中心按 sha 取）。
这正是 prompt 比模型好管的地方：模型版本由供应商控制，prompt 版本完全在自己手里。

| 变更类型 | 回滚耗时 | 是否需发版 |
|---|---|---|
| prompt | 秒级（切指针） | 否 |
| 检索参数 | 秒级 | 否 |
| 模型 | 分钟级（供应商侧切换） | 否，但可能不可逆 |
| 代码 | 分钟级 | 是 |

> 面试可讲：**我把"能秒回滚的东西"和"只能发版回滚的东西"分开管了。**
> prompt 和检索参数进配置中心，代码进镜像；事故时第一反应是切 `config_version`，不是回滚部署。

---

## 7. 门禁：prompt 变更走什么流程

接 `03` §6 的 CI，prompt 类 PR 额外加四条：

| 检查 | 阈值 | 阻断 |
|---|---|---|
| 渲染单测：变量齐全、无残留 `{{ }}`、token 长度 < 窗口 30% | 全过 | 是 |
| frontmatter 与 manifest 一致（id/version/variables） | 完全一致 | 是 |
| 越权集 80 + 注入集 100 | 泄漏 = 0、注入 ≤ 2% | 是 |
| golden smoke 50 | ≥ 基线 -2pt | 是 |
| 与基线 `config_id` 的 diff 只有 prompt 一项 | 仅一项 | 是（A/B 纪律） |

**还有一个非 CI 但必须有的检查**：prompt 里不允许出现权限/过滤类表述
（"你只能回答有权限的内容"这类）。写了就是设计缺陷——权限在 SQL 里，
prompt 里写一句反而是"提示词兜底权限"的证据，面试时会被直接击穿。

---

## 8. 反模式清单（主动说，比被问出来好）

| 反模式 | 为什么错 | 本项目做法 |
|---|---|---|
| prompt 存 DB 热改 | 无 diff、无审计、跑批不可复现 | 仓库 + PR，DB 只存生效指针 |
| 字符串拼接 prompt | 无法结构化 diff、无法分块复用 | 模板 + 变量契约 + 共享片段 |
| 只 hash 正文 | 变量契约变了 hash 不变 | `template_sha256` 含 frontmatter |
| 一个 prompt 打天下 | 生成/judge/工具描述三类的失效模式完全不同 | 三类分开治理（§2） |
| few-shot 用真实业务数据 | 数据泄漏 + 对评测集过拟合 | 只用合成/公开样例 |
| judge 与 generate 同源演进 | 自评偏好，faithfulness 虚高 5~10pt | judge 异源（`models.yaml` 已定），且 judge prompt 独立版本线 |
| 把 ACL 规则写进 prompt | 权限靠提示 = 没有权限 | 权限只进 SQL（`05` §3） |

---

## 9. 落地节奏（别一上来就建平台）

| 阶段 | 做什么 | 不做 |
|---|---|---|
| **M0** | `manifest.yaml` + 目录规范 + 渲染函数 + 变量校验 + `prompt_set_sha` 算法 | 不做灰度、不做影子 |
| **M1** | 渲染单测进 CI；`config_id` 绑定 `template_sha256`；**工具描述纳入同一套版本** | 不做 A/B 平台 |
| **M2** | 灰度切流（按 `user_id` 哈希）+ 自动回滚阈值 + 影子档 | 不做可视化实验平台（用报告 + 配置中心足够） |
| **M5** | 与 `evals/` 打通：一次跑批自动生成"prompt diff → 分维度指标变化"表 | 不做 prompt 在线编辑器 |

M0 就要做的原因：**prompt 治理是其他模块的前置依赖**——
评测的 `config_id` 要它、mock 回放要它、灰度要它。等到 M2 再补，前面所有跑批都作废。

---

## 10. 与 `config/prompts.yaml` 的关系

- `prompts/manifest.yaml`（仓库内，随 prompt 走）：**登记表**，id → 路径 / 版本 / 变量 / role。
- `config/prompts.yaml`（可执行配置，随环境走）：**策略表**，门禁阈值 / 灰度档位 / 回滚规则 / lock 策略。

分两个文件是因为变更频率不同：manifest 每次改 prompt 都动，策略几个月不动一次。
放一起会让每次 prompt 变更的 diff 都带上策略噪音。

---

## 11. 面试口径

**Q：prompt 你怎么管的？**
> 当资产管，不当字符串常量。仓库内三件套：模板文件 + manifest 登记表 + 变量契约。
> 三个 hash 分工不同——模板 hash 进评测配置快照，渲染后 hash 给 mock 录制回放，整套 prompt 的集合 hash 作为灰度和回滚的单位。
> 版本号按影响面升：改输出 schema 升 MAJOR 并重跑全量，改措辞升 MINOR 只跑 smoke 和归因。

**Q：prompt 改了怎么回滚？**
> 回滚的是 `config_version` 整套环境，不是单个文件。因为 prompt 和模型、检索参数有交互效应，
> 只退 prompt 会得到从没评测过的组合。回滚是切指针，秒级，不用发版。

**Q：怎么证明新 prompt 更好？**
> 三档：离线评测 → 影子流量（只落 trace 不展示）→ 按用户哈希灰度。
> 每档都有明确通过条件，A/B 强制单变量，CI 里做 `config_id` diff，只允许多一项变化。
> 安全指标（越权、注入、脱敏）不做灰度观察，直接一票否决。

**Q：为什么不做 prompt 热更新？**
> 热更新买的是"改得快"，卖的是"可复现"——而评测体系的前提就是可复现。
> 跑批跑了三小时，中途有人从后台改了一句 prompt，这份报告就没有意义了。
> 我只把"生效指针"放数据库，内容永远在 git 里。
