# 月度业绩分析报告模板

## 场景化 Skill 入口

项目现已提供可组合场景库。Codex 入口为 `SKILL.md`；首个场景位于
`assets/scenes/standard-premium/`，使用逻辑数据集 `standard_premium`，并由
`scripts/render_scene.py` 生成场景 Markdown 和审计 JSON。报告级的标题、贺报与场景顺序由
`assets/reports/monthly-performance/` 负责。

```powershell
.\.venv\Scripts\python.exe .\monthly-performance-analysis-template\scripts\render_scene.py `
  --scene .\monthly-performance-analysis-template\assets\scenes\standard-premium\scene.yaml `
  --dataset "standard_premium=.\docs\标保.csv" `
  --param "data_month_name=5月" `
  --param "cutoff_date=5月31日" `
  --output .\monthly-performance-analysis-template\output\标保场景分析.md `
  --context-output .\monthly-performance-analysis-template\output\标保场景分析.context.json
```

场景契约及新增场景规则见 `references/scene-contract.md`。下文记录的
`data-contract.json + report.md.j2` 是现有完整九场景报告的兼容实现，在其他场景迁移完成前继续保留。

本目录提供一套“数据规则与报告文案分离”的月度业绩分析方案。九个 CSV 先按照 `data-contract.json` 转换成统一的报告上下文，再交给 `report.md.j2` 渲染为 Markdown。

```text
九个 CSV + 月份参数
        │
        ▼
data-contract.json
字段映射、汇总、默认阈值筛选、数量兜底、校验
        │
        ▼
meta + summary + derived (分析报告.context.json方便审计)
        │
        ▼
report.md.j2
章节结构、文字表达、格式化
        │
        ▼
Markdown 业绩分析报告
```

实际安装、执行、换月和结果核验步骤见 [`USAGE.md`](USAGE.md)。

## 目录结构

```text
monthly-performance-analysis-template/
├── README.md
├── USAGE.md
├── requirements.txt
├── scripts/
│   └── render_report.py
├── templates/
│   ├── data-contract.json
│   └── report.md.j2
└── output/                         # 执行后生成
    ├── 五月业绩分析报告.md
    └── 五月业绩分析报告.context.json
```

其中 `data-contract.json` 和 `report.md.j2` 是模板的核心：前者决定“数据如何计算”，后者决定“结果如何表达”。

## data-contract.json：数据计算契约

`templates/data-contract.json` 是数据层的唯一规则来源。它不负责写报告文案，而是描述输入 CSV 如何转换成模板可使用的结构化上下文。

### 1. 基本配置

```json
{
  "contract_version": "4.0.0",
  "template": "report.md.j2",
  "source_encoding": "UTF-8"
}
```

- `contract_version`：契约结构版本。字段结构或运行语义发生不兼容变化时应升级主版本。
- `template`：契约最终交给哪个 Jinja 模板渲染。
- `source_encoding`：CSV 读取编码。

### 2. normalization：输入清洗规则

`normalization` 规定所有 CSV 的共同处理方式：

- 清理表头和字符串两侧空格。
- 将 `61.8%` 转成数值 `61.8`。
- 将 `-1.1pt` 转成数值 `-1.1`。
- 将空字符串和 `-` 识别为缺失值。
- 第一列等于“全系统”的行作为汇总行，且必须恰好出现一次。
- 机构默认按指定阈值筛选；数量落在 `selection.min` 与 `selection.max` 的包含区间内时直接展示，越界时才按 `selection.limit` 反推阈值。最终名单恢复源 CSV 行顺序。

缺失值不会自动变成零。这样可以区分“没有数据”和“指标确实为0”。

### 3. meta：运行时元数据

`meta` 定义每次生成报告必须显式传入的参数：

```json
{
  "report_month_name": "五月",
  "data_month_name": "5月",
  "cutoff_date": "5月31日",
  "quarter_name": "二季度"
}
```

四个值分开传入，因为封面月份、CSV 动态列、截止日期和季度不一定能从文件名互相推断。

`{data_month_name}` 和 `{quarter_name}` 可以出现在列名配置中。例如：

```json
{
  "column": "{data_month_name}达成率"
}
```

当 `data_month_name` 为 `5月` 时，运行时解析为 CSV 列 `5月达成率`。

### 4. summary：全系统汇总字段

`summary` 描述报告中的全系统指标从哪个 CSV、哪一列取得。以标保为例：

```json
{
  "standard_premium": {
    "source": "标保.csv",
    "row": "全系统",
    "fields": {
      "month_amount": {
        "column": "{data_month_name}达成",
        "format": "amount_wan"
      },
      "month_rate": {
        "column": "{data_month_name}达成率",
        "format": "pct"
      }
    }
  }
}
```

执行器在 `标保.csv` 第一列中找到唯一的“全系统”行，并生成：

```json
{
  "summary": {
    "standard_premium": {
      "month_amount": 12444,
      "month_rate": 61.8
    }
  }
}
```

