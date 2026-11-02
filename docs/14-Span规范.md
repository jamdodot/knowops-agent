# 14 Span 规范

> 为什么单独成篇：`03` §5.2 的归因树写得很清楚（检索 span → 证据 span → 权限 span），
> 但**没有一个字规定 span 叫什么、带哪些属性**。这层不定，`03` §5.3 那段 `diagnose()` 就写不出来——
> 你没法从 trace 里取 `retrieved_top50`，因为没人定义过它记在哪。
>
> 一句话：**评测体系的上限，由 span 结构决定。**

---

## 1. 三条原则（先定原则，再定字段）

| 原则 | 含义 | 违反的后果 |
|---|---|---|
| **名字稳定，维度进属性** | span 名只表达"在做什么"，变化的东西（top_k、部门、模型）放属性 | 名为 `retrieve_top20` 的 span 一改参数就断历史看板 |
| **低基数名字 + 高基数字段** | 名字/属性键基数可控，值可以很散 | 把 `query` 文本塞进 span 名 → 基数爆炸，存储与查询双崩 |
| **归因需要的才记** | 每个属性都要能回答"哪个 E 类/ F 类归因要用它" | 记了一堆没人查的字段，成本涨、噪声大 |

第 3 条是反向的：**如果一个属性没有归因用途，就不要记。**

---

## 2. 命名规范

```
{domain}.{layer}.{operation}
```

| 段 | 取值 | 说明 |
|---|---|---|
| `domain` | `knowledge` / `fulfillment` / `shared` | 域；共享内核用 `shared` |
| `layer` | `http` / `graph` / `node` / `llm` / `tool` / `retrieval` / `acl` / `memory` / `a2a` | 层，共 9 个，不再扩张 |
| `operation` | 小写下划线 | 动词性，如 `dense_search`、`grade_evidence` |

示例：

| span 名 | 层级 | 说明 |
|---|---|---|
| `knowledge.graph.run` | graph | 一次图执行的根 |
| `knowledge.node.retrieve` | node | 图节点（节点名进属性，不拼进 span 名） |
| `shared.retrieval.dense_search` | retrieval | 向量那一路 |
| `shared.retrieval.bm25_search` | retrieval | 稀疏那一路 |
| `shared.retrieval.rerank` | retrieval | 重排 |
| `shared.acl.build_filter` | acl | 拼鉴权条件（不查库，只拼） |
| `shared.llm.chat` | llm | 所有模型调用统一成这一个名字 |
| `shared.tool.call` | tool | 所有工具调用统一（工具名进属性） |
| `shared.a2a.send_task` | a2a | 跨域委派 |
| `shared.memory.recall` | memory | 记忆召回 |

**为什么 `shared.llm.chat` 不按角色细分**：`synthesize` 和 `grade` 的调用是两件事，
但它们**都是 LLM 调用**。按角色拆 span 名会让"LLM 总耗时/总成本"这个最重要的问题变成 N 个查询的并集。
角色放属性 `knowops.llm.role`，一次聚合就出来。

> 同理，**图节点也不进 span 名**。节点会增删改名，span 名跟着改，历史数据就断了。
> 节点名放 `knowops.node`，这样"retrieve 节点的平均耗时"永远查得到。

---

## 3. 一次请求的完整 span 树

### 3.1 知识域（应用调 agent）

```
[java] http.server POST /v1/chat                      ← root（trace 由 Java 起）
├─ [java] shared.acl.resolve_subject                  ← 主体属性解析（05 §4 缓存命中与否）
└─ [java] http.client POST /v1/knowledge/answers
    └─ [py] http.server POST /v1/knowledge/answers    ← traceparent 续链，同 trace_id
        ├─ knowledge.graph.run                         ← 图执行根（stop_reason 记在这）
        │   ├─ knowledge.node.analyze_intent
        │   │   └─ shared.llm.chat        role=classify
        │   ├─ knowledge.node.retrieve
        │   │   ├─ shared.acl.build_filter            ← 拼条件（不查库）
        │   │   ├─ shared.retrieval.dense_search      ← 一条 SQL，含 permitted CTE
        │   │   ├─ shared.retrieval.bm25_search
        │   │   └─ shared.retrieval.rerank            ← 超时则标 degraded
        │   ├─ knowledge.node.grade_evidence
        │   │   └─ shared.llm.chat        role=classify, prompt=grade_evidence
        │   ├─ knowledge.node.synthesize
        │   │   └─ shared.llm.chat        role=generate, prompt=synthesize
        │   └─ knowledge.node.verify_citations
        └─ shared.memory.recall / shared.memory.write
```

