---
name: weekly-skill
description: >-
  从真实论文、原始资料和明确契约审查、比较并选择科研实验方案；核对多候选/版本化方案的假设、变量、对照、步骤、有限参数域、设备耗材、成本机时、排期、风险与判读标准，给出证据定位、逐项算术、可行性、敏感性、入选及舍弃理由。遇到实验方案比较/选型、资源或预算约束、版本变化、/input/case.json、output_contract 或需形成可复核 /app/output/result.json 时使用。不用于单纯文献摘要、全文翻译、无候选比较目标的问答或实验数据统计建模。
---

# 实验方案比较与选择

让**所有真实候选在同一证据矩阵上，通过硬约束，再做可辩护选择**。不编造数据、来源、审批、未来性能或运行历史，不把新增理想设计冒充原候选。真实任务、原始文件和原始 `output_contract` 优先于本流程。

## 1. 先存契约骨架

读实际 `/input/case.json`、本例 `task.md` 和资源清单，记录原始 `case_id`、输出字段/层级/类型/枚举与每个实际候选 ID/版本。所有真实版本都要审查；无证据时不要假定三条平行路线或 v1→v2→v3 修订链，不自行造三个方案。

先在新 `/app/work` 与 `/app/output`（或原任务指定目录）落一份真实的 partial/unknown 结果；已有结果读取 checkpoint，不能重复 bootstrap 覆盖。所有原契约要求的判断最终都写入 `/app/output/result.json`，不能只在侧车文件里。契约禁止附加字段时不要塞 `_meta`。

```bash
SKILL_DIR=/skills/weekly-skill
mkdir -p /app/work /app/output
python3 "$SKILL_DIR/scripts/contract_audit.py" bootstrap \
  --case /input/case.json --result /app/output/result.json \
  --work-dir /app/work
python3 "$SKILL_DIR/scripts/source_audit.py" index \
  --root /input --work-dir /app/work
```

外部契约在所有命令里使用**同一个** `--contract <path>`，其路径、哈希与原始契约一致。未知值不是 `0`、空串或首枚举；必需数值没有诚实默认时，按真实任务补齐并标出限制作业。说明性 `constraint/note/required_content` 仍需人工满足，不能拿机械校验通过代替科学完成。

**先做第一小组事实投影，再做深入分析。** 对照实际结构，优先投影真实候选 ID/version 和对应原始事实到已存在的比较字段（可用 `/candidates` 对应实际数组型比较字段；其它形状则逐指针处理），生成初版 JSON Patch 并立即写回。不要复制整个 case、猜别名、把不同形状强行合并，也不要截至目前还不清楚的资源/确认项填默认值。投影只表示待审事实，不是已经科学完成；类型或语义不明就先保留 partial/unknown，不强行映射。详见第三节和模板中的投影规则，避免到数值门结束或最后几分钟才复制几十条任务/费用行。

已有检查点只用 JSON Patch 增量更新，失败时保留旧结果：

```bash
python3 "$SKILL_DIR/scripts/contract_audit.py" patch \
  --case /input/case.json --result /app/output/result.json \
  --work-dir /app/work --patch /app/work/change-001.json \
  --reason "只写入已完成且有来源的核验" --source-refs <实际ID>
```

## 2. 围绕选择核查证据

先提取研究目标、硬约束、真实优先级和候选身份，再读**影响这些判断**的原始方法、对照、参数、表格、图注和局限，而非先做泛读/长综述。只核验关键依据，不把“引文存在”当成“引文支持该判断”。