字段名称使用业务含义，而不是“参数1”“参数2”，使模板引用和上下文审计更直观。

### 5. derived：机构名单和实际阈值

`derived` 负责机构级分析。默认策略是：

> 先使用指定的默认阈值筛选机构。结果数量落在 `[selection.min, selection.max]` 内时，直接使用该名单和默认阈值；只有数量低于 `min` 或高于 `max` 时，才按排名取 `selection.limit` 家并反推能够精确筛出该集合的阈值。

`min` 和 `max` 均为包含边界。`limit` 是越界后的兜底数量，不是默认阈值结果必须达到的数量。契约要求：

```text
selection.min <= selection.limit <= selection.max
```

以“5月标保达成率高位机构”为例：

```json
{
  "month_rate_high": {
    "column": "{data_month_name}达成率",
    "selection": {
      "direction": "descending",
      "limit": 9,
      "min": 0,
      "max": 10
    },
    "threshold": {
      "operator": ">",
      "step": 10,
      "default": 70
    }
  }
}
```

当前模拟数据执行 `5月达成率 > 70%` 后恰好得到9家，数量位于 `[0, 10]` 内，因此直接采用默认阈值70%，并记录：

```json
{
  "organizations": [
    "河南", "天津", "潍坊", "云南", "新疆",
    "海南", "内蒙古", "宁波", "甘肃"
  ],
  "threshold": 70,
  "selection_mode": "default_threshold"
}
```

如果业务允许默认阈值结果展示3至9家，可配置：

```json
{
  "selection": {
    "direction": "descending",
    "limit": 9,
    "min": 3,
    "max": 9
  }
}
```

此时把默认阈值设为80%，模拟数据筛出4家。虽然4不等于 `limit: 9`，但4位于 `[3, 9]` 内，因此仍展示这4家和80%，不会触发排名反推。

如果把范围改为 `[5, 10]`，相同的4家结果低于 `min: 5`。执行器才会按达成率降序取 `limit: 9` 家，反推阈值70%，验证新阈值能够精确筛出9家，并记录 `selection_mode: inferred_threshold`。

### 6. 四种机构选择策略

#### threshold_range_then_ranked_limit：默认阈值加数量区间兜底

这是未显式填写 `selection.strategy` 时的默认策略：

```json
{
  "selection": {
    "direction": "ascending",
    "limit": 8,
    "min": 6,
    "max": 10
  },
  "threshold": {
    "operator": "<",
    "step": 10,
    "default": 50
  }
}
```

先执行 `< 50`。结果为6至10家时直接展示；少于6家或多于10家时，按指标升序取8家并反推阈值。

#### ranked_window：默认区间加排名窗口兜底

区间规则同时具有默认指标下界、上界和数量范围：

```json
{
  "selection": {
    "strategy": "ranked_window",
    "direction": "descending",
    "offset": 2,
    "limit": 4,
    "min": 3,
    "max": 5
  },
  "lower_threshold": {
    "operator": ">",
    "step": 10,
    "default": 80
  },
  "upper_threshold": {
    "reuse": "target_met.threshold",
    "operator": "<",
    "default": 100
  }
}
```

先按默认指标区间 `> 80% 且 < 100%` 筛选。结果为3至5家时直接展示；越界时跳过排名前2家，取接下来的4家并反推下界。上界复用目标达成规则实际采用的阈值。

#### complement：补集

根据已生成名单计算其余机构，并可保留指标缺失机构：

```json
{
  "selection": {
    "strategy": "complement",
    "of": "bachelor_target_met",
    "include_missing": true
  }
}
```

补集规则不使用 `min`、`max`、`limit` 或阈值反推。

#### compound_condition：复合条件

多个条件必须同时成立：

```json
{
  "selection": {
    "strategy": "compound_condition",
    "display_order": "source"
  },
  "all": [
    {"column": "{data_month_name}送训_送训", "operator": "==", "value": 0},
    {"column": "{quarter_name}同引_达成", "operator": "==", "value": 0}
  ]
}
```

复合条件规则直接按条件计算名单，不使用数量区间或阈值反推。

### 7. 默认阈值和反推阈值

单侧规则的阈值配置为：

```json
{
  "operator": ">=",
  "step": 10,
  "default": 100
}
```

- `default`：首先执行的默认业务阈值。
- `operator`：比较关系，如 `>`、`>=` 或 `<`。
- `step`：默认结果越界时允许反推的业务刻度。

反推只在以下情况触发：

```text
default_count < selection.min
或
default_count > selection.max
```

反推阈值必须使用配置的 `operator` 精确筛出排名选定的 `selection.limit` 家机构。若多个刻度有效，取数值最小者；若边界并列或没有有效刻度，执行器报错。

`selection_mode` 用于审计：

- `default_threshold`：默认结果数量位于 `[min, max]` 内。
- `inferred_threshold`：默认数量越界，按 `limit` 排名并反推阈值。
- `complement`、`compound_condition`：特殊规则。

### 8. validation：契约校验

运行时必须保证：

