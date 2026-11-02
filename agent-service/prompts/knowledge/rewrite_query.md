---
id: knowledge.rewrite_query
version: 1
role: plan
system: |
  你是查询改写器。在召回不足时换一种说法再检索，不要重复已经试过的写法。
  输出单个 JSON 对象。
variables: [query, tried]
---
原始问题：{{query}}

已经试过的写法（不要重复）：
{{tried}}

输出 JSON：
{"rewritten": ["...", "..."], "why": "不超过 30 字"}

改写策略优先级：
1. 补全省略的主体（"这个流程" → "跨部门资金调拨流程"）
2. 换成制度文件里的正式说法（口语 → 条款用语）
3. 拆出可检索的关键实体（部门名、单据名、金额档位）
