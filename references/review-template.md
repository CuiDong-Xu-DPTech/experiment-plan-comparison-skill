# 通用审查骨架

这是运行时的**内部中间产物模板**，不是替代原始 `output_contract` 的最终答案，也不是预制推荐。每个字段必须依据实际案例填写。不要输出未替换的 `TODO`，不要为了填满而使用无依据默认值。

## A. 证据索引与定位

`/app/work/evidence-locators.json` 为数组，每项形式：

```json
{
  "claim_id": "由实际字段生成的稳定ID",
  "source_id": "source-index.json 中实际存在的 ID",
  "kind": "page_quote 或 text_quote 或 json_pointer",
  "page": "PDF物理页码；纯文本删除此键",
  "exact": "从实际提取文本复制的连续短原文；json_pointer时删除此键",
  "pointer": "如 /plans/0/budget；仅json_pointer时使用",
  "expected_value": "该路径下的实际值；仅json_pointer时使用"
}
```

source-index 能验证字节和定位，Agent 另外填写：

| 字段 | 必填内容 |
|---|---|
| claim/field | candidate_id、version、稳定字段路径、原始值/单位 |
| 关系 | supports / contradicts / context_only |
| 信息状态 | reported / derived / assumed / inferred / unknown / conflict / not_applicable |
| 适用条件 | 实验体系、对象、时间点、地点/情景，不可省略决定适用性的条件 |
| 核验 | 文本提取或实际渲染/OCR，提取工具/版本，独立打开情况 |
| 未知与反证 | 缺失、冲突、需要何种确认；缺失并不可视为 0 或否定 |

## B. 每个候选版本的同构审查

三个及任意更多实际候选每个复制同一结构；没有某字段时填 `unknown` 和原因，不能删除关键栏或填伪省缺值。

```json
{
  "plan_id": "实际候选ID",
  "version": "实际版本",
  "identity_source_refs": [],
  "problem_and_objective": {
    "observed": "研究问题、目标终点、成功尺度和推断范围",
    "source_refs": []
  },
  "seven_fields": {
    "hypotheses": [],
    "variables": [],
    "controls": [],
    "steps": [],
    "parameters": [],
    "resources": [],
    "interpretation_criteria": []
  },
  "cross_cutting_dimensions": {
    "objective_alignment": [],
    "cost_ledger": [],
    "time_ledger": [],
    "feasibility": [],
    "evidence_strength": [],
    "risks_and_contingencies": [],
    "confirmations": []
  },
  "eligibility": {
    "status": "feasible 或 infeasible 或 undetermined",
    "hard_checks": [],
    "unknowns": []
  }
}
```

七类数组中的每条至少含：

```json
{
  "item_id": "输入已有稳定ID或明确记录的运行内新建ID",
  "observation": "来源明示的原始字段、值、单位和条件",
  "normalization": "仅改名/单位换算/重排，给规则与来源，否则写未作归一化",
  "evaluation": "对目标/约束的判断，与观察分列",
  "information_state": "reported/derived/assumed/inferred/unknown/conflict/not_applicable",
  "source_refs": ["具体 source_id#定位"],
  "uncertainty": "缺失、冲突、外推、覆盖不全或没有遗漏该字段的说明"
}
```

## C. 一张真正同口径的比较矩阵

所有版本同列、同单位、同目标判断；每格都带原始值而非只有评分。外层可读汇总使用以下表头，字段路径与实际契约映射。

| 比较口径 | 实际候选A/版本 | 实际候选B/版本 | 实际候选C/版本 | 关键差异、可比性和证据 |
|---|---|---|---|---|
| 目标及成功判读 | 同一目标与口径下写实际内容 | 同口径 | 同口径 | 定位到 claim |
| 核心假设 | 各版本实际内容 | … | … | 明示未知和前提 |
| 变量和实验单位 | 同一字段，含重复层级 | … | … | 不能以单位混淆假对齐 |
| 对照与偏倚控制 | 类型、用途、适用性 | … | … | 不同目标效应标不可比 |
| 执行步骤 | 依赖/批次/质控与完成标准 | … | … | 不全默认皆执行 |
| 有限参数/代表组合 | 原生定义域和实际通过元组 | … | … | 联合验证，未全枚举声明 |
| 资源及可行性 | 设备/物料/技能/预约 | … | … | 区分冲突和未知 |
| 成本 | 总额、币种、单位、计费期 | … | … | 来源、公式、覆盖范围 |
| 机时、人时、日历时长 | 各自独立值 | … | … | 等待/排队/依赖/货期 |
| 判读标准 | 指标/方向/阈值/不确定规则 | … | … | 阈值来源与适用范围 |
| 风险与回退 | 触发→后果→缓解→残余→回退 | … | … | 不能用笼统风险等级 |
| 证据 | 具体支持与反证，直接性 | … | … | 并非文献数量或自制置信度 |
| 安全、伦理、设备、专家确认 | 实际必需项及状态 | … | … | 未确认不冒充已批准 |
| 选择及依据 | 版本、原始终值、验证通过元组和理由 | … | … | 不同选择路径条件化 |

## D. 选择、敏感性和变更

| 条目 | 内容 |
|---|---|
| 入选/不入 | 原候选/版本的明确身份，允许“无合格候选” |
| 代表参数/排期 | 真实完成了哪些检查及哪些未检查 |
| 未选原因 | 每个未选者相对入选者的可核验差异；不能只有“分数低” |
| 条件改选 | 有依据的集合/情景与重新计算的选择；范围不当作概率 |
| 版本变化 | 源版本、目标版本、字段、before/after、来源、影响层级 |
| 待确认项 | 安全、伦理、设备可用及专家确认，各自负责人是否已指定 |
| 限制 | 覆盖不足、缺资料、来源冲突、工具失败；各自状态如何影响结论 |

实际 JSON Patch 变更记录由 `contract_audit.py` 在 `/app/work/contract-state.json` 保存；另行记录字段级科学/语义变化即可。不能伪造未由脚本产生的 transaction/hash 或声称脚本报错也已通过。

## E. 数字输入生成纪律

`numeric-input.json` 的结构支持集合、公式、真实费用行、硬约束和软目标。从读得的原始语义映射到这一结构；精确输入字段以 `scripts/numeric_audit.py` 顶部说明和 `--help` 为准。

- 只填来源支持的值；不能代入课程标准价、概率、设备产能或经验时长。
- 在默认先做基础审查的情况下，若明确要求优化，优化值仍属于允许域内建议，必须与原始输入分开。
- 不能用自然语言绕过算术；用脚本支持的安全表达式、精确十进制值和实际单位。
- 输入无真实软目标/优先级时，不要求脚本创造单选；科学证据的定性比较另外说明。