- 为关键 claim 暂存源 ID、物理页/表/图/JSON Pointer、原文或数值、适用范围、证据关系、计算链和未知。区分 `reported`、`proposed/prospective`、`derived`、`inferred`、`unknown/conflict`、`not_applicable`。文献的旧观测、拟议场景、机械计算与个人判断不可混写。对决定推断类别、方法有效性或关键选择取舍的科学主张，保留已实际核对的短原文/图中标签，不能只写转述和页码；资源数值用原值、公式与定位即可，拒绝为了给齐引文而从缺失 SI 或记忆补造句子。
- 遇到低文本、乱码、表图数值或图轴差异，**真实渲染相关页**并查看；没验证就不要声称读过。缺 SI、不可读文件、未知谱系/矩阵/预约都如实保留，不凭文件名或记忆补全。
- 遇到来源自身矛盾，保留表头与原值，并明确各自的公式/减法。不得静默统一数字、翻转符号或挑选一种说法。哈希只辨识字节一致，不证明支持。

关键引文用 `kind`、`source_id`、physical page（若适用）、`exact` 或 JSON Pointer/expected value 形成 `work/evidence-locators.json`，真实执行以下核验；`exact` 必须连续一致，不能只凭记忆或相似措辞。转述标为转述，不能冒充逐字引文。逐条看失败和歧义，不以 locator count 代替证据强度。

```bash
python3 "$SKILL_DIR/scripts/source_audit.py" verify \
  --index /app/work/source-index.json \
  --claims /app/work/evidence-locators.json \
  --output /app/work/evidence-verification.json
```

期限、硬门、校准/定量验收规则也应进入这些 claims，逐项用真实 JSON Pointer 与 expected value 核对路径、层级和值类别，不能凭印象简写父路径。任何找不到的指针、值不符或引文不匹配都应保留为未核实，不能称已核来源。材料给出校准点、LOQ/LOD、范围或判无效规则时，逐项保留数值、单位、适用条件及后果，不只写“有校准/有验收”，更不能删界外样本迎合结果。

每个版本都按同一七字段核查：假设、变量、对照、步骤、参数、资源、判读标准。特别保留：

- **独立实验单位 vs 技术重复 vs 环境/处理分配单位**。单个环境回路、同一合成批、同溶剂区块、更多动物或更多曲线，不自动等于环境重复或去混杂因果证据；注明亲子/谱系与对比层级。
- 目标、风险、成本、全程工期、可行性与证据直接性。对照能否排除替代解释、关键依赖/QC/停止路径，以及未知的 SI/审批/资源。
- 每项均为“原文事实、前瞻情景约定、机械派生、评价推断或未知/冲突”之一，不因排版整齐伪造可比。

风险写“触发→后果→缓解→残余风险→回退/确认”，不能只写“有风险”。只写该候选真实存在的材料、步骤和读数所对应的风险；不存在的路径标 N/A 或省略，不硬套模板。若候选的研究问题不同，不强行排名为同一科学终点。

## 3. 先算账、过硬门，再扩展说明

读 [numeric-input-guide](references/numeric-input-guide.md)，而不是先读数百行 parser。把已核实的输入转换成规范审查单，运行：

```bash
python3 "$SKILL_DIR/scripts/numeric_audit.py" \
  /app/work/numeric-input.json --output /app/work/numeric-report.json
python3 "$SKILL_DIR/scripts/summary_audit.py" \
  /app/work/numeric-report.json --output /app/work/numeric-summary.json
```

逐行核对来源、币种、单位、参数元组、联合约束和硬门：

