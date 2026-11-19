# Evidence-Gated Experiment Plan Comparison

一个可自动触发的 Agent Skill，用真实来源对齐多个版本化实验方案，做完整资源/成本/有限参数域审查，并给出可复核的选择，而非按模板生成三份“相似方案”。

## 交付物

- `SKILL.md`：触发方式、执行流程、路径、选择规则和最终自查。
- `references/methods.md`：七类实验字段、证据、成本/排期和版本语义。
- `references/review-template.md`：可复用的统一审查骨架（不是案例答案）。
- `references/recovery.md`：缺料、不可读、冲突、无合格候选和契约异常的恢复方式。
- `examples.md`：明确标为合成示例的输入、命令与示意输出。
- `scripts/source_audit.py`：文件索引/哈希、PDF 页文本提取和精确证据定位验证。
- `scripts/contract_audit.py`：动态原始 `output_contract` 骨架、原子 JSON Patch、变更记录、契约与 `case_id` 验证。
- `scripts/numeric_audit.py`：精确有理数算术、有限域联合检查、成本/任务图审计、硬门及显式目标比较。

所有脚本均为 UTF-8 文本，无符号链接或预提取论文/预置案例答案。没有网络数据、个人文件、隐蔽环境变量或手工操作依赖。

## 安装与调用

将本目录放在 Agent 的 Skill 目录。官方题面评测位置是 `/skills/weekly-skill`，`SKILL.md` frontmatter 中的 `name` 为 `weekly-skill`，不设置禁用自动调用的字段。描述同时包含科学任务与运行契约触发词；是否加载由宿主标准 Skill 发现机制决定。

要求 Python **3.9+**（仅标准库）。不需要 pip、 npm、网络访问、外部求解器或浮点优化器。

```bash
python3 --version
mkdir -p /app/work /app/output
python3 /skills/weekly-skill/scripts/contract_audit.py bootstrap \
  --case /input/case.json --result /app/output/result.json \
  --work-dir /app/work
python3 /skills/weekly-skill/scripts/source_audit.py index \
  --root /input --work-dir /app/work
```

如果原 `task.md` 而非 `case.json` 定义了字段，先将原字段忠实转录为 JSON 或 JSON Schema，再传：

```bash
python3 /skills/weekly-skill/scripts/contract_audit.py bootstrap \
  --case /input/case.json --contract /app/work/contract.json \
  --result /app/output/result.json --work-dir /app/work
```

使用外部 contract 时，后续的 `patch` 与 `validate` 也应传**同一** `--contract /app/work/contract.json`。脚本会保存其路径和 SHA-256；未再次指定时从原 bootstrap 状态恢复同一契约，改变契约必须使用新的结果与工作目录。

`bootstrap` 只能创建新的结果/状态，检测到已有结果或 checkpoint 时默认拒绝覆盖。恢复运行时先读已有结果继续修订，或另选全新的结果/工作路径，不用再次初始化清空先前工作。

契约校验器支持其上所述字段与常见 JSON Schema 约束（类型、必填、枚举、常量、数组/对象数量、字符串长度/模式、数值边界及选择分支）；遇到未支持的 Schema 关键词会明确报错而非静默判绿。它不替代完整官方 Schema 引擎，也不验证科学正确性。

若材料在评测环境之外，将三个参数 `--root`、`--case` 和 `--result` 换成真实路径；不保留开发机绝对路径。用 `python3 脚本 --help` 查看全部开关。

## PDF 与工具依赖

PDF 处理按顺序发现本机 `pdftotext`、已安装 `pypdf` 或已安装 `PyMuPDF`，并把**实际使用的后端、版本和文本层情况**写入 `/app/work/source-index.json`。脚本报“没有可用 PDF 解析器”是明确状态，不代表已经读取 PDF。

- 若环境提供渲染/OCR 工具，以资源清单中的实际路径、约束和调用说明使用；成功后再把页图/文本写入 `/app/work`。
- 没有文本层未渲染/OCR时，该页的证据保持未知。
- 大文件、超过文件数限制、不可读 JSON、加密 PDF 都显式记录，不静默截断后假装完整读取。

