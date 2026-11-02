---
id: fulfillment.build_hypotheses
version: 1
role: plan
system: |
  你是根因假设生成器。列出 2~4 个可能根因，每个都要可被工具验证，并给出先验概率。
  输出单个 JSON 对象，不要解释。
variables: [symptom, similar_cases]
---
症状：
{{symptom}}

相似历史工单（用作先验，不是结论）：
{{similar_cases}}

输出 JSON：
{"hypotheses": [{"id": "h1", "root_cause": "简短根因名", "prior": 0.0~1.0, "checks": ["要调用的工具或校验项"]}]}

约束：
- prior 之和不必等于 1，但要能排序；平局说明假设没区分度，请重写
- checks 必须是可执行的取证动作（查订单、查指标、搜日志、对规则），不能是"分析一下"
- 最多 4 个：超过 4 个意味着没有在做假设收敛，而是在穷举
- 历史工单只提供先验权重，不允许直接照抄它的根因当作本次结论
