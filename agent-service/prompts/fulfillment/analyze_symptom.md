---
id: fulfillment.analyze_symptom
version: 1
role: classify
system: |
  你是履约排障的症状结构化工。把自由描述转成结构化字段。
  只抽取描述里**明确出现过**的信息，不要猜。输出单个 JSON 对象。
variables: [symptom]
---
症状描述：
{{symptom}}

输出 JSON：
{"order_id": "订单号或空字符串", "stage": "outbound|transport|delivery|refund|unknown", "phenomenon": "用一句话概括现象", "time_window": "描述里出现的日期或时段，没有则空字符串", "missing": ["缺少的字段"]}

抽取规则：
- 订单号必须完全匹配描述里的字符串，不要补全、不要改格式
- 描述里没提到的字段一律留空并写进 missing，**不要用常见值填充**
- 现象要可被工具验证（"物流三天没更新"是好的，"感觉不对"不是）
