# Template Analysis Agent V4 模板设计规则

本文说明 V4 当前的模板分层、字段含义、执行规则、文案约束和新增场景流程。文档以代码和现有九场景配置为准。

## 1. 总体设计

V4 将“数据读取、业务计算、自然语言表达”拆成三个边界清晰的阶段：

```text
CSV / 标准化 JSON
        ↓
AnalysisDataset（全系统汇总 + 机构明细）
        ↓
场景 YAML（指标、参数、步骤、阈值、文案分组）
        ↓
确定性 Fact（Python 计算）
        ↓
DeepSeek 受约束表达
        ↓
事实校验 + 固定顺序组装
        ↓
Markdown 报告
```

核心原则：

1. YAML 决定“分析什么”，Python 决定“事实是什么”，LLM 只决定“事实怎么说”。
2. LLM 不接触原始 CSV，只接收已经计算完成的 Fact。
3. 阈值、机构名单、数量和排序均由 Python 产生，禁止 LLM 重新计算。
4. 月报九个场景全部为必需项，任一场景失败时不生成部分报告。
5. 历史五月报告的 73 条事实是当前九场景的回归基线。

## 2. 配置分层

| 配置 | 目录 | 职责 |
|---|---|---|
| 场景模板 | `templates/*.yaml` | 定义指标、阈值、计算步骤、信号和文案分组 |
| CSV Profile | `profiles/monthly-performance.yaml` | 将九个业务 CSV 映射为统一数据结构 |
| 月报 Recipe | `reports/monthly-performance.yaml` | 定义标题、九场景顺序和贺报规则 |

三个配置层不要混用：

- CSV 列名只放在 Profile 中。
- 业务阈值和分类规则只放在场景模板中。
- 跨场景顺序和贺报只放在月报 Recipe 中。

## 3. 场景模板结构

一个场景对应一个 YAML 文件，基本结构如下：

```yaml
id: example-scene
version: 4.1.0
title: 示例场景
description: 示例场景说明。
keywords: [示例, 示例指标]

parameters: {}
metrics: {}
steps: []
signals: {}
narrative_groups: []
```

### 3.1 基础元数据

| 字段 | 必填 | 规则 |
|---|---|---|
| `id` | 是 | 全局唯一、稳定，推荐使用小写 kebab-case |
| `version` | 是 | 模板版本；规则或输出契约变化时递增 |
| `title` | 是 | 独立报告和完整月报中的场景标题 |
| `description` | 是 | 说明场景分析范围，不参与计算 |
| `keywords` | 是 | 自然语言路由使用的关键词列表 |

模板加载器只读取模板目录第一层的 `*.yaml` 文件。目录不存在、没有 YAML 或模板 ID 重复都会直接失败。

### 3.2 参数 `parameters`

参数用于保存可覆盖的业务阈值：

```yaml
parameters:
  value_high_threshold:
    default: 85
    description: 价值达成率高位阈值。
```

当前参数字段只有：

- `default`：默认值。
- `description`：业务说明。

运行时参数按以下优先级合并：

```text
模板默认值 < AnalysisRequest.parameters 中的同名值
```

模板中的 `threshold_param` 必须指向一个能够解析为数值的参数。报告月份、数据月份、季度和截止日期属于月报上下文，不作为场景阈值参数维护。

### 3.3 指标 `metrics`

指标定义语义 ID、展示名称和格式：

```yaml
metrics:
  month_yoy:
    label: 月度价值同比
    formatter: pct
    semantic: yoy
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `label` | 是 | 报告中的指标名称 |
| `formatter` | 是 | 指标值的展示格式 |
| `semantic` | 否 | `value`、`yoy` 或 `gap`，默认 `value` |
| `threshold_formatter` | 否 | 阈值使用不同格式时指定 |

支持的格式器：

| formatter | 示例 |
|---|---|
| `number` | `100` |
| `integer` | `100` |
| `person` | `100人` |
| `pct` | `61.8%` |
| `pt` | `5.4pt` |
| `wan` | `12444万` |
| `abs_pct` | `-20` 展示为 `20%`，主要用于“负增超过20%” |

`semantic: yoy` 会根据数值正负生成方向：

- 大于 0：`increase`
- 小于 0：`decrease`
- 等于 0：`flat`

方向会进入 Fact，并用于检查 LLM 是否把增长写成下降或把下降写成增长。

## 4. 分析步骤 `steps`

V4 支持三类操作：`summarize`、`rank`、`classify`。

### 4.1 汇总 `summarize`

从 `AnalysisDataset.summary` 中按指标 ID 读取全系统汇总值：

```yaml
- id: overall_summary
  title: 全系统概况
  op: summarize
  metrics: [month_amount, month_rate, month_yoy]
