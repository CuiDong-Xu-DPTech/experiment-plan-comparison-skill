# 最小调用示例（纯合成，非正式案例答案）

本例**只演示接口和可复核计算**。以下两个候选、价格、预算、步骤时长均为虚构；不是任何实验室、供应商、论文数据或评测的三个固定案例，也不用于声称选择真实科研路线。真实运行时须保留原案例的全部候选、七字段和原始输出契约。

## 1. 合成输入

目录中的 `/input` 只是示例路径，不是本仓库文件。创建 `/input/case.json` 和 `/app/work`：

```json
{
  "case_id": "synthetic-plan-comparison-demo",
  "planning_scenario": {
    "budget_cny": "17.20",
    "source_scope": "synthetic illustration only, not a quote or measured performance"
  },
  "candidate_plans": [
    {
      "plan_id": "alpha",
      "version": "v1",
      "n_domain": [2, 3],
      "kit_price_cny": "4.20",
      "fixed_cny": "0.30",
      "charge_events": 2,
      "steps": "prepare 2 h, then measure n h"
    },
    {
      "plan_id": "beta",
      "version": "v2",
      "n_domain": [2, 3],
      "kit_price_cny": "8.55",
      "fixed_cny": "0.30",
      "charge_events": 2,
      "steps": "prepare 2 h, then measure n h"
    }
  ],
  "explicit_objective": "Among fully feasible parameter combinations, minimize cost_total",
  "synthetic_joint_limit": "For this arithmetic illustration only, two times n must be at most eight",
  "output_contract": {
    "type": "object",
    "properties": {
      "case_id": {"type": "string"},
      "status": {"type": "string", "enum": ["partial", "complete"]},
      "selected_plan": {"type": ["object", "null"]},
      "comparison": {"type": "array"},
      "sources": {"type": "array"},
      "uncertainties": {"type": "array"},
      "change_records": {"type": "array"}
    },
    "required": ["case_id", "status", "selected_plan", "comparison", "sources", "uncertainties", "change_records"],
    "additionalProperties": false
  }
}
```

`/input/protocol.md` 的合成原文：

```text
SYNTHETIC EXAMPLE, not a source of scientific fact.
The scenario only compares two invented candidates against the stated budget.
Kit quantity matches its price charging unit; every candidate incurs two charge events.
Prepare for 2 hours, then measure for n hours.
The two branches may run until all predecessor dependencies are satisfied.
This is not a booking confirmation or demonstration of actual resource availability.
```

先读原始契约，保存不虚构数值的部分结果，再索引实际材料：

```bash
SKILL_DIR=/skills/weekly-skill
mkdir -p /app/work /app/output
python3 "$SKILL_DIR/scripts/contract_audit.py" bootstrap \
  --case /input/case.json --result /app/output/result.json \
  --work-dir /app/work
python3 "$SKILL_DIR/scripts/source_audit.py" index \
  --root /input --work-dir /app/work
```

## 2. 示例的证据定位

从 `/app/work/source-index.json` 读该协议的真实 `source_id`，不要假定排序号；然后把下面的实际 ID 写入 `/app/work/evidence-locators.json`：

```json
[
  {
    "claim_id": "synthetic-charge-events",
    "source_id": "用实际 source_id 替换",
    "kind": "text_quote",
    "exact": "Kit quantity matches its price charging unit; every candidate incurs two charge events."
  }
]
```

```bash
python3 "$SKILL_DIR/scripts/source_audit.py" verify \
  --index /app/work/source-index.json \
  --claims /app/work/evidence-locators.json \
  --output /app/work/evidence-verification.json
```

`locator_verified: true` 只说明那本合成文本中包含原话，**不证明这些内容在真实世界成立**。

## 3. 规范数值输入

以下为完整 `numeric_audit.py` 输入形状；其它字段的来源指针应指向实际输入文件的 JSON Pointer。没有显示的科学七字段在真实任务中不能省略。保持 `Decimal`/`Fraction` 兼容的精确输入，不用浮点近似。

