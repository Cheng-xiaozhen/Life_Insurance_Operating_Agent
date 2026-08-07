# 月度业绩分析模板 V2

V2 使用“指标目录 + 有序分析步骤 + 确定性执行器 + 场景 Jinja”生成可审计的保险业绩分析。Agent 负责理解问题、选择场景和组织步骤；Python 负责读取 CSV、汇总、分类、排序；Jinja 负责标准 Markdown 表达。

```text
用户问题
   │
   ▼
Agent：匹配 scene.match，选择候选指标，组织 steps
   │
   ├── assets/metrics/*.yaml：字段映射、单位、格式、允许操作
   └── assets/scenes/*/scene.yaml：参数、步骤、阈值、信号
   ▼
render_scene.py：读取 CSV、计算、复合条件筛选、生成上下文
   │
   ├── 每个场景独立的 scene.md.j2
   └── report.md.j2：按 report.yaml 顺序组合场景
   ▼
Markdown 报告 + 审计上下文 JSON
```

## 目录结构

```text
monthly-performance-analysis-template-v2/
├── SKILL.md / SKILL-CN.md
├── agents/openai.yaml
├── assets/
│   ├── metrics/                         # 九个逻辑数据集的候选指标目录
│   ├── scenes/                          # 标保及八个新增场景
│   │   └── <scene>/scene.yaml + scene.md.j2
│   └── reports/monthly-performance/      # 标准组合报告
├── references/scene-contract.md
├── scripts/render_scene.py
├── tests/test_render_scene.py
├── requirements.txt
└── output/
```

## 已实现的场景

| 场景 | 数据集 ID | CSV | 主要分析 |
|---|---|---|---|
| 标保 | `standard_premium` | `标保.csv` | 月度达成率、年度进度 |
| 价值 | `value` | `价值.csv` | 价值达成率 |
| 活动人力 | `active_manpower` | `活动人力.csv` | 活动人力达成率及目标 |
| 阳光人力 | `sunshine_manpower` | `阳光人力.csv` | 阳光人力达成率和活动占比 |
| 主管活动 | `supervisor_activity` | `主管活动.csv` | 主管活动率 |
| 主管双星 | `supervisor_double_star` | `主管双星.csv` | 双星率 |
| 标准组 | `standard_team` | `标准组.csv` | 同比和标准组占比 |
| 新增 | `recruitment` | `新增.csv` | 新增达成率、本科占比目标 |
| 同引 | `co_recruitment` | `同引.csv` | 季度达成率和双字段挂零 |

公司评价场景暂未加入标准报告。

## 步骤契约

### 汇总

```yaml
- id: overall_summary
  type: summarize
  metrics:
    - metric: month_amount
      prefix: ${data_month_name}标保共达成
      separator_after: "；"     # 可选，覆盖本步默认分隔符
  presentation:
    prefix: 截至${cutoff_date}，全系统
    separator: "，"
    terminator: "。"
```

### 简单分类

```yaml
- id: rate_classification
  type: classify
  metric: month_rate
  display_order: source
  bands:
    - id: high
      operator: gt
      threshold_param: high_threshold
      presentation:
        style: threshold_list
        subject: 达成率
        comparison: "高于 "
```

### 复合分类

区间、AND、OR 和缺失值都通过声明式条件实现，不在 Python 中增加业务专用分支：

```yaml
conditions:
  match: all
  rules:
    - metric: added_rate
      operator: gt
      threshold_param: middle_low
    - metric: added_rate
      operator: lt
      threshold_param: target
```

支持 `eq`、`gt`、`gte`、`lt`、`lte`、`is_missing`、`not_missing`；展示顺序支持 `source`、`metric_asc`、`metric_desc`。`count_list` 和 `count_text` 用于带数量的目标/未达标表达。

`operations` 是指标允许的操作清单。执行器会拒绝指标未声明的 `summarize` 或 `classify` 用法。

## 运行完整五月报告

要求 Python 3.10 或更高版本，并安装 `Jinja2`、`PyYAML`。

```powershell
.\.venv\Scripts\python.exe `
  .\monthly-performance-analysis-template-v2\scripts\render_scene.py `
  --report .\monthly-performance-analysis-template-v2\assets\reports\monthly-performance\report.yaml `
  --dataset "standard_premium=.\docs\标保.csv" `
  --dataset "value=.\docs\价值.csv" `
  --dataset "active_manpower=.\docs\活动人力.csv" `
  --dataset "sunshine_manpower=.\docs\阳光人力.csv" `
  --dataset "supervisor_activity=.\docs\主管活动.csv" `
  --dataset "supervisor_double_star=.\docs\主管双星.csv" `
  --dataset "standard_team=.\docs\标准组.csv" `
  --dataset "recruitment=.\docs\新增.csv" `
  --dataset "co_recruitment=.\docs\同引.csv" `
  --param "report_month_name=五月" `
  --param "data_month_name=5月" `
  --param "quarter_name=二季度" `
  --param "cutoff_date=5月31日" `
  --output .\monthly-performance-analysis-template-v2\output\五月业绩分析报告.md `
  --context-output .\monthly-performance-analysis-template-v2\output\五月业绩分析报告.context.json
```

阈值参数可通过额外的 `--param` 覆盖；最终生效参数和每个 band 的实际条件都会写入上下文 JSON。

## 运行时保护

执行器会拒绝缺少参数或 CSV 列、非法数值、重复或缺失“全系统”行、未知步骤/指标/运算符/展示样式、指标操作不允许和错误的 signal 引用。普通筛选不会把缺失值当作零，只有 `is_missing` 规则会显式选择缺失值。

当前配置以人工编写为主，不使用外部 JSON Schema；运行时保护和黄金测试承担初期契约检查。执行器不推断阈值，也不使用 Top-N 兜底。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover `
  -s .\monthly-performance-analysis-template-v2\tests -v
```

测试覆盖标保回归、九场景汇总与名单、复合条件、缺失值、操作权限、阈值覆盖、空分类、完整报告顺序和异常保护。V2 当前输出 Markdown 与审计上下文 JSON，PDF 排版属于后续独立阶段。