```

每个指标生成一条 summary Fact，ID 规则为：

```text
{template_id}.{step_id}.{metric_id}
```

例如：

```text
standard-premium.overall_summary.month_rate
```

### 4.2 排名 `rank`

从机构明细中按一个指标排序并取前 N 条：

```yaml
- id: top5
  title: 达成率 Top 5
  op: rank
  metric: month_rate
  order: desc
  limit: 5
```

规则：

- `order` 只能是 `asc` 或 `desc`。
- 指标缺失的机构不参加排名。
- 数值相同时保持原始 CSV 的机构顺序。
- 一整个排名步骤生成一条 ranking Fact。

当前九场景月报不使用排名操作，但执行器仍保留该能力。

### 4.3 分类 `classify`

分类步骤按规则筛选机构，每个 band 生成一条 classification Fact：

```yaml
- id: month_rate_classification
  title: 价值达成机构
  op: classify
  order: source
  bands:
    - id: high
      label: 价值达成率较高机构
      metric: month_rate
      operator: gt
      threshold_param: value_high_threshold
```

Fact ID 规则为：

```text
{template_id}.{step_id}.{band_id}
```

#### 支持的运算符

| operator | 含义 |
|---|---|
| `eq` | 等于 |
| `gt` | 高于 |
| `gte` | 不低于 |
| `lt` | 低于 |
| `lte` | 不高于 |
| `is_missing` | 指标缺失 |
| `not_missing` | 指标非缺失 |

边界必须按业务报告逐字确认。`gt: 70` 与 `gte: 70` 会产生不同名单，不能互换。

#### 阈值来源

阈值可以直接配置，也可以引用参数：

```yaml
threshold: 100
```

或：

```yaml
threshold_param: monthly_target_threshold
```

业务模板优先使用 `threshold_param`，便于运行时覆盖和审计。

#### 复合条件

使用 `conditions` 表达多个规则：

```yaml
- id: middle
  label: 新增中档机构
  conditions:
    match: all
    rules:
      - {metric: added_rate, operator: gt, threshold_param: recruitment_middle_low_threshold}
      - {metric: added_rate, operator: lt, threshold_param: recruitment_target_threshold}
  display_metric: added_rate
```

- `match: all`：所有条件均满足，相当于 AND。
- `match: any`：任一条件满足，相当于 OR。
- `display_metric`：机构名单中展示哪个指标；未设置时使用第一条规则的指标。

缺失值场景通常使用 `match: any`：

```yaml
conditions:
  match: any
  rules:
    - {metric: bachelor_gap, operator: lt, threshold_param: bachelor_target_threshold}
    - {metric: bachelor_gap, operator: is_missing}
```

#### 重叠与互斥

`exclusive` 默认是 `false`：每个 band 独立筛选，同一机构可以同时进入多个名单。

例如标保历史口径中：

- 达成率 105% 的机构同时属于“高于70%”和“目标达成”。
- 达成率 4% 的机构同时属于“低于50%”和“低于5%”。

设置 `exclusive: true` 后，机构一旦进入前面的 band，就不会再进入后续 band。当前月报为复现历史口径，使用非互斥分类。

#### 名单顺序

`order` 支持：

- `source`：保持 CSV 原始顺序，历史月报默认使用。
- `metric_asc`：按展示指标升序。
- `metric_desc`：按展示指标降序。

升降序时缺失值始终放在最后；数值相同保持原始顺序。

## 5. 实际阈值与展示阈值

某些历史规则的实际筛选边界和报告展示边界不同。例如主管双星实际按 55% 筛选，但报告写“高于50%”：

```yaml
- id: high
  metric: double_star_rate
  operator: gt
  threshold_param: double_star_high_effective_threshold
  display_threshold_param: double_star_high_threshold
  rule_text: "主管双星率高于{threshold_display}"