### 3.2 排障域 + A2A + 中断恢复

```
        ├─ knowledge.node.delegate
        │   └─ shared.a2a.send_task       skill=diagnose_order_symptom
        │       └─ [py-fulfillment] shared.a2a.handle_task    ← 第二个进程，同 trace_id
        │           └─ fulfillment.graph.run
        │               ├─ fulfillment.node.analyze_symptom
        │               ├─ fulfillment.node.build_hypotheses
        │               │   └─ shared.memory.recall   (情景记忆 top-3)
        │               ├─ fulfillment.node.gather_evidence
        │               │   └─ shared.tool.call  name=search_logs   × N
        │               ├─ fulfillment.node.evaluate
        │               └─ fulfillment.node.propose_action
        │
        │  ⏸ input_required（图暂停，流关闭）
        │
        └─ shared.a2a.resume_task   ⇢ Span Link 指回 propose_action
```

**这里必须用 Span Link，不能用父子关系。** 理由：恢复可能发生在 20 分钟后，
如果 `resume_task` 是 `propose_action` 的子 span，那么父 span 的 duration 就变成了 20 分钟，
所有"节点耗时"统计全被污染。Link 表达的是"相关但非连续执行"，语义正确。

> 这是 OTel 里少数几个"用错就等于埋错"的地方，面试讲出来很能说明真用过。

---

## 4. 必填属性（按层）

命名遵循：OTel 语义约定优先（`gen_ai.*`、`db.*`、`http.*`），自定义一律 `knowops.*` 前缀。

### 4.1 所有 span 共有

| 属性 | 类型 | 用途 |
|---|---|---|
| `knowops.domain` | string | 双域拆分统计 |
| `knowops.session_id` | string | 与 `chat_message` 关联 |
| `knowops.turn_seq` | int | 与 `chat_turn` 对齐 |
| `knowops.config_version` | string | 实验对比的分组键（`12` §5） |
| `knowops.prompt_set_sha` | string | prompt 环境指纹（`12` §4.1） |
| `knowops.degraded` | string[] | 显式降级，禁止静默（`models.yaml` 纪律） |

### 4.2 按层

| 层 | 必填属性 | 归因用途 |
|---|---|---|
| `retrieval.*` | `knowops.retrieval.route`(dense/sparse/graph) `knowops.retrieval.top_k` `knowops.retrieval.hits` `knowops.retrieval.topk_ids`(≤50) `knowops.retrieval.rows_scanned` `knowops.chunk.offset_range`(top-k 的起止偏移) | **E3/E4**：金标在不在前 50、排第几；**E2**：是否跨 chunk 边界 |
| `retrieval.rerank` | 另加 `knowops.rerank.rank_before` `knowops.rerank.rank_after` | **E4**：重排是变好还是变坏 |
| `acl.*` | `knowops.acl.filter_hash` `knowops.acl.dept_paths_n` `knowops.acl.approval_n` `knowops.acl.rows_filtered` `knowops.acl.verdicts` | **E8/E9**：`acl_verdicts` 就是 `03` §5.2 用的那个字段 |
| `llm.chat` | `gen_ai.request.model` `gen_ai.usage.input_tokens` `gen_ai.usage.output_tokens` `knowops.llm.role` `knowops.prompt.id` `knowops.prompt.version` `knowops.prompt.template_sha` `knowops.llm.cost_usd` `knowops.llm.cache_hit` | **E6**：幻觉时能定位到哪个 prompt 版本 |
| `tool.call` | `knowops.tool.name` `knowops.tool.args_hash` `knowops.tool.args_keys` `knowops.tool.rows` `knowops.tool.timeout_ms` `knowops.tool.attempt` | **F3/F4**：取证失败 vs 工具选错 |
| `graph.run` | `knowops.stop_reason`(sufficiency/steps/tokens/converged/escalate) `knowops.steps` `knowops.hypotheses_n` | **E10/F6**：停止原因是否正确 |
| `a2a.*` | `knowops.a2a.skill` `knowops.a2a.task_id` `knowops.a2a.state_to` `knowops.a2a.resumed` | 跨域协作与降级 |
| `node.*` | `knowops.node` `knowops.step` | 节点耗时与步数分布 |