- **费用**：事件数 × 数量 × 合同单价 + 显式固定项，并分清一次性/重复计费、共享/批次、人工/机器/CPU。缺项和已知小计只能标 unknown/partial，不能当全成本零。共享、分摊、返工及概率必须有依据。
- **有限域**：先取交集，再完整检查联合元组；不取并集、不取中点、不把离散值补成连续区间或抽样当全域。被截断即报告覆盖不全。
- **日程**：设备机时、人员工时、日历工期与依赖分开。串行相加；有明确无资源争用才可按 DAG 并行。其它并发/可用时段不明的 earliest-time 最多是下界，不能当作 deadline 保证。**人时不等于日历缩短所需时间**，不把任务标签当实际预约。同日截止也须核实际开工时刻（例如 READ=8h、REPORT=5h，18:00 截止则最迟 05:00 顺序开始；这不等于已获准早班）。**队列获得资格≠测量/报告已完成**：须把后续 READ、REPORT、排队、工作日切换、实际开始时刻、许可班次与剩余耗时列入完整路径；若尚未证实实际 slot 和合法开工，不能把资格日写成 earliest full completion。额外逐 session 检查峰值占用与每日/班次上限，例如单次14小时作业超过8小时上限时即保留对应原候选的日程不合格，不得用总人时/总机时盈余或擅自拆班绕过。核查到窗口内的最后一个工作日及后续离线分析/审查仍未解决时，标出排期未建模或不合规，而不是引用引擎整体的 `feasible` 结束判断。
- **受限安全求值**：仅使用允许的表达式，含 `ceil/floor` 和有界 `min/max`，精确有理数进位。未知绝不改成零。跨币种、不同单位、负工时、算术/类型错误与科学不可行分别记录。

先看 `numeric-summary.json` 的版本、状态、coverage、失败与未知源，然后**只看相关计划的原文或数值明细**。保留全报告在 work 供复核，但不反复把巨型报告读回模型、重复拼巨型结果。

**此时立即形成第一版核心结论，不等待所有旁支证据完备。** 按实际契约，把候选身份/版本、原始目标、已核成本和日程、关键公式、硬门失败及 `unknown` 状态写进比较矩阵与相应结果字段。条件选择尚缺依据就诚实标 `undetermined`，之后再更新，不能因等待“更完整的所有字段”而让结果长期为空。优先用小批 JSON Patch 直接提交已审查字段；涉及相同 candidate/profile 的对象保持同一结构，不为了省字数漏掉任何真实候选或参数组合，不固定组合数。

大块同构字段、步骤、任务和费用行不要反复手写。已核实其含义与目标路径后，用明确指针制作 `/app/work/projection-map.json`，运行 `python3 "$SKILL_DIR/scripts/source_audit.py" project --case /input/case.json --map /app/work/projection-map.json --output /app/work/projection.json`，再用前一节命令对 `projection.json` 中 `patch` 做原子更新。每项只能指定实际存在的结果路径、真实源指针，以及必要且同构的 `select` 字段；不猜源/目标别名，不把原始源选择当成科学选择。更多规则见 [模板](references/review-template.md) 的原始字段投影。没有明确语义或会被嵌套异构对象缺失拦住的映射不要强行简写，宁可逐项核实。所有真实候选、QC 和原契约要求的行仍要保留；汇总共享事实时可直接用 result 内部路径引用，避免重叠栏位重复抄三遍，不能用 work 侧车替代正文。

## 4. 按硬门作条件选择

顺序固定：**评估可行性 → 用真实同单位指标/Pareto → 使用案例明示优先级或效用。**

- `feasible` 仅指已评估的硬门能满足；`infeasible` 是已证冲突；`undetermined` 是资料不足；工具错误另列。全不合格时可有效选择“无”，不能为凑胜者放松约束，但未知不自动等于不可行。
- **规划资格≠执行就绪**。若原任务允许，可选条件式可辩护方案，保留方法、审批、设备、专家和未定价排除项。不能把条件选择讲成真预约、已批准或可立即实施。
- **窄目标优胜≠原完整目标优胜**。若某选择只对省略某个终点或来源/处理范围的问题成立，明示完整目标、实际采用的范围、失去的信息和条件改选路径；把 programme/scientific owner 的范围/选择规则签署列为条件。任务不允许缩窄时返回 `null` 或对应的无可行选择，不能称其为原完整目标的最佳方案。
- 没有明示偏好，不发明权重/总分。纵使仅一个 Pareto 非劣项，也要确认它在明确的科学目标下有辩护依据，而非最低价默认。每位落选者必须给出具体硬失败、证据缺口或真实取舍，不能只写“分数略低”。
- 敏感性仅用有依据的有限域/场景重算，记录何种条件会改选及理由。不得杜撰百分比扰动、概率、成功率或功效。安全、伦理和资源硬门不被低成本抵消。
- 版本字段用手 `(candidate_id, version)` + field path + before/after + source 记录。参数、资源、日历或硬门改变后，联动重算域、成本、排期、风险、资格与选择；新增 QC 超预算/超期就保留失败，不删除必要步骤来救选。无证据时不能凭 v1/v2/v3 编造继承关系。