```

规则：

1. `threshold_param` 决定实际机构名单。
2. `display_threshold_param` 决定文案必须展示的阈值。
3. Fact 的规则审计信息仍保留实际阈值。
4. LLM 文案只能使用展示阈值，不能泄露不同的内部筛选阈值。

`rule_text` 可覆盖通用规则文案，目前支持 `{threshold_display}` 占位符。例如标准组：

```yaml
rule_text: "标准组同比负增超过{threshold_display}"
```

## 6. 场景信号 `signals`

信号用于把某条事实暴露给跨场景报告逻辑：

```yaml
signals:
  target_met: monthly_rate_classification.target_met
```

信号值可以写模板内的相对 Fact ID，执行时自动补全模板 ID。信号引用不存在的 Fact 时配置直接失败。

当前完整月报使用标保场景的 `target_met` 信号生成“贺报”。信号只做事实引用，不重新计算名单。

## 7. 文案分组 `narrative_groups`

文案分组决定一次 LLM 调用中，每个 Markdown 文本块必须表达哪些事实：

```yaml
narrative_groups:
  - id: overall
    title: 全系统概况
    fact_ids:
      - overall_summary.month_amount
      - overall_summary.month_rate
      - overall_summary.month_yoy
```

规则：

1. 每条 Fact 必须恰好属于一个 group。
2. 不允许引用未知 Fact。
3. 不允许多个 group 重复引用同一 Fact。
4. 不允许遗漏任何 Fact。
5. group 顺序就是独立场景报告中的章节顺序。
6. 完整月报忽略 group 的子标题，只按 group 顺序拼接其 Markdown 文本块。

LLM 必须返回：

```json
{
  "blocks": [
    {
      "group_id": "overall",
      "fact_ids": ["example.overall_summary.metric"],
      "text": "- 示例文案。"
    }
  ]
}
```

`group_id` 和 `fact_ids` 必须与模板完全一致。即使文字内容正确，擅自改变分组或事实顺序也会校验失败。

## 8. 路由规则

路由支持两种模式：

### 8.1 显式模板 ID

月报编排使用 `AnalysisRequest.template_id` 显式指定模板，不依赖自然语言：

```python
AnalysisRequest(template_id="standard-premium", dataset=dataset)
```

显式 ID 不存在时返回 `needs_clarification`。

### 8.2 关键词路由

独立场景分析使用 `keywords`：

1. 对问题和关键词执行 `casefold()`。
2. 关键词只要是问题的子串就算命中。
3. 模板得分等于所有命中关键词的字符长度之和。
4. 只有唯一最高分模板会被选中。
5. 无命中或最高分并列时返回 `needs_clarification`，不会猜测场景。

关键词应优先使用明确业务术语，避免多个场景共享过短、过泛的词。

## 9. CSV Profile 规则

Profile 将业务 CSV 转换为统一结构：

```yaml
profiles:
  standard-premium:
    filename: 标保.csv
    encoding: utf-8-sig
    row_label_column: 片区
    organization_column: 机构
    total_value: 全系统
    metrics:
      month_amount: "${data_month_name}达成"
      month_rate: "${data_month_name}达成率"
```

字段说明：

| 字段 | 说明 |
|---|---|
| Profile ID | 必须与场景模板 ID 一致 |
| `filename` | `--data-dir` 下的约定 CSV 文件名 |
| `encoding` | 当前九个文件统一为 `utf-8-sig` |
| `row_label_column` | 用来识别“全系统”汇总行的列 |
| `organization_column` | 机构名称列 |
| `total_value` | 汇总行匹配值，当前为“全系统” |
| `metrics` | 场景指标 ID 到 CSV 列名的映射 |

动态列名支持以下上下文变量：

- `${data_month_name}`，例如 `5月达成率`。
- `${quarter_name}`，例如 `二季度同引_达成率`。

标准化规则：

1. 自动去除 CSV 表头和单元格两端空白。
2. `%` 和 `pt` 后缀会被移除后解析为数值。
3. 空字符串和 `-` 解析为 `None`。
4. 禁止 `NaN` 和无穷大。
5. 清理后的表头不能重复。
6. 所有映射列必须存在。
7. 必须恰好有一条“全系统”汇总行。
8. 必须至少有一条机构明细。
9. 机构名单保持 CSV 原始顺序。

标准化后的数据结构为：

```json
{
  "summary": {"metric_id": 100},
  "rows": [
    {
      "organization": "机构A",
      "metrics": {"metric_id": 80}
    }
  ]
}
```

## 10. 月报 Recipe 规则

当前 Recipe：

```yaml
report:
  id: monthly-performance
  version: 4.0.0
  title: "{report_month_name}业绩分析报告"
  scenes:
    - standard-premium
    - value
    # 其余七个场景……
  callout:
    title: 贺报
    scene_id: standard-premium
    signal: target_met
    text: "热烈祝贺 {organizations} 达成{data_month_name}标保目标"
