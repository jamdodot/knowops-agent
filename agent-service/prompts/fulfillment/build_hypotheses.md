---
id: fulfillment.build_hypotheses
version: 2
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

可用取证工具（checks 只能填这些名字，原样照抄，不要翻译、不要自创）：
- lookup_order         订单快照：状态、出库时间、承诺时效、仓库、承运商
- inventory_snapshot   库存分配快照：需求量、可用量。可用量 < 需求量即分配失败
- shipment_track       物流轨迹：节点与事件文本，含地址异常、交通管制等判断依据
- payment_flow         支付流水：状态、失败码。failed 时订单不会进入履约链路
- search_op_logs       操作日志：滞留告警、出库复核、地址校验、重复扣减等
- check_rule           规则原文。判断"是不是规则误判"之前必须先查，不许凭印象

输出 JSON：
{"hypotheses": [{"id": "h1", "root_cause": "简短根因名", "prior": 0.0~1.0, "checks": ["lookup_order", "shipment_track"]}]}

约束：
- prior 之和不必等于 1，但要能排序；平局说明假设没区分度，请重写
- checks 只能填上面六个工具名之一，且必须与该假设**真正相关**——
  四个假设都填同一组工具，等于没做区分，评测时会被判为无区分度
- 最多 4 个：超过 4 个意味着没有在做假设收敛，而是在穷举
- 历史工单只提供先验权重，不允许直接照抄它的根因当作本次结论
- 根因名要能区分这几类：承运商滞留 / 库存不足 / 地址异常 / 支付失败 / 重复出库 / 规则误判