## 5. 先交付可核结果，按需精修

保留最后 **15–25% 时间**验收和修正关键失误：单次 ≤64 模型请求、900 秒、单响应 16384 tokens（含推理）。应按实际工作记录预算，而非声称达标。把 3 个左右明显影响进入执行的问题放在最前，不求先写出全部可能的旁支细节。

初始化时记录开始时间，可用 `contract-state.json` 的实际创建时刻计时。**到约 60–70% 预算时，先交付有真实内容的核心正文**，至少覆盖所有真实候选/版本、原始目标、关键七类字段、关键算术/公式、已证硬门失败、条件选择或合理的 undetermined，以及当前未知。绝不能到时限末才开始组装正文，更不能只有工具报告和空 result。

按以下顺序补齐而非无限扩写：

1. 所有原字段、实际版本、七类实验字段和横向的目标/成本/日程/风险/证据/确认项/变更记录。
2. 每行的输入、单位、公式、取整与真实聚合，硬门和入选的完整对应关系。
3. 决策关键证据的实际页码/定位、适用条件与冲突项；来源定位与实际结论相匹配，未知关系和未确认的报价/预约保持 unknown。
4. 选择/无合格候选与当前硬门、未知和真实准备状态一致。缺项写 unknown/partial 并给补救，不把占位字符串当科学答案。

保持真实、同构、可复算的结构。优先把每个已成形字段以较小 JSON Patch 直接增量写入结果，失败时仍以原 checkpoint 为准；确需脚本时才从同构字段快照聚合 JSON。不要先写一遍大型说明，再原样抄成补丁，也不要在一个长回合同时排演全部字段和巨型生成器。**共享来源引用与台账，避免重复叙述**，但不省略原契约所要求的成本/QC 明细、候选版本或最终正文字段，不能用 work 侧车代替。

**实质完成检查不能被 `status` 或字段存在替代。** 对每个真实候选/组合逐项检查七类内容；空数组/空对象处必须有明确的实际 N/A 或 unknown 原因、影响与后续确认，不得默认留白。风险缺少 trigger→consequence→mitigation→residual→fallback 时必须补实，不能把总风险词当成候选分析。原始未修改的变更记录可为明确的 `not_applicable`，真正计划为零的事件要保留其零与单位。

`complete` 仅用于已实质完成的比较；`conditional` 是在原任务允许时，数据/选择完整但执行未就绪的结论。之后没有足够信息支持候选比较或条件选择时，保留 partial，并说明缺口和所需证据，不把占位字段或程序化验证绿灯冒充完整科研交付。

只有原任务要求时才制作 Office/幻灯片/可编辑图与预览，并真实渲染查看 PDF/PNG。最后用真实路径验证：

```bash
python3 "$SKILL_DIR/scripts/contract_audit.py" validate \
  --case /input/case.json --result /app/output/result.json \
  --work-dir /app/work --format brief
```

完整 `validation.json`、规范契约、语义要求、原数值报告仍留作审计；brief 只减轻 Agent 阅读负担，不能隐藏错误或说语义已核完。出现损坏 PDF、冲突、缺料、枚举超限、全不可行或工具失败时，读 [恢复规则](references/recovery.md)，先保留已有可用部分，区分材料、科学、工具错误。只有遇到对应问题才加载 [methods.md](references/methods.md) 和 [review-template.md](references/review-template.md)。

不要捏造平台增益、预算达标、已装软件或运行记录。平台负责正式对照和三重复跑；本 Skill 不在单个案例中自行跑三遍或补造正式答案。