```

规则：

1. 完整月报必须配置九个场景。
2. `scenes` 顺序就是最终报告章节顺序。
3. 标题使用 Python `str.format` 风格的 `{report_month_name}`。
4. 贺报只读取已计算的 signal；名单为空时不生成贺报。
5. 九个 CSV 在首次调用 DeepSeek 前全部完成预检。
6. DeepSeek 按场景顺序调用九次，不并发、不降级。
7. 任一场景调用失败或文案校验失败，整份报告状态为 `failed`。

## 11. 文案事实校验

文案返回后逐 group 校验：

- group 是否存在且只出现一次。
- `fact_ids` 是否与模板完全一致。
- 每条必需 Fact 是否恰好覆盖一次。
- 文案数字是否来自所引用 Fact 或月报上下文。
- 是否完整表达所有汇总值、分类数量和展示阈值。
- 是否遗漏机构或加入事实之外的机构。
- 同比增长、下降、持平方向是否与 Fact 一致。
- 是否包含“原因、可能、预计、建议、优化、改进、因为、措施”等禁止表达。

因此模板设计者必须把希望出现在文案中的所有数字和机构先建模为 Fact；不能依靠提示词让 LLM 临时补充。

## 12. 当前九场景事实清单

| 场景 ID | 标题 | Fact 数 | 文案组数 |
|---|---:|---:|---:|
| `standard-premium` | 标保 | 13 | 3 |
| `value` | 价值 | 7 | 2 |
| `active-manpower` | 活动人力 | 6 | 2 |
| `sunshine-manpower` | 阳光人力 | 6 | 2 |
| `supervisor-activity` | 主管活动 | 6 | 2 |
| `supervisor-double-star` | 主管双星 | 5 | 2 |
| `standard-team` | 标准组 | 2 | 1 |
| `recruitment` | 新增 | 16 | 5 |
| `co-recruitment` | 同引 | 12 | 3 |
| **合计** |  | **73** | **22** |

## 13. 新增或修改场景的检查清单

### 新增场景

1. 在 `templates/` 新建场景 YAML，确定稳定的场景 ID。
2. 声明所有指标、格式和语义。
3. 将所有业务阈值定义为参数。
4. 用 `summarize`、`rank`、`classify` 表达确定性步骤。
5. 逐项确认 `gt/gte/lt/lte` 边界和名单排序。
6. 配置 `narrative_groups`，确保每条 Fact 恰好出现一次。
7. 如需跨场景贺报或提示，配置 `signals`。
8. 在 CSV Profile 中增加同 ID 映射，并配置文件名和动态列。
9. 将场景 ID 加入月报 Recipe 的正确位置。
10. 增加路由、CSV 错误、Fact 和完整报告测试。

### 修改现有场景

1. 判断是否会改变 Fact ID、阈值、机构名单或报告结构。
2. 规则变化时递增模板版本。
3. 不随意改变 step ID、band ID 和 metric ID；它们共同构成可审计的 Fact ID。
4. 对照历史 context 更新回归预期，不能只检查最终文字。
5. 确认所有文案分组仍完整覆盖事实。

## 14. 常见错误

- **把 CSV 列名写入场景模板**：应放入 Profile。
- **让 LLM 判断机构是否达标**：必须使用 `classify` 预先计算。
- **混淆 `gt` 与 `gte`**：会改变临界值机构名单。
- **历史重叠名单误设为 `exclusive: true`**：会丢失目标达成或严重低位子集。
- **新增 Fact 后忘记加入 `narrative_groups`**：模板加载后执行会报遗漏事实。
- **一个 Fact 放进多个 group**：会报重复引用。
- **展示阈值与实际阈值不同时只配置一个参数**：无法同时保证名单和报告口径。
- **动态月份与 CSV 表头不一致**：例如传入 `6月`，但文件只有 `5月达成率`，数据预检会失败。
- **把归因或建议写进模板提示**：当前 V4 只允许事实描述，校验器会拒绝推测和建议。

