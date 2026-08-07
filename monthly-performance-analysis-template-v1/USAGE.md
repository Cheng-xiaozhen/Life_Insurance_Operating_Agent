# 月度业绩分析报告复现指南

本文说明如何使用 `docs` 目录中的九个 CSV、`data-contract.json` 和 `report.md.j2`，生成一份完整的 Markdown 月度业绩分析报告。以下命令均从仓库根目录执行。

## 1. 输入与输出

模拟输入文件：

| 指标 | CSV |
|---|---|
| 标保 | `docs/标保.csv` |
| 价值 | `docs/价值.csv` |
| 活动人力 | `docs/活动人力.csv` |
| 阳光人力 | `docs/阳光人力.csv` |
| 主管活动 | `docs/主管活动.csv` |
| 主管双星 | `docs/主管双星.csv` |
| 标准组 | `docs/标准组.csv` |
| 新增 | `docs/新增.csv` |
| 同引 | `docs/同引.csv` |

模板资产：

- `templates/data-contract.json`：CSV 来源、字段映射、排名数量、阈值刻度和校验规则。
- `templates/report.md.j2`：最终报告正文。
- `scripts/render_report.py`：参考执行器。
- `requirements.txt`：固定版本的运行依赖。

复现命令会生成：

- `output/五月业绩分析报告.md`：最终报告。
- `output/五月业绩分析报告.context.json`：渲染上下文，用于审计汇总数据、机构名单和推算阈值。

## 2. 环境要求

- Python 3.10 或更高版本。
- 能够从 Python 包索引安装 `Jinja2==3.1.6`。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\monthly-performance-analysis-template\requirements.txt
```

macOS 或 Linux：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r ./monthly-performance-analysis-template/requirements.txt
```

虚拟环境只用于隔离依赖，不需要激活；后续命令直接调用其中的 Python。

## 3. 一条命令生成模拟报告

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe `
  .\monthly-performance-analysis-template\scripts\render_report.py `
  --data-dir .\docs `
  --report-month-name 五月 `
  --data-month-name 5月 `
  --cutoff-date 5月31日 `
  --quarter-name 二季度 `
  --output .\monthly-performance-analysis-template\output\五月业绩分析报告.md `
  --context-output .\monthly-performance-analysis-template\output\五月业绩分析报告.context.json
```

macOS 或 Linux：

```bash
./.venv/bin/python \
  ./monthly-performance-analysis-template/scripts/render_report.py \
  --data-dir ./docs \
  --report-month-name 五月 \
  --data-month-name 5月 \
  --cutoff-date 5月31日 \
  --quarter-name 二季度 \
  --output ./monthly-performance-analysis-template/output/五月业绩分析报告.md \
  --context-output ./monthly-performance-analysis-template/output/五月业绩分析报告.context.json
```

成功时终端会输出两个生成文件的绝对路径。

## 4. 执行过程

执行器按以下顺序运行：

1. 读取 `data-contract.json`，取得动态列、CSV 来源、默认阈值、数量范围、兜底数量、排名方向及模板名称。
2. 读取九个 CSV，清理表头和单元格空格，将百分数、`pt` 和整数转换为数值。
3. 在每个 CSV 的第一列查找唯一“全系统”行，生成 `summary`。
4. 排除“全系统”、机构为空和指标缺失的行。
5. 对每条普通规则先应用 `threshold.default` 和 `threshold.operator`。
6. 如果 `selection.min <= 默认结果数量 <= selection.max`，直接采用默认名单和默认阈值，记录 `selection_mode: default_threshold`。
7. 如果数量低于 `min` 或高于 `max`，按 `selection.direction` 排名并取 `selection.limit` 家；区间规则同时应用 `offset`。
8. 根据固定集合和 `threshold.step` 反推阈值，再用新阈值筛选全部有效机构。
9. 只有新阈值能够精确筛出相同的 `limit` 家机构时才继续，否则报错。
10. 恢复 CSV 原始顺序，生成 `organizations`、实际阈值和 `selection_mode`。
11. 生成 `meta`、`summary`、`derived` 上下文 JSON。
12. 使用严格缺失值模式渲染 `report.md.j2`，写出 Markdown 报告。

数据流如下：

```text
九个 CSV + 命令行月份参数
            │
            ▼
   清洗、数值化、全系统汇总
            │
            ▼
       使用默认阈值筛选
            │
  数量是否位于 [min, max]？
       ├─ 是 → 展示默认阈值结果
       └─ 否 → 排名取 limit 家 → 反推阈值 → 重新筛选校验
            │
            ▼
      恢复源 CSV 展示顺序
            │
            ▼
 meta + summary + derived（审计 JSON）
            │
            ▼
       report.md.j2 → Markdown 报告
