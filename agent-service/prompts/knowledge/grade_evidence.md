---
id: knowledge.grade_evidence
version: 2
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
{"verdict": "sufficient | partial | insufficient", "relevance": {"<chunk_id>": 0.0~1.0}, "missing": "缺少什么，若无则空字符串"}

判定口径（**先定档，不要给连续小数**）：
- sufficient：证据能直接覆盖问题的全部要点——涉及金额/时限/审批主体时，这些数字与主体都在证据里
- partial：只覆盖部分要点，或缺少关键数字、缺少其中一个审批层级
- insufficient：证据与问题无关，或只有标题没有正文

为什么必须分档而不是打 0~1 的连续分：
同一份证据，模型每次给的连续分数会在 0.6~0.95 之间随机浮动，而判定阈值（0.75）正好
落在浮动带中间——于是同一个问题跑两次会一次作答、一次拒答。这是**判定不稳定**，
不是模型能力不足。三档把"差不多够"与"确实不够"分开，边界只出现一次，波动没有落脚点。
