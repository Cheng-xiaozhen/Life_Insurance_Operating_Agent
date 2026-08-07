---
name: monthly-performance-analysis-template-v2
description: 基于结构化数据规划并生成可审计、模板驱动的中国保险业绩分析。当 Codex 需要根据问题匹配可复用业务场景、选择候选指标、把用户意图转换为有序的汇总或分类步骤、执行确定性 CSV 计算、组合标准月度报告，或维护场景库时使用。
---

# 月度业绩分析模板 V2

使用场景库把用户意图转换为声明式分析计划，再由 Python 计算、Jinja 渲染。

## 工作流程

1. 根据候选场景文件中的 `scene.match` 匹配用户问题。
2. 读取场景引用的指标目录，只向用户提供其中声明的候选指标和可用操作。
3. 将需求转换为有序的 `steps` 计划：`summarize` 用于汇总全系统指标，`classify` 用于按阈值生成机构名单；区间、AND/OR 和缺失值规则使用 band 的 `conditions`。
4. 通过增加、删除或调整步骤对象改变分析内容，不把计算逻辑写入提示词或模板。
5. 将逻辑数据集 ID 绑定到具体 CSV 路径，并收集配置声明的参数。
6. 使用标准场景模式或标准报告模式运行 `scripts/render_scene.py`。
7. 返回渲染后的 Markdown，并保留上下文 JSON 作为审计依据。

运行标准报告：

```powershell
python scripts/render_scene.py `
  --report assets/reports/monthly-performance/report.yaml `
  --dataset standard_premium=../docs/标保.csv `
  --dataset value=../docs/价值.csv `
  --dataset active_manpower=../docs/活动人力.csv `
  --dataset sunshine_manpower=../docs/阳光人力.csv `
  --dataset supervisor_activity=../docs/主管活动.csv `
  --dataset supervisor_double_star=../docs/主管双星.csv `
  --dataset standard_team=../docs/标准组.csv `
  --dataset recruitment=../docs/新增.csv `
  --dataset co_recruitment=../docs/同引.csv `
  --param report_month_name=五月 `
  --param data_month_name=5月 `
  --param quarter_name=二季度 `
  --param cutoff_date=5月31日 `
  --output output/五月业绩分析报告.md `
  --context-output output/五月业绩分析报告.context.json
```

需要临时调整业务阈值时，可用额外的 `--param` 覆盖已声明参数的默认值。

## 计划接口

- `analysis_instructions` 中的自然语言只用于帮助人和 Agent 理解意图，不参与执行。
- 结构化 `steps` 列表是唯一可执行的分析计划。
- 计划只能引用关联指标目录中的指标，并遵守该指标声明的可用操作。
- 简单 band 使用 `metric`、`operator`、`threshold_param`；区间、复合条件使用 `conditions.match`（`all` 或 `any`）以及规则级指标、运算符和阈值。
- 业务规则需要处理缺失值时，显式使用 `is_missing` 或 `not_missing`。
- 保留用户要求的步骤顺序。用户删除某个分类维度时删除对应步骤；用户增加维度时，为支持分类的候选指标增加 `classify` 步骤。
- 阈值应声明为参数，使默认规则可复用、单次运行覆盖可审计。

## 职责规则

- Agent：理解意图、选择场景和指标、生成声明式步骤。
- Python：读取数据、转换数值、选择行、计算、分类、排序并生成信号。
- Jinja：循环渲染结构化结果和标准报告；不得读取 CSV 或计算业务结果。
- 指标目录和场景不得写死物理 CSV 路径；运行时绑定逻辑数据集。
- 上下文保留原始数值，格式化方式放在指标元数据或展示配置中。
- 跨场景的报告级内容（例如贺报）通过 `signals` 暴露。
- 缺少列、数值非法、步骤或指标或运算符未知、指标操作不允许、汇总行不唯一时必须失败；除非规则明确匹配 `is_missing`，不得把缺失指标自动转换为零。
- 当前配置以手工编写为主，不要求外部 JSON Schema 校验；使用必要的运行时保护和测试控制风险。

新增或修改指标目录、场景或步骤前，请阅读 [references/scene-contract.md](references/scene-contract.md)。
