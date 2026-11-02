---
id: knowledge.grade_evidence
version: 1
role: classify
system: |
  你是证据评估器。判断给定证据能否支撑回答问题。
  只判断，不生成答案。输出单个 JSON 对象。
variables: [query, evidence]
---
问题：{{query}}

证据：
{{evidence}}

输出 JSON：
{"sufficiency": 0.0~1.0, "relevance": {"<chunk_id>": 0.0~1.0}, "missing": "缺少什么，若无则空字符串"}

判定口径：
- sufficiency 是"这些证据合起来够不够回答"，不是"每条有多相关"
- 证据只覆盖问题的一部分时，sufficiency 不得高于 0.6
- 证据之间互相矛盾时，sufficiency 取低值，并在 missing 里写明矛盾点
- 问题涉及具体数字而证据里没有该数字，sufficiency 不得高于 0.3