### 4.3 `knowops.acl.verdicts` 的结构（最容易被追问的一个字段）

```json
{
  "doc_881": {"visible": true,  "by": "dept:D10.3"},
  "doc_902": {"visible": true,  "by": "approval:AP-01"},
  "doc_915": {"visible": false, "by": "none", "filtered_at": "permitted_cte"}
}
```

只对"本次候选 top-k 涉及的 doc"记，不记全库——否则一个属性几百条，成本失控。

这个字段同时解决两件事：
1. **E8 权限误过滤**：本该可见却被过滤，能直接看出是哪一路没命中；
2. **`05` §8 的可解释性**：用户申诉"为什么看不到这份文档"，能答出"命中 0 路"而不是"在集合外"。

---

## 5. 归因映射：每类错误要读哪个 span

`03` §5.1 定义了 E1~E10，§9.3 定义了 F1~F7。这张表把它们钉到具体字段上——**归因代码就是按这张表写的**。

### 5.1 知识域

| 归因 | 读哪里 | 判定 | 缺了会怎样 |
|---|---|---|---|
| **E1 解析失败** | 不在请求 trace 里，读 `parse_report`（入库链路 trace） | `text_coverage` / `table_teds` / `header_footer_leak` 偏低 | 会把解析问题误判成检索问题 |
| **E2 分块断裂** | `retrieval.topk_ids` + `knowops.chunk.offset_range` | 金标答案跨两个 chunk 且各自不完整 | 只能人工看原文 |
| **E3 召回未命中** | `retrieval.topk_ids` 是否含金标 | 不含 → E3 | **归因直接做不了** |
| **E4 排序失当** | `topk_ids` + `rerank.rank_before/after` | 在 top50 但不在 top8 | 分不清是召回还是排序 |
| **E5 证据噪声** | `knowops.evidence.sent_ids` + `count` | 送进模型的证据里无关项占比高 | 会把噪声误判成幻觉 |
| **E6 生成幻觉** | `llm.chat`(synthesize) + `evidence.sent_ids` | 证据有金标但答案不受支撑 | 无法区分 E5/E6 |
| **E7 引用错误** | `verify_citations` 的 `knowops.verify.failed_eids` | 引用编号 ∉ 证据集 | 只能人工核对 |
| **E8 权限误过滤** | `acl.verdicts` | 金标文档 `visible=false` | 会去改检索，越改越差 |
| **E9 越权泄漏** | `acl.filter_hash` + `retrieval.route` | 泄漏 + filter 缺失/为空 | 红线事故无法定位 |
| **E10 拒答不当** | `graph.run.stop_reason` + `knowops.evidence.sufficiency` | `abstain` 但证据其实够 | 只能看最终结果猜 |

> **E1 的诚实说法**：它不在请求 trace 里，因为解析发生在入库时。
> 所以归因入口是 `parse_report`，不是一个 span——这一点不写清楚，写代码时会到处找"解析 span"。

### 5.2 排障域

| 归因 | 读哪里 | 判定 |
|---|---|---|
| **F1 症状解析错误** | `analyze_symptom` 的 `knowops.symptom.fields` | 结构化出的订单号/环节/现象与工单不符 |
| **F2 假设缺失** | `build_hypotheses` 的 `knowops.hypotheses`（根因枚举列表） | 正确根因从未进候选 |
| **F3 取证失败** | `tool.call` 的 status / `knowops.tool.attempt` | `timeout` / `permission_denied` / `error` |
| **F4 工具选错** | `tool.call` 的 name 集合 | 缺 `search_logs` 或多了无关工具 |
| **F5 判证偏差** | `evaluate` 的 `knowops.eval.verdict` | 证据支持却判成反驳 |
| **F6 提前收敛** | `graph.run.stop_reason=converged` + `confidence` + `evidence_n` | 置信度够但证据条数为 1~2 |
| **F7 动作不当** | `propose_action` 的 `knowops.action.type` / `risk_level` | 命中 `forbidden_actions`（红线） |