```json
{
  "schema_version": "numeric-audit/v1",
  "plans": [
    {
      "plan_id": "alpha",
      "version": "v1",
      "parameters": [
        {"name": "n", "unit": "samples", "domain": [2, 3], "allowed_domains": [{"constraint_id": "synthetic-n-domain", "values": [2, 3], "source_ref": "source-case#/candidate_plans/0/n_domain"}]}
      ],
      "joint_constraints": [{"constraint_id": "synthetic-work-limit", "expr": "n * 2 <= 8", "source_ref": "source-case#/synthetic_joint_limit"}],
      "cost_items": [
        {"item_id": "kits", "category": "consumables", "quantity": "n", "unit_price": "4.20", "events": 2, "fixed_cost": "0.30", "currency": "CNY", "quantity_unit": "kit", "price_unit": "kit", "recurrence": "recurring", "source_ref": "source-case#/candidate_plans/0"}
      ],
      "schedule": {
        "tasks": [
          {"task_id": "prepare", "duration_hours": 2, "predecessors": [], "person_hours": "duration_hours"},
          {"task_id": "measure", "duration_hours": "n", "predecessors": ["prepare"], "person_hours": "2 * duration_hours", "machine_hours": "duration_hours"}
        ],
        "resource_contention_known": false
      },
      "hard_constraints": [{"constraint_id": "budget", "expr": "cost_total <= 17.2", "source_ref": "source-case#/planning_scenario/budget_cny"}],
      "soft_metrics": [{"metric_id": "money", "expr": "cost_total", "direction": "min"}],
      "preference": {"priorities": [{"metric_id": "money", "priority": 1, "direction": "min"}]}
    },
    {
      "plan_id": "beta",
      "version": "v2",
      "parameters": [
        {"name": "n", "unit": "samples", "domain": [2, 3], "allowed_domains": [{"constraint_id": "synthetic-n-domain", "values": [2, 3], "source_ref": "source-case#/candidate_plans/1/n_domain"}]}
      ],
      "joint_constraints": [{"constraint_id": "synthetic-work-limit", "expr": "n * 2 <= 8", "source_ref": "source-case#/synthetic_joint_limit"}],
      "cost_items": [
        {"item_id": "kits", "category": "consumables", "quantity": "n", "unit_price": "8.55", "events": 2, "fixed_cost": "0.30", "currency": "CNY", "quantity_unit": "kit", "price_unit": "kit", "recurrence": "recurring", "source_ref": "source-case#/candidate_plans/1"}
      ],
      "schedule": {
        "tasks": [
          {"task_id": "prepare", "duration_hours": 2, "predecessors": [], "person_hours": "duration_hours"},
          {"task_id": "measure", "duration_hours": "n", "predecessors": ["prepare"], "person_hours": "2 * duration_hours", "machine_hours": "duration_hours"}
        ],
        "resource_contention_known": false
      },
      "hard_constraints": [{"constraint_id": "budget", "expr": "cost_total <= 17.2", "source_ref": "source-case#/planning_scenario/budget_cny"}],
      "soft_metrics": [{"metric_id": "money", "expr": "cost_total", "direction": "min"}],
      "preference": {"priorities": [{"metric_id": "money", "priority": 1, "direction": "min"}]}
    }
  ]
}
```

来源字段中的 `source-case` 是本例文档里的引用标识，运行时应换成从该 case 的实际 `source-index.json` 读出的 ID。`synthetic_joint_limit` 已包含在上述合成 case 中，是可核对的虚构情景条件。使用时须确保证据引用的来源真实存在，不能伪称键或文件已存在。科学审查比这个最小数值演示更广。

```bash
python3 "$SKILL_DIR/scripts/numeric_audit.py" \
  /app/work/numeric-input.json --output /app/work/numeric-report.json
```

## 4. 期望的机械结果

- `alpha` 两种候选组合均过联合条件 `2*n <= 8`。`n=2` 的成本是 `2 * 2 * 4.20 + 0.30 = 17.10 CNY`，满足 `17.20` 预算；`n=3` 成本 `25.80`，不满足。
- `alpha/n=2` 的依赖图最早完成时间是 `2+2=4 h`，人工量是 `2+2*2=6 person-h`，机器占用 `2 machine-h`。这些仅是合成 DAG 演算，**不是预约或资源并行证明**。
- `beta` 在 `n=2` 已为 `34.50 CNY`，两种组合都违反预算；保持淘汰记录。
- 由于本例显式声明 `cost_total` 最小为唯一优先目标，`selection.unique_winner` 是 `alpha/n=2`，不是任意按字母排序。

正式审查不可把费用低或预算通过自动解释成科学性能更好；真实任务必须检查原论文、版本、七字段和所有硬约束。

## 5. 增量结果与校验

下面是示意输出片段，省略了真实案例应有的完整七字段矩阵/确认项。实际生成时同字段需写到原始 `output_contract`，以下 Fit/alpha 只在合成示例中成立。

```json
{
  "case_id": "synthetic-plan-comparison-demo",
  "status": "complete",
  "selected_plan": {
    "plan_id": "alpha",
    "version": "v1",
    "parameters": {"n": 2},
    "cost_cny": "17.10",
    "reason": "唯一通过合成预算硬门并由显式成本优先级选出的虚构候选"
  },
  "comparison": [
    {"plan_id": "alpha", "version": "v1", "status": "feasible", "fully_admissible_scenarios": 1},
    {"plan_id": "beta", "version": "v2", "status": "infeasible", "fully_admissible_scenarios": 0}
  ],
  "sources": [{"source_id": "实际索引ID", "scope": "synthetic example"}],
  "uncertainties": ["仅为合成DAG；不构成资源档期、可执行性、性能或真实科学选择证明"],
  "change_records": ["实际已生成的变更记录，未执行 patch 时不捏造此项"]
}
```

把语义修改写进 RFC 6902 JSON Patch 文件（数组或含 `patch/reason/source_refs` 的对象），执行：

```bash
python3 "$SKILL_DIR/scripts/contract_audit.py" patch \
  --case /input/case.json --result /app/output/result.json \
  --work-dir /app/work --patch /app/work/change-001.json \
  --reason "从合成的证据、数值审查结果填入选型" \
  --source-refs source-0001
```

最后用**原始案例与原始契约**验证；返回0只代表机械校验通过：

```bash
python3 "$SKILL_DIR/scripts/contract_audit.py" validate \
  --case /input/case.json --result /app/output/result.json \
  --work-dir /app/work
```

状态、来源、不确定性、变更记录均保留在原始契约里；如果原字段不允许某个说明，按契约保存侧车而不是增加伪字段。更多合成失败例子见 `references/recovery.md`。
