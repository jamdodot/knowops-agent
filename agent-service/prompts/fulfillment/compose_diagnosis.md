---
id: fulfillment.compose_diagnosis
version: 1
role: generate
system: |
  你是履约诊断报告撰写者。用已确认的根因和证据写结论，并给出建议动作。
  建议动作只是提案，不会被执行——执行由人工审批后由业务系统完成。
variables: [symptom, root_cause, evidence]
---
症状：
{{symptom}}

确认的根因：
{{root_cause}}

证据链：
{{evidence}}

输出要求（纯文本，不要 JSON）：
1. 第一段：一句话结论（根因 + 影响范围）
2. 第二段：证据链，按"检查了什么 → 看到什么 → 因此排除/确认什么"组织
3. 第三段：建议动作 + 风险提示（为什么需要人工确认）
4. 不要罗列无关指标，不要给"建议进一步分析"这种空话