所有其它计算仅 Python 标准库；无需假设第三方包可用。

## 主要方法

1. **Source-bound**：输入与原文是事实；系统性检查相关方法段、表格和资源契约，不只看摘要。关键判断回指页/表/章节、短引文、JSON Pointer 和文件哈希。
2. **Aligned**：每个实际候选/版本都审查同一七字段：假设、变量、对照、步骤、参数、资源、判读标准。另横向比较目标、风险、成本、可行性和证据。
3. **Non-compensatory**：先检查安全/伦理、设备资源、预算、档期与有限参数域；硬门失败不能被低成本或加权高分抵消。未知、冲突、不适用、真实 `0/false`、不可行各自独立。
4. **Recomputable**：每个派生数字保留输入、量纲、公式与未舍入值。有限参数域做交集和联合检查，而不是填满连续区间或只取中点。枚举不完整时公开覆盖限制。
5. **Qualitative + explicit preference**：原始指标、逐对权衡/非支配关系和原任务的明确优先级共同支撑选型；不伪造等权偏好、概率、性能或实验成功。
6. **Version-aware**：字段变更传播到可行性、成本、排期及选择。不能把布局和措辞变化当成科学改动，不能因名为 v1/v2/v3 就捏造继承链。

方法对应的学术与设计依据见 `references/methods.md`。这属于研究辅助和审查，不证明实验即可实际操作，也不替代必要的安全、伦理、法律或领域专家确认。

## 输出

运行时按真实 `output_contract` 生成 `/app/output/result.json`，至少满足原字段及 `case_id`，包含实际的主结果、来源、状态、不确定性、版本/变更记录和必要的确认项。此正文不规定替代原始契约的固定字段名。

可在 `/app/work` 或契约允许的位置保存：

- `source-index.json`：实际材料登记和工具版本。
- `evidence-verification.json`：逐项定位/值相等与哈希验证；不等于证据强度判断。
- `numeric-input.json`、`numeric-report.json`：规范审查数据及计算覆盖/警告。
- `contract-state.json`：实际增量变更及前后结果哈希。
- `validation.json`：机械字段/Schema 校验结果；不等于科学有效性。

`partial` 允许在真实缺料/失败时表示有限完成度；必须列出限制。所有候选违约可形成有效的“无合格方案”分析，而不等于程序崩溃。不会仅因命令运行完成就标为科学审查完成。

## 最小调用示例

见 `examples.md`。其数据完全为合成数字，只演示接口与审计，不是声称平台三个正式案例的任何结果，也不代表真实科研选择或实测收益。

## 适用范围与限制

适合有若干候选方案、研究目标、来源文件及明确/半明确资源与选择约束的比较、选择、版本复盘和允许域内优化。不用于单纯文献综述、全文翻译、实验数据模型分析，亦不把全新科学设计冒充输入候选。

- 自然语言语义映射和相关性由 Agent 审查，脚本不能证明科学意义。
- 有限域只有完整枚举才能声称对全域成立。连续区间需要额外解析界或证实条件，不任意网格化成全局保证。
- 算术内核验证显式任务图，不声称能找到全局最优资源受限排期；未给出预约/资源窗口就不能证明实际档期。
- 契约类型不明确时保留诚实的部分结果，Agent 按原始说明认真解释；不会为 Schema 通过而造数。
- 平台每个案例预算为 64 模型请求、900 秒、每响应 16384 tokens（含 reasoning）。脚本将机械工作前移，主提示要求早保存、逐步校验。平台三次重复由评测环境自行执行，本 Skill 不会在一次案例中空耗三倍预算。
- 多候选次数约束不能依赖任意同分默认值；缺偏好时可给条件选择并清晰说明。

## 开发期自查

用临时合成输入检查正常的 Schema/引用/算术流程，同时检查重复键、缺失必需字段、空参数交集、单位/币种冲突、任务环、全不可行、材料变异和解析失败。不要把合成检查说成平台官方增益；上/无 Skill 的正式对照结果只由平台提供。