` 

## 5. 标保示例如何产生

契约中的5月标保高位规则为：

```json
{
  "column": "{data_month_name}达成率",
  "selection": {
    "direction": "descending",
    "limit": 9,
    "min": 9,
    "max": 9
  },
  "threshold": {
    "operator": ">",
    "step": 10,
    "default": 70
  }
}
```

使用当前模拟数据时：

1. 先执行默认条件 `5月达成率 > 70%`。
2. 结果为9家，位于 `[9, 9]` 内。
3. 直接采用默认阈值70%，`selection_mode` 为 `default_threshold`。
4. 按 CSV 原始顺序展示：河南、天津、潍坊、云南、新疆、海南、内蒙古、宁波、甘肃。

如果把范围放宽为 `[3, 9]`，并把默认阈值改成80%，默认筛选得到4家。4虽然不等于 `limit: 9`，但位于允许范围内，因此直接展示4家和80%，不反推阈值。

如果范围为 `[5, 10]`，同样的4家结果低于 `min: 5`。程序按达成率降序取 `limit: 9` 家，反推阈值70%，验证 `> 70%` 能精确筛出9家，并记录 `selection_mode: inferred_threshold`。

模板始终使用实际 `organizations` 数组生成数量和名单。

## 6. 复现结果检查

检查标保正文：

```powershell
Select-String `
  -Path .\monthly-performance-analysis-template\output\五月业绩分析报告.md `
  -Pattern "标保达成率高于"
```

预期包含：

```text
9家机构5月标保达成率高于70%：河南、天津、潍坊、云南、新疆、海南、内蒙古、宁波、甘肃
```

检查审计上下文：

```powershell
$context = Get-Content -Raw -Encoding UTF8 `
  .\monthly-performance-analysis-template\output\五月业绩分析报告.context.json | ConvertFrom-Json
$context.summary.standard_premium
$context.derived.standard_premium.month_rate_high
```

标保汇总预期为：

```text
month_amount  : 12444
month_rate    : 61.8
month_yoy     : -11.6
year_amount   : 100538
year_progress : 53.8
year_yoy      : 20.6
```

## 7. 用户请求到报告的调用方式

当用户提出“请生成一份5月业绩分析报告”时，调用方需要先明确四个元数据：

```json
{
  "report_month_name": "五月",
  "data_month_name": "5月",
  "cutoff_date": "5月31日",
  "quarter_name": "二季度"
}
```

然后把这四个值传给 CLI。不要从文件名或当前日期推断月份，因为封面月份、数据列月份、截止日期和季度可能不一致。

推荐的应用层执行流程是：

1. 确认九个 CSV 均已上传到同一数据目录。
2. 从用户请求或结构化参数中取得四个元数据。
3. 调用 `render_report.py`。
4. 若执行失败，把缺失列、重复全系统行、排名数量不足或边界无法分隔等错误反馈给用户。
5. 若执行成功，先保存上下文 JSON，再向用户返回 Markdown 报告；需要 PDF 时，应在 Markdown 内容复核后进入独立的 PDF 排版流程。

## 8. 更换月份

假设要生成6月报告：

- CSV 表头必须包含 `6月达成`、`6月达成率`、`6月同比` 等契约动态列。
- 同引 CSV 必须包含与传入季度一致的列。
- 命令中显式改为 `--report-month-name 六月 --data-month-name 6月 --cutoff-date 6月30日`。
- 如果默认阈值、允许数量范围、越界兜底数量或反推刻度变化，分别修改 `threshold.default`、`selection.min/max`、`selection.limit` 或 `threshold.step`。

## 9. 常见失败及处理

| 错误 | 含义 | 处理 |
|---|---|---|
| 找不到数据文件 | 九个 CSV 不完整或目录错误 | 检查 `--data-dir` |
| 列不存在 | 月份、季度参数与 CSV 表头不一致 | 检查动态列名和命令参数 |
| 全系统行不是恰好1行 | 汇总行缺失或重复 | 修正 CSV 第一列中的“全系统”行 |
| 数量范围无效 | 不满足 `min <= limit <= max` | 修正 `selection.min/max/limit` |
| 排名兜底数量不足 | 有效机构数少于 `limit` | 补数据或调整兜底数量 |
| 反推阈值不能精确恢复名单 | 边界并列或 `step` 不适用 | 调整 `limit`、处理并列或修改 `step` |
| Jinja 严格缺失值错误 | 上下文字段与模板引用不一致 | 对照上下文 JSON 和模板字段 |

## 10. 当前产物边界

参考执行器生成 Markdown 和审计 JSON，不生成 PDF，也不复刻原 PDF 下半部分的数据表格。PDF 输出应作为后续独立步骤处理，并在排版后进行逐页视觉检查。