**F2 与 F5 的区分就靠 `knowops.hypotheses` 这一个字段**：正确根因在列表里但判错了 → F5；
不在列表里 → F2。没有这个属性，两类问题永远分不开，而它们的修复手段完全不同
（F2 加情景记忆召回，F5 改 prompt 或换模型）。

---

## 6. 采样与留存

| 场景 | 策略 | 理由 |
|---|---|---|
| 本地 / CI（M0~M1） | **100% 全采** | 量小，且这个阶段就是在调 span 本身 |
| 线上常态 | head 采样 20% + **tail-based 保留** | 头采样的经典坑：把错误采掉了 |
| tail 必留条件 | `status=error` / P95 以上 / `degraded` 非空 / 评测 case / 越权疑似 | 这五类是唯一值得付存储成本的 |
| 评测跑批 | 100%，且**永久归档**到对象存储 | `03` §5.2 要求事后能重跑归因 |
| 线上 trace 留存 | 30 天（Langfuse） | 超过 30 天的投诉走 `audit_log`，不走 trace |

**为什么必须 tail-based**：head 20% 采样下，"错误率 2%"意味着 98% 的错误案例一开始就没进 trace，
等你发现某个 E 类归因变多时，手上没有样本。tail 采样要求 collector 缓冲完整 trace 再决定——成本高，但只有这样才能保住错误样本。

> 诚实的边界：本地做的是 head 全采（`OTEL_TRACES_SAMPLER=always_on`），
> tail-based 需要 collector，M5 才上。这一点要在文档里承认，不然"我做了 tail sampling"一追问就穿。

---

## 7. 隐私边界：span 记什么、绝不记什么

| 内容 | 记不记 | 替代方案 |
|---|---|---|
| 用户问题原文 | ❌ 不记 | `knowops.query.sha256` + `len` + `query_type` |
| 文档正文 | ❌ 不记 | `doc_id` + `chunk_id` + `section_path` |
| 模型输出全文 | ❌ 不记 | `knowops.answer.sha256` + `len`；全文在 `chat_message` |
| prompt 全文 | ❌ 不记 | `prompt.id` + `version` + `template_sha` |
| 工具入参 | ⚠️ 只记 hash + key 名 | `args_hash` + `args_keys`（**值不记**） |
| 手机号 / 地址 | ❌ | 脱敏发生在 span 之前（`05` §7 列级） |
| 可见文档判定 | ✅ 记 | `acl.verdicts`（只有 doc_id 与来源，无正文） |

**trace 与 `audit_log` 的分工**（两者经常被混为一谈）：

| | `audit_log`（MySQL） | trace（OTel） |
|---|---|---|
| 回答什么 | **谁**在什么时候看了**哪些文档** | **为什么**能看、**哪一步**出的问题 |
| 存储 | 关系表，可查可导出 | 列式/对象存储，按 trace_id 查 |
| 留存 | 长期（合规） | 30 天（运维） |
| 能否删除 | 不能（管理员访问也要留痕） | 可过期 |

一句话：**审计要可读，trace 要可分析。** 把审计需求塞进 trace，合规抽检时会发现查不出来。

---

## 8. 与业务表的 join

`trace_id` 是唯一 join key，三处对齐：

| 表/文件 | 字段 | 用途 |
|---|---|---|
| `chat_turn`（MySQL） | `trace_id` | 一轮问答 ↔ 一条 trace（`07` §3.1） |
| `eval_case_result`（PG） | `trace_id` | 评测 case ↔ trace（`03` §4.2） |
| `action_request`（MySQL） | `trace_id` | 审批动作 ↔ 触发它的那次诊断 |

`04` M5 的验收项"一条跨 2 进程的完整 trace"，本质就是验证这三个 join 都能串起来。

---

## 9. 开销预算

| 项 | 预算 | 控制手段 |
|---|---|---|
| 延迟开销 | < 3% P95 | 异步批量导出（5s 一批），不阻塞请求 |
| 属性体积 | 单 span < 2KB | `topk_ids` 上限 50；`verdicts` 只记 top-k 涉及文档 |
| 基数 | 单属性值 < 1 万 | 禁止把 query/答案/id 列表以外的高基数值进属性 |
| 存储 | 每 trace < 200KB | 不记全文（§7） |

