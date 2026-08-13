# 标保 Presentation 模板试验

这个目录用于逐步确认 Presentation 模板的最终形态，暂不替换
`template-analysis-agent-v6` 中正在运行的正式模板。

## 文件

- `standard-premium.yaml`：更新后的标保 Scene 草案。
- `expected-standard-premium.md`：使用 `docs/标保.csv`、五月参数渲染后的目标结果。

## 本版要验证的设计

本版保留现有 Scene 的三层职责，并增加一个很窄的展示层：

1. `metrics` 定义指标的名称、数值类型和单位。
2. `analysis` 使用 `summary`、`select` 生成确定性 facts。
3. `presentation` 使用类型化 clause 将 facts 渲染成固定文字。

`presentation` 目前只定义三种 clause：

| 类型 | 用途 | 例子 |
|---|---|---|
| `summary` | 按固定顺序展示全系统指标 | 全系统达成、达成率、同比 |
| `selection` | 展示家数、条件和机构名单 | 9家机构达成率高于 70% |
| `annotation` | 展示某个独立筛选结果的补充说明 | 其中天津、新疆达成目标 |

这里没有模板函数、循环、`eval` 或自由条件表达式。每个 clause 都只引用一个
已经由 `analysis` 生成的 `fact_id`。

## 模板字段如何工作

### `selection`

下面的配置：

```yaml
- type: selection
  fact_id: high_month_rate
  subject: "${month_label}标保达成率"
  show_count: true
  show_threshold: true
  item_display: organization
```

由渲染器按以下方式解释：

1. 从 `facts.high_month_rate` 读取机构数组。
2. 家数使用数组长度，不让模型填写。
3. 从同名 `analysis` action 读取 `operator: ">"` 和参数 `high_rate: 70`。
4. 通用比较符映射把 `>` 渲染为“高于”。
5. 根据 `month_rate.value_type: percent` 将阈值展示为 `70%`。
6. `item_display: organization` 表示名单只输出机构名，不附带逐机构百分比。
7. `analysis` 没有配置 `order_by`，所以名单保持 CSV 源顺序。

最终得到：

```text
9家机构5月标保达成率高于 70%：河南、天津、潍坊、云南、新疆、海南、内蒙古、宁波、甘肃
```

### `annotation`

“达成目标”“不足 5%”“进度超 80%”都先在 `analysis` 中建立独立 action，
再由 `annotation` 展示。例如：

```yaml
- type: annotation
  fact_id: month_target_met
  prefix: 其中
  suffix: "达成${month_label}标保目标"
  item_display: organization
  empty: omit
```

渲染器只负责把筛选结果放到 `prefix` 和 `suffix` 之间，不从高位名单中再次计算，
也不让 LLM 决定是否出现该结论。`empty: omit` 表示没有符合条件的机构时省略这条补充说明。

### `summary`

`summary.items` 决定指标顺序和报告标签：

```yaml
- {field: month_yoy, label: 同比, formatter: trend}
```

`metric` formatter 原样使用 fact 的 `display` 并补充 metric 单位；`trend`
formatter 根据规范化数值的正负号输出“正增/负增 + 绝对值”。formatter 是通用能力，
不包含标保专用判断。

## 预期使用方式

Report 需要向 Scene 提供展示参数：

```yaml
parameters:
  report_month: 五月
  month_label: 5月
  cutoff_date: 5月31日

params_schema:
  cutoff_date:
    type: string
    required: true
    description: 报告统计截止日期，例如“5月31日”。

sections:
  - scene: standard-premium
```

未来解析器接入后的执行过程应为：

```text
加载并校验 Scene
  → 执行 analysis 生成 facts
  → 校验 presentation 引用的 fact/field/parameter
  → 按 bullets 和 clauses 顺序确定性渲染
  → 与其他 Scene 结果组装为 report.md
```

命令入口仍保持不变：

```powershell
.venv\Scripts\python.exe template-analysis-agent-v6\standalone_agent.py `
  "请基于当前数据生成五月业绩分析报告" `
  --data-context-id docs
```

但要注意：**当前 v6 尚未实现 Presentation 解析器，且正式 Scene 还没有替换为本目录草案，
所以现在运行上述命令不会产生 `expected-standard-premium.md` 中的格式。** 本目录当前只用于模板评审，
不要直接复制覆盖正式模板。

模板确认后，再按以下顺序接入：

1. 在 Report 中增加 `cutoff_date` 参数。
2. 为 Scene 增加 Presentation 结构校验。
3. 实现 `summary`、`selection`、`annotation` 三种通用 renderer。
4. 将确认后的模板移入正式 `references/scenes`。
5. 使用 `expected-standard-premium.md` 做黄金文件测试。

## 本轮建议重点确认

1. `presentation` 是否继续放在 Scene 内，而不是拆成单独文件。
2. 三种 clause 是否足以覆盖标保场景，是否需要更少或更多类型。
3. 机构名单默认保持源顺序是否符合业务预期。
4. “达成目标、极低、进度突出”是否都应作为独立 action。
5. 目标文案是否采用当前统一空格和标点，还是要逐字符复刻历史报告。