- 每个汇总字段存在且非缺失。
- 每个 CSV 的“全系统”行恰好一行。
- 普通规则满足 `min <= limit <= max`。
- 默认阈值结果在 `[min, max]` 内时不得触发排名反推。
- 默认阈值结果越界时，排名兜底必须取得 `limit` 家机构。
- 反推阈值必须重新筛出完全相同的 `limit` 家机构。
- 机构名单排除“全系统”和空机构。
- 模板中的机构数量由名单长度生成。
- 动态月份和季度列必须唯一存在。

契约在默认阈值、数量区间和排名兜底无法同时成立时会尽早失败。
## report.md.j2：报告表达模板

`templates/report.md.j2` 是展示层。它只读取已经计算完成的 `meta`、`summary` 和 `derived`，负责章节结构、条件显示和自然语言表达，不读取 CSV，也不重新计算排名。

### 1. 输入上下文

模板只依赖三类对象：

```json
{
  "meta": {},
  "summary": {},
  "derived": {}
}
```

- `meta`：报告月份、数据月份、截止日期和季度。
- `summary`：全系统汇总数值。
- `derived`：机构名单、实际采用的阈值和选择模式。

执行器使用 Jinja `StrictUndefined`。模板引用不存在的字段时直接报错，避免把缺失内容静默渲染为空字符串。

### 2. 动态数量、阈值和名单

标保分析模板写法如下：

```jinja2
{{ derived.standard_premium.month_rate_high.organizations | length }}家机构
{{ meta.data_month_name }}标保达成率高于
{{ derived.standard_premium.month_rate_high.threshold | pct }}：
{{ derived.standard_premium.month_rate_high.organizations | cn_join }}
```

这里没有硬编码“9家”“70%”或机构名称：

- 数量来自 `organizations | length`。
- 阈值来自契约本次实际采用的 `threshold`，可能是默认值，也可能是反推值。
- 名单来自 `organizations | cn_join`。

因此，数据规则变化应修改 `data-contract.json`，不应在正文模板中手工修改数字或机构名单。

### 3. 条件章节

贺报只在达标机构非空时显示：

```jinja2
{% if derived.standard_premium.month_target_met.organizations %}
## 贺报
...
{% endif %}
```

条件只控制文案是否展示，不承担机构筛选逻辑。

### 4. 格式化过滤器

执行器向模板注册以下过滤器：

| 过滤器 | 输入 | 输出 |
|---|---:|---|
| `amount_wan` | `12444` | `12444万` |
| `pct` | `61.8` | `61.8%` |
| `pt` | `5.4` | `5.4pt` |
| `yoy_pct_phrase` | `-11.6` | `同比负增11.6%` |
| `yoy_pt_phrase` | `3.7` | `同比正增3.7pt` |
| `gap_pt_phrase` | `-5.4` | `缺口5.4pt` |
| `cn_join` | `["河南", "天津"]` | `河南、天津` |

数值方向的文案由过滤器生成，CSV 和上下文只保存数值。例如 `-11.6` 保存为负数，模板自动生成“同比负增11.6%”。

### 5. 模板设计边界

`report.md.j2` 应当包含：

- 报告标题和章节结构。
- 自然语言句式。
- 条件显示。
- 格式化过滤器调用。

`report.md.j2` 不应包含：

- CSV 文件读取。
- 排名、筛选或阈值计算。
- 固定机构数量。
- 固定阈值。
- 固定机构名单。

## 两个核心文件如何协作

| 变化 | 修改位置 |
|---|---|
| CSV 列名变化 | `data-contract.json` 的 `column` |
| 指标来源文件变化 | `data-contract.json` 的 `source` |
| 默认业务阈值变化 | `data-contract.json` 的 `threshold.default` |
| 默认展示数量范围变化 | `data-contract.json` 的 `selection.min` / `selection.max` |
| 越界兜底数量变化 | `data-contract.json` 的 `selection.limit` |
| 排名方向变化 | `data-contract.json` 的 `selection.direction` |
| 反推业务刻度变化 | `data-contract.json` 的 `threshold.step` |
| 新增或调整报告章节 | `report.md.j2` |
| 调整一句话的表达 | `report.md.j2` |
| 调整百分比或同比格式 | 执行器过滤器及模板调用 |

修改后必须同时检查：

1. 契约生成的上下文是否包含模板引用字段。
2. 默认名单数量是否位于 `[min, max]`，或兜底名单数量是否等于 `limit`。
3. 默认数量越界时，反推阈值是否精确恢复 `limit` 指定的名单。
4. Jinja 严格模式是否能够完整渲染。
5. 输出报告中的数量、阈值和名单是否与上下文 JSON 一致。

## 当前输出边界

当前参考执行器生成：

- Markdown 业绩分析报告。
- 用于审计和复现的上下文 JSON。

当前不生成 PDF，也不复刻原 PDF 下半部分的数据表格。需要 PDF 时，应在 Markdown 内容确认后进入独立的排版和视觉检查流程。