**最容易被忽略的是基数**：`knowops.tool.args_hash` 是天然高基数（每次工具调用都不同），
它不能用于聚合查询，只能用于"这两次调用是不是同一个请求"的比对。
所以它必须和其他可用于 group by 的属性分开使用——混着查会让聚合查询慢到不可用。

---

## 10. 反模式

| 反模式 | 后果 | 正确做法 |
|---|---|---|
| span 名里塞变量（`retrieve_top20`） | 改参数即断历史 | 名字稳定，参数进属性 |
| 一个节点一个 span，不带任何属性 | 归因时什么都查不到，等于没埋 | 按 §4 表格填必填项 |
| 两次 LLM 调用塞进一个 span | token 与成本无法归到具体 prompt | 一次调用一个 span |
| 把 prompt/答案全文当属性 | 成本暴涨 + 隐私事故 | 记 hash + 长度 |
| head 采样 20% 就上线 | 错误样本被采掉，归因无据 | tail-based 保错误 |
| 用 span 代替日志打 200 行 SQL 结果 | trace 存储爆掉，查询变慢 | 明细进 artifact/日志，span 只记计数 |
| 中断恢复用父子 span | 父 span duration 被拉长到小时级，所有耗时统计失真 | **Span Link** |

---

## 11. 落地节奏

| 阶段 | 做什么 | 验收 |
|---|---|---|
| **M0** | 命名规范 + 共有属性 + `http`/`graph`/`node` 三层埋点 + traceparent 透传 | `/healthz` 和假链路的 trace 在 Jaeger/Langfuse 里能看到完整树 |
| **M1** | `retrieval.*` + `acl.*` 属性补齐（**E3/E4/E8 必须先可用**） | 拿一个失败 case 能自动判出 E3 还是 E4 |
| **M2** | `llm.chat` 的 prompt/model/token/cost + `evidence.sent_ids`（E5/E6/E7） | 成本能按 prompt 版本拆开 |
| **M4** | `tool.*` + `a2a.*`（F3/F4）+ Span Link 恢复 | 断点恢复的 trace 可回放 |
| **M5** | tail-based 采样 + Langfuse 看板 + 归因自动化（`03` §5.3） | 80% case 自动归因，10 分钟内定位 |

**M1 就要 E3/E4/E8 的原因**：检索层是瓶颈（`03` §2.1 那条"Recall@50 不上去，后面所有 prompt 优化都是徒劳"），
先让这三个可归因，比先把所有 span 都埋上更划算。

---

## 12. 面试口径

**Q：可观测性你怎么做的？**
> 用 OTel + Langfuse，但重点不是接了什么平台，是 span 结构怎么定的。
> 三条原则：名字稳定、维度进属性、只记归因需要的。
> 所以 `shared.llm.chat` 只有一个名字，角色放属性——不然"LLM 总成本"这个最该一眼看到的问题，要查 N 次再合并。

**Q：归因是怎么自动化的？**
> 因为 span 里有归因需要的东西。比如判断 E3 还是 E4，就看 retrieval span 里的 `topk_ids` 有没有金标——
> 没有是召回问题，有但不在前 8 是排序问题。判断 E8 就看 `acl_verdicts`，金标文档被判不可见就是权限误过滤。
> 没有这些字段，`diagnose()` 那个函数根本写不出来，只能人工看。

**Q：中断恢复的 trace 怎么串？**
> 用 Span Link 不用父子。诊断暂停 20 分钟后恢复，如果做成子 span，父 span 的时长就变成 20 分钟，
> 所有节点耗时统计全废。Link 表达的是"相关但非连续"，这才是恢复的真实语义。

**Q：trace 里会不会有敏感数据？**
> 不记全文。问题、答案、prompt、文档正文都只记 hash 和长度，工具入参只记 hash 和 key 名，值不落。
> 审计需求和可观测需求是两件事：谁看了哪些文档进 `audit_log`，要可读、长期留存；
> 为什么能看、哪一步出错进 trace，30 天过期。混在一起，合规抽检时查不出来。
