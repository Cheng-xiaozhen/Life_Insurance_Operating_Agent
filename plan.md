# 模板驱动分析 Agent：开发主线与技术设计计划

> 状态：设计基线（持续更新）  
> 当前范围：Phase 1 · 模板驱动分析 Agent  
> 依据：`docs/智能经营分析Agent_产品规划与设计.md`、`docs/智能经营分析Agent_Phase1_详细设计方案.md`

## 1. 产品目标

Phase 1 要开发的不是能够自由探索数据、临场规划分析路径的通用 Agent，而是一个**受控、可配置、可追溯的模板驱动分析系统**。

人工把成熟分析师的报告方法固化为可执行模板。用户用自然语言提出需求后，系统匹配模板、补齐参数、按报告顺序读取场景数据并执行筛选规则，再由 LLM 基于确定的数据和筛选结果生成场景分析与最终报告。

Phase 1 的核心价值是：

- 把分析师经验沉淀成可复用、可审核、可版本化的模板资产。
- 用受控数据绑定和确定性筛选保证数据、指标口径与分析结果可靠。
- 用 LLM 完成意图理解、数据解读和报告表达，而不是让其控制数据与口径。
- 让报告中的重要结论能够回溯到场景、CSV 数据和筛选结果。

## 2. 一句话开发主线

> 以“模板是程序、执行引擎是解释器”为核心，建设 Scene、Binding、Report 三层模板和线性执行引擎，打通“自然语言需求 → 模板匹配 → 参数绑定 → CSV 读取 → 阈值筛选排序 → 场景分析 → 报告”的完整闭环。

## 3. Phase 1 系统边界

1. **模板资产层**
   - 报告模板以 SKILL 作为可安装、可触发的能力封装。
   - 报告模板由可复用的声明式场景组成，负责场景选择、顺序和最终展现。
   - 场景以 YAML 定义语义字段、全系统摘要、阈值筛选排序规则和叙述要求。

2. **CSV 数据绑定层**
   - Phase 1 使用独立 Binding YAML，把场景的语义字段映射到实际 CSV 文件和列名。
   - CSV 保持在 SKILL 外部，只作为每次执行的输入数据。
   - 后续接入 API 或数据库时，输出相同的语义字段即可复用场景，不改变场景分析逻辑。

3. **阈值筛选排序层**
   - 报告需要展示的数字直接来自对应数据源，不在模板中定义计算公式。
   - 执行引擎只按场景声明的阈值筛选机构，并按指定字段排序。

4. **意图路由与参数补齐**
   - 在启用模板中匹配最合适的模板。
   - 抽取时间、机构、业务对象等模板参数。
   - 缺少必填参数时追问；低置信度时澄清，不强行执行。

5. **模板执行引擎**
   - 加载模板并绑定参数。
   - 按报告顺序依次执行场景的数据读取、筛选排序和文字分析。
   - 场景失败时记录原因，不让 LLM 猜测缺失数据。
   - 按报告结构综合生成最终报告。

## 4. 正确的端到端执行链路

```text
用户输入分析需求
  → 意图路由：选择 active 模板并抽取参数
  → 参数校验：缺失则追问，低置信度则澄清
  → 加载报告模板及其场景
  → 解析场景顺序和输出格式
  → 对每个 scene：
      1. 根据 Binding 读取场景对应的 CSV
      2. 取得场景声明的全系统数据字段
      3. 按阈值规则筛选并排序机构
      4. 将原始数字、筛选结果和写作要求交给 LLM
      5. 生成该场景的报告内容
  → 按报告模板的场景顺序组装并输出
  → 保存并展示最终报告
```

这个链路中，报告模板决定“使用哪些场景、按什么顺序、输出什么格式”；场景模板决定“展示哪些源数据、按什么阈值筛选排序”；LLM 只负责把结果写成报告文字。

## 5. 核心设计原则

1. **模板定义分析路径，代码不写死业务报告。** 新增同类场景应优先新增模板或查询配置，而不是修改引擎主流程。
2. **源数据、筛选规则、文字表达分离。** 数字来自数据源，代码执行阈值筛选排序，LLM 负责写作。
3. **不重复计算已有指标。** CSV 已提供达成率、同比、占比等报告数字，模板直接引用。
4. **模板只保留执行所需字段。** 不为未来功能预建复杂 DSL、依赖图和中间结果协议。
5. **失败必须显式。** 数据源缺失或规则引用的字段不存在时停止该场景，不让 LLM 猜测。
6. **先完成单模板纵向闭环，再扩充模板数量。** 先用月度经营分析模板验证架构，再扩展模板数量。


## 6. 目标架构的职责划分

| 模块 | 核心职责 | 不应承担的职责 |
|---|---|---|
| Template Registry | 加载报告 YAML 和场景 YAML | 执行数据分析 |
| Intent Router | 模板匹配、参数抽取、澄清决策 | 规划新的分析路径 |
| Execution Engine | 按报告顺序执行场景、组装最终报告 | 自主增加分析步骤 |
| Binding Resolver | 将场景语义字段映射到 CSV 文件和列名 | 定义场景分析规则 |
| Data Loader | 读取 CSV，清理表头与百分比等基础格式 | 计算数据源未提供的指标 |
| Rule Engine | 执行阈值筛选和升降序排列 | 执行任意表达式或业务计算 |
| LLM Gateway | 根据原始数字和规则结果生成场景文字 | 修改数字或重新判断阈值 |
| Execution Store | 保存运行、步骤、事件、产物与反馈 | 参与业务推理 |

## 7. 模板库设计基线

### 7.1 设计目标

模板只解决两个问题：

1. **场景模板**：指定从场景数据源展示哪些数字，以及按哪些阈值筛选、排序机构。
2. **报告模板**：指定使用哪些场景、场景顺序、各场景数据源和最终输出格式。

场景是最小复用单元。报告通过组合场景生成，不把九个场景的规则写进一个大模板。Phase 1 不设计通用计算 DSL，因为当前报告需要的金额、人数、达成率、同比、进度、占比和差额都已存在于 CSV 中。

### 7.2 当前场景范围

当前月度业绩分析模板只包含具有独立 CSV 数据源的 9 个基础场景。贺报、公司评价不属于当前范围，也不作为无数据源场景保留。

| 场景 ID | 中文名称 | 当前 CSV 输入 |
|---|---|---|
| `standard-premium` | 标保 | `docs/标保.csv` |
| `value` | 价值 | `docs/价值.csv` |
| `active-manpower` | 活动人力 | `docs/活动人力.csv` |
| `sunshine-manpower` | 阳光人力 | `docs/阳光人力.csv` |
| `supervisor-activity` | 主管活动 | `docs/主管活动.csv` |
| `supervisor-double-star` | 主管双星 | `docs/主管双星.csv` |
| `standard-team` | 标准组 | `docs/标准组.csv` |
| `recruitment` | 新增 | `docs/新增.csv` |
| `co-recruitment` | 同引 | `docs/同引.csv` |

CSV 是本次执行的数据输入，不属于场景模板。场景 YAML 只引用稳定的语义字段；Binding YAML 负责映射文件名和实际列名。未来接入 API 或数据库时，只要提供相同语义字段即可复用场景。

### 7.3 模板分层

模板库采用三层结构，每一层只解决一个问题：

```text
SKILL
  ├─ Report：选择和编排场景，定义最终输出格式
  ├─ Scene：定义一个业务场景如何从数据中提取事实并形成分析
  └─ Binding：把外部 CSV 文件和列名映射为场景使用的语义字段
```

#### SKILL：能力入口

`SKILL.md` 只保留触发条件、执行步骤、资源位置和必要边界，不放入九个场景的详细业务规则。这样可以保持入口精简，执行时再加载选中的报告和场景资源。

Phase 1 先实现一个“月度业绩分析”SKILL，并包含一个月度业绩报告模板。以后确实出现同一领域的其他报告时，可以在同一个 SKILL 中增加报告模板，共用场景和运行时；当前不提前实现多报告路由。

#### Scene：最小分析单元

每个场景独立描述：

- 场景名称和用途。
- 使用哪些语义字段。
- 全系统摘要展示哪些指标。
- 按哪些阈值筛选、按什么字段排序机构。
- 希望 LLM 表达的分析重点。

场景不关心 CSV 文件名、具体月份列名、报告中的前后位置和最终文件格式。

#### Binding：数据适配

Binding 描述：

- 场景使用哪个 CSV 文件。
- 哪一列是机构，如何识别“全系统”汇总行。
- 语义字段与实际 CSV 列的映射。

月份、季度等出现在物理列名中的内容通过运行参数替换。例如 `month_label=5月` 时，`{month_label}达成率` 解析为 `5月达成率`。无法参数化的列名直接写在 Binding 中，不把它们扩散到场景规则和执行代码。

#### Report：报告编排

报告模板描述：

- 报告标题和必要运行参数。
- 使用哪个 Binding。
- 选用哪些场景及顺序。
- 每个场景在报告中的标题和可选参数覆盖。
- 最终输出格式。

Phase 1 的输出先固定为 Markdown。报告模板负责章节和场景顺序，不描述 PDF 页坐标、颜色或复杂页面布局；后续需要 Word/PDF 时增加独立渲染器，不改变场景定义。

### 7.4 建议的 SKILL 目录

```text
monthly-performance-analysis/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── run_report.py
│   └── runtime/
└── references/
    ├── reports/
    │   └── monthly-performance.yaml
    ├── scenes/
    │   ├── standard-premium.yaml
    │   ├── value.yaml
    │   ├── active-manpower.yaml
    │   ├── sunshine-manpower.yaml
    │   ├── supervisor-activity.yaml
    │   ├── supervisor-double-star.yaml
    │   ├── standard-team.yaml
    │   ├── recruitment.yaml
    │   └── co-recruitment.yaml
    └── bindings/
        └── monthly-csv.yaml
```

不额外创建 README、安装说明、变更日志或重复的中文/英文规则副本。模板的解释信息直接写在对应 YAML 中。

### 7.5 Scene YAML v1

Scene DSL 只支持两类操作：

1. `summary`：读取全系统汇总行中的指定指标。
2. `select`：按一个或多个简单条件筛选机构，并按指定指标排序。

第一版不支持公式、任意表达式、场景依赖、条件分支和脚本嵌入。

下面以“标保”为示例。它体现分析逻辑，不要求逐字复刻现有五月报告：

```yaml
id: standard-premium
title: 标保
description: 分析月度标保达成、同比表现和年度进度，识别领先与落后机构。

parameters:
  high_rate: 70
  low_rate: 50
  high_year_progress: 60
  low_year_progress: 45

analysis:
  - id: overall
    type: summary
    fields:
      - month_amount
      - month_rate
      - month_yoy
      - year_amount
      - year_progress
      - year_yoy

  - id: high_month_rate
    type: select
    where: {field: month_rate, operator: ">", value: "${high_rate}"}
    order_by: {field: month_rate, direction: desc}

  - id: low_month_rate
    type: select
    where: {field: month_rate, operator: "<", value: "${low_rate}"}
    order_by: {field: month_rate, direction: asc}

  - id: ahead_year_progress
    type: select
    where: {field: year_progress, operator: ">", value: "${high_year_progress}"}
    order_by: {field: year_progress, direction: desc}

  - id: behind_year_progress
    type: select
    where: {field: year_progress, operator: "<", value: "${low_year_progress}"}
    order_by: {field: year_progress, direction: asc}

narrative:
  objective: 概括全系统标保表现，并指出月度达成和年度进度明显领先或落后的机构。
  style: 使用简洁的经营分析语言，先整体、后机构；只引用执行结果中的数字。
```

约定：

- `parameters` 是可在报告中覆盖的默认阈值，避免把阈值写死在执行代码中。
- `fields` 使用语义字段，不出现 `5月达成率` 等物理列名。
- `operator` 第一版只实现 `>`、`>=`、`<`、`<=`、`=` 和 `is_empty`。
- `narrative` 说明分析目标和写作要求，不保存现有报告原句。

### 7.6 Binding YAML v1

一个 Binding 文件可以集中保存当前报告九个场景的 CSV 映射：

```yaml
id: monthly-csv
type: csv
encoding: utf-8-sig

scenes:
  standard-premium:
    file: 标保.csv
    organization_column: 机构
    total_row: {column: 片区, value: 全系统}
    fields:
      month_amount: "{month_label}达成"
      month_rate: "{month_label}达成率"
      month_yoy: "{month_label}同比"
      year_amount: 全年标保达成
      year_progress: 全年进度
      year_yoy: 全年同比

  value:
    file: 价值.csv
    organization_column: 机构
    total_row: {column: 片区, value: 全系统}
    fields:
      month_amount: "{month_label}达成"
      month_rate: "{month_label}达成率"
      month_yoy: "{month_label}同比"
      year_progress: 全年进度
      year_yoy: 全年同比
```

其余七个场景按同样方式配置。运行时传入 `data_dir` 和 `month_label`，文件路径由 `data_dir + file` 得到；场景模板本身不保存 `docs` 路径。

数据加载只做必要的规范化：

- 去除表头和文本两侧空格。
- 将百分比和 `pt` 转为可比较的数值，同时保留原始展示值。
- 将 `-`、空字符串等识别为空值。
- 保留机构在源文件中的顺序，除非场景明确要求排序。

### 7.7 Report YAML v1

```yaml
id: monthly-performance
title: "{report_month}业绩分析报告"
binding: monthly-csv

parameters:
  report_month: 五月
  month_label: 5月
  cutoff_date: 5月31日

sections:
  - scene: standard-premium
  - scene: value
  - scene: active-manpower
  - scene: sunshine-manpower
  - scene: supervisor-activity
  - scene: supervisor-double-star
  - scene: standard-team
  - scene: recruitment
  - scene: co-recruitment

output:
  format: markdown
  heading_level: 1
```

当某份报告希望采用不同阈值时，只在对应场景引用处覆盖：

```yaml
sections:
  - scene: standard-premium
    parameters:
      high_rate: 75
      low_rate: 45
```

报告 YAML 不重复场景的指标、筛选规则和写作要求，也不保存 CSV 实际列名。

### 7.8 场景执行结果

场景执行引擎向 LLM 和报告组装器提供一个简单、统一的结果对象：

```yaml
scene_id: standard-premium
title: 标保
facts:
  overall: {}
  high_month_rate: []
  low_month_rate: []
  ahead_year_progress: []
  behind_year_progress: []
narrative: "..."
warnings: []
```

`facts` 由代码根据 YAML 和 CSV 确定性生成；`narrative` 由 LLM 根据 `facts + narrative` 要求生成。最终报告按 `sections` 顺序连接各场景的标题和 `narrative`，无需再让 LLM 对整份报告重新推理。

### 7.9 最小校验范围

只实现能防止明显错误的校验：

1. YAML 能正常解析，且 `id`、`title` 等必需字段存在。
2. 报告引用的 Scene 和 Binding 存在。
3. CSV 文件存在，场景使用的语义字段都有列映射，实际列能在 CSV 中找到。
4. 操作类型和比较符属于第一版支持范围。
5. LLM 生成内容前，必须有对应的场景执行结果；缺失数据不得交给 LLM 猜测。

Phase 1 暂不建设 JSON Schema 生成器、复杂类型系统、模板继承、版本依赖求解、依赖图、规则冲突检测和自动迁移工具。版本先保留在 Git 历史中，等模板进入数据库管理后再设计正式生命周期。

### 7.10 模板扩展方式

- **新增同类报告**：新建 Report YAML，组合已有场景。
- **新增业务场景**：新建 Scene YAML，并在 Binding 中增加数据映射。
- **更换月份或数据目录**：传入运行参数，不修改 Scene YAML。
- **更换字段名或数据来源**：修改或新增 Binding，不修改 Scene YAML。
- **调整分析阈值**：修改 Scene 默认参数，或在 Report 中局部覆盖。
- **调整章节顺序**：只修改 Report 的 `sections`。
- **调整文字风格**：修改 Scene 的 `narrative`，不修改筛选规则。

### 7.11 模板设计验收标准

1. 九个场景均可使用各自 CSV 独立执行并输出结构化事实。
2. 月度报告能按 Report YAML 顺序组合九个场景并生成 Markdown。
3. 把 `month_label` 从 `5月` 改为其他月份时，无需修改场景规则和执行代码。
4. 修改标保高低阈值或场景顺序时，无需修改执行代码。
5. 同一个场景可被另一份 Report YAML 引用，不复制场景规则。
6. 输出体现已有报告的分析逻辑，但不要求机构清单、句式和段落与五月报告逐字一致。

## 8. 最小模板样例（已完成）

已在 `template-analysis-agent-v6` 实现并验证：

1. `standard-premium` 和 `value` 两个 Scene YAML。
2. 包含标保、价值 CSV 映射的 `monthly-csv` Binding YAML。
3. 按“标保 → 价值”顺序编排两个场景的 Report YAML。
4. 能够读取三份 YAML、执行 `summary/select` 并输出结构化 facts 和 Markdown 的最小运行时。
5. 标准 SKILL 入口、命令行运行脚本和自动化测试。

验证结果：

- Skill 目录通过 `quick_validate.py` 校验。
- 12 项自动化测试通过，覆盖两个场景的真实汇总值、筛选顺序、阈值覆盖与严格边界、汇总行排除、同值稳定排序、月份参数化和必要失败路径。
- 使用 `docs/标保.csv` 端到端生成成功：月度高位 9 家、月度低位 8 家、年度领先 7 家、年度落后 6 家，无告警。
- 使用 `docs/价值.csv` 端到端生成成功：全系统价值达成 7045 万、达成率 74.1%，月度高位 5 家、月度低位 8 家，无告警。
- 独立前向执行与 CSV 逐项核对无差异，重复运行结果一致。

正式输出位于：

- `template-analysis-agent-v6/output/monthly-performance.facts.json`
- `template-analysis-agent-v6/output/monthly-performance.md`

## 9. Agent 执行引擎设计

### 9.1 设计目标与边界

Agent 执行引擎负责把用户自然语言需求转换成一次受控的报告模板执行，打通以下闭环：

```text
自然语言需求
  → 报告模板匹配
  → 参数抽取、规范化与补齐
  → 确定性执行 Report / Scene / Binding
  → CSV 读取、阈值筛选和排序
  → LLM 基于场景 facts 生成分析文字
  → 按 Report 顺序确定性组装报告
  → 返回报告与执行记录
```

这里的 Agent 是一个**受限的模板编排器**，不是自由探索型 ReAct Agent：

- 模型可以理解用户需求、选择已注册的 Report、抽取参数和撰写场景分析。
- 模型不能自由读取文件、执行 Shell、修改模板、生成 SQL、改变场景顺序或增加分析步骤。
- 所有业务数字、机构筛选和排序结果继续由 v6 确定性运行时产生。
- 最终报告由 Report 模板和代码组装，不调用“总报告 LLM”重新推理全部内容。
- Phase 1 只支持完整 Report 执行，不根据用户措辞临时裁剪或组合 Scene。

### 9.2 对 v5 `standalone_agent.py` 的取舍

v5 提供了可参考的 Agent 外壳，但不能原样迁移。

| v5 能力 | v6 决策 | 原因 |
|---|---|---|
| DeepSeek 模型初始化、超时和重试 | 保留思路，封装为 `LLMClient` | 隔离模型供应商，便于测试和替换 |
| `chat()` 和 `chat_stream()` | 保留接口形态 | 兼顾 CLI、Notebook 和未来前端 |
| 历史消息转换 | 保留，但只服务于澄清对话 | 执行状态由结构化 Session 保存 |
| token、工具开始/结束事件 | 保留事件流思想 | 改成稳定的业务事件，不暴露框架内部节点 |
| 扫描 SKILL 元数据 | 缩小为 Report Catalog | 路由对象是报告模板，不是任意工具 |
| `read_file` / `fetch_url` / `terminal` | 不提供给模型 | 当前闭环不需要宽权限工具 |
| `create_agent` + 自由工具循环 | 不采用 | Phase 1 路径固定，状态机更可控、可测 |
| 单文件承载全部能力 | 拆分职责 | 避免模型、路由、工具和会话再次耦合 |

v6 可以使用 LangChain 的模型适配器和结构化输出能力，但不把 LangChain Agent 循环作为业务编排核心。业务状态机属于本项目，框架只是可替换的模型调用层。

### 9.3 总体架构

```text
Chat / CLI / API
      │ AgentRequest(message, session_id, data_context_id)
      ▼
TemplateAnalysisAgent
      ├─ ReportCatalog         扫描可用 Report 的路由元数据
      ├─ IntentRouter          LLM 结构化选择 Report
      ├─ ParameterBinder       合并默认值、抽取值和澄清结果
      ├─ ReportExecutionTool   唯一业务工具，调用确定性运行时
      ├─ SceneAnalyzer         LLM 基于单场景 facts 撰写分析
      ├─ ReportAssembler       按 Report sections 组装 Markdown
      ├─ RunStore              保存运行产物和最小审计日志
      └─ EventEmitter          同步结果与流式业务事件
```

| 组件 | 核心职责 | 明确不负责 |
|---|---|---|
| ReportCatalog | 加载 Report 路由摘要 | 执行 Scene、读取 CSV |
| IntentRouter | 模板匹配、意图判断、参数抽取 | 自由规划分析步骤 |
| ParameterBinder | 参数默认值、规范化、补齐和澄清 | 访问 CSV、猜测缺失数据 |
| ReportExecutionTool | 调用现有 `execute_report()` 产生 facts | 调用 LLM、改变模板 |
| SceneAnalyzer | 基于单场景 compact facts 生成文字 | 读取 CSV、计算新指标 |
| ReportAssembler | 按 Report 顺序渲染最终报告 | 重排场景、重新推理数字 |
| RunStore | 保存状态、事件和产物 | 参与业务判断 |

### 9.4 显式状态机

Phase 1 的分析路径已经由模板确定，不需要模型反复决定“下一步调用什么”。状态只允许由引擎代码推进：

```text
RECEIVED
  → ROUTING
    ├─ NEEDS_CLARIFICATION
    ├─ UNSUPPORTED
    └─ PARAMETERS_BOUND
         → EXECUTING_FACTS
           ├─ FAILED
           └─ ANALYZING_SCENES
                → ASSEMBLING_REPORT
                  ├─ COMPLETED
                  └─ COMPLETED_WITH_WARNINGS
```

LLM 返回结构化路由决策或场景文字，不能自行跳转状态、循环调用工具或发起未声明的操作。

### 9.5 Report Catalog 与模板匹配

#### Report 路由元数据

在现有 Report YAML 中增加少量元数据，不建立独立意图库：

```yaml
id: monthly-performance
title: "{report_month}业绩分析报告"

routing:
  scenario_desc: 基于月度经营 CSV 生成包含标保、价值等场景的业绩分析报告。
  intent_examples:
    - 生成五月业绩分析报告
    - 看一下本月经营情况
    - 做一份月度经营复盘

parameters:
  report_month: 五月
  month_label: 5月

params_schema:
  report_month:
    type: string
    required: false
    description: 报告标题使用的中文月份，例如“五月”。
  month_label:
    type: string
    required: false
    description: CSV 列名使用的月份前缀，例如“5月”。
```

`parameters` 继续保存运行默认值；`params_schema` 只服务于路由、抽参和澄清，不改变 Scene DSL。

ReportCatalog 启动时只读取 `id`、`title`、`routing`、`parameters`、`params_schema` 和 `sections` 中的 Scene ID，不把完整 Scene、Binding 或 CSV 放进路由 Prompt。模板少于 30 个时，一次性把 Report 摘要交给 LLM；规模增长后再考虑检索，Phase 1 不引入向量库。

#### 路由结构化输出

```yaml
action: execute | clarify | unsupported
report_id: monthly-performance | null
confidence: 0.0-1.0
extracted_params: {}
missing_params: []
clarification: null
reason: 简短可审计原因
```

路由规则：

1. 只能从 Catalog 候选中选择 `report_id`，不能生成新 ID。
2. `confidence >= 0.7` 且参数可绑定时进入执行。
3. 置信度不足或多个模板含义接近时返回 `clarify`。
4. 与所有模板无关的请求返回 `unsupported`，不能勉强套用月报模板。
5. v1 只做 Report 级路由。用户要求“只分析价值”时，如果没有对应 Report 模板，应说明当前仅支持完整月报，不动态裁剪 `sections`。

### 9.6 参数绑定

ParameterBinder 按固定优先级合并：

```text
Report 默认 parameters
  < 当前 Session 已确认参数
  < 本轮用户明确参数
```

月份由集中式规范化函数处理，避免让 LLM 分别生成两个可能矛盾的字段：

```text
“五月” / “5月” / “2026年5月”
  → report_month = 五月
  → month_label = 5月
```

Phase 1 只实现当前模板实际使用的月份参数，不建设通用日期表达式语言：

- 用户没有指定月份时使用 Report 默认值。
- 用户明确指定月份时同时覆盖 `report_month` 和 `month_label`。
- 用户输入互相冲突的月份信息时必须澄清。
- 必填参数无默认值且无法抽取时进入 `NEEDS_CLARIFICATION`。
- 参数名称必须来自 `params_schema`；拒绝模型生成的未知参数。
- `data_dir` 不属于业务参数，由应用以可信 `data_context_id` 提供，不能由 LLM 构造本地路径。

如果用户请求六月而当前数据只有 `5月达成率` 等列，确定性运行时应正常报缺列。Agent 将其转换为“当前数据源不包含六月字段”，不回退到五月，也不编造六月报告。

### 9.7 唯一业务工具：ReportExecutionTool

Agent 不获得 `terminal`、通用 `read_file` 或任意 Python 执行能力，只注册一个领域工具：

```python
execute_report(
    report_id: str,
    parameters: dict[str, str],
    data_context_id: str,
) -> DeterministicReportResult
```

工具内部：

1. 用 `report_id` 从 Catalog 解析真实 Report 路径，拒绝任意路径输入。
2. 用 `data_context_id` 解析应用已授权的数据目录。
3. 只允许传入 `params_schema` 中声明的参数。
4. 调用 v6 已有 `execute_report()` 生成 facts 和 baseline narrative。
5. 返回结构化对象，不让模型解析 Shell 文本。

CLI 中 `data_context_id` 可以对应 `--data-dir docs`；未来 Web 中对应一次上传或已授权的数据集。自然语言即使包含路径，也必须先经过应用层解析和目录边界检查。

### 9.8 场景分析

当前报告文字由确定性代码生成。接入 Agent 后，确定性文字保留为 fallback，正常路径由 SceneAnalyzer 为每个场景生成更自然的分析。

#### 模型输入

SceneAnalyzer 每次只接收一个场景：

- Scene 的 `title`、`description`、`metrics` 和 `narrative`。
- 该 Scene 已执行完成的 compact facts。
- Report 的必要上下文，例如报告月份。

不传入原始 CSV 全表、无关 Scene、SKILL 全文或运行时代码。compact facts 只保留：

- `summary` 的字段名、`display` 和规范化 `value`。
- `select` 的 action 标题、机构名和展示字段。

完整 facts 保存到运行记录，但不全部进入模型上下文。

#### 场景分析结构化输出

```yaml
scene_id: standard-premium
content: |
  - 全系统……
  - 月度达成率领先机构……
used_fact_ids:
  - overall
  - high_month_rate
  - low_month_rate
warnings: []
```

Prompt 约束：

1. 只能使用输入 facts，不得新增机构、数字、指标或因果解释。
2. 不重新计算达成率、同比或排名。
3. 数字采用 facts 中的 `display`，正负方向必须与原值一致。
4. 可以改变句式和侧重点，不要求逐字复制五月报告。
5. `used_fact_ids` 只能引用当前场景真实存在的 action ID。

#### 最小验证与降级

- `scene_id` 必须与当前 Scene 一致。
- `content` 不能为空。
- `used_fact_ids` 必须存在于当前 facts。
- 输出不能包含 compact facts 中不存在的机构名。
- 模型调用或结构化输出失败时重试一次。
- 重试仍失败时使用确定性 baseline narrative，状态记为 `COMPLETED_WITH_WARNINGS`。

第一版暂不实现脆弱的全文数字正则审计；先用结构化输入输出、低温度和 fallback 保证可靠性，发现真实错误样例后再加针对性校验。

### 9.9 报告组装

ReportAssembler 不调用 LLM，严格按照 Report `sections` 顺序执行：

```text
报告标题
  → 标保标题 + 标保 narrative
  → 价值标题 + 价值 narrative
  → 后续场景……
```

复用现有 `render_markdown()`，仅将 baseline narrative 替换为验证通过的 LLM narrative：

- 场景不会被遗漏、增加或重排。
- LLM 不会在综合阶段篡改已经确定的数字。
- 单场景模型失败仍可使用 baseline 完成报告。

Phase 1 不生成跨场景总评、贺报或红黄蓝评价；以后需要时必须设计为显式 Scene 或 Report 模块。

### 9.10 Agent 请求、响应与 Session

#### 请求

```yaml
message: 请基于当前数据生成五月业绩分析报告
session_id: optional-session-id
data_context_id: current-upload
```

#### 最终响应

```yaml
status: completed | completed_with_warnings | needs_input | unsupported | failed
message: 给用户的简短说明或澄清问题
run_id: 20260812-...
report_id: monthly-performance
parameters:
  report_month: 五月
  month_label: 5月
facts_path: .../facts.json
report_path: .../report.md
warnings: []
```

Session 只保存执行需要的结构化状态：上一轮 `report_id`、已确认参数、待补充参数和 `data_context_id`。上一轮处于 `NEEDS_CLARIFICATION` 时，下一条消息优先补参数，不重新路由。Phase 1 可先用进程内 Session Store，数据库持久化留到对话服务阶段。

### 9.11 同步与流式接口

保留 v5 易用的接口形态：

```python
result = agent.chat(message, session_id=None, data_context_id="docs")

async for event in agent.chat_stream(message, session_id, data_context_id):
    ...
```

流式事件使用稳定的业务类型：

```text
request_received
route_started
route_selected | clarification_required | unsupported
parameters_bound
report_execution_started
scene_facts_ready
scene_analysis_started
scene_analysis_ready | scene_analysis_fallback
report_ready
failed
done
```

事件只包含可展示状态、Scene ID、耗时、warning 和产物路径，不转发 LangChain 内部节点、完整 Prompt、API Key 或大段工具输出。同步和流式必须调用同一个核心状态机；`chat()` 只是收集事件并返回最终结果。

### 9.12 LLM 适配层

初始实现可以继续使用 v5 的 DeepSeek 配置：

- `DEEPSEEK_API_KEY`
- 默认 base URL：`https://api.deepseek.com`
- 默认模型：`deepseek-v4-flash`

业务代码只依赖内部接口：

```python
class LLMClient:
    def route(request, candidates) -> RouteDecision: ...
    def analyze_scene(scene_context) -> SceneNarrativeResult: ...
```

建议参数：

- Router：`temperature=0`，强制结构化输出。
- SceneAnalyzer：`temperature=0.1~0.2`，强制结构化输出。
- 单次超时 60–120 秒；传输失败重试一次，业务验证失败修复一次。
- 自动化测试使用 `FakeLLMClient`，默认不访问真实模型。

不要把 DeepSeek 类、LangChain 消息类型或框架事件扩散到 Router、Engine、RunStore 等业务模块。

### 9.13 运行产物与追溯

Phase 1 不引入数据库，每次执行建立轻量 run 目录：

```text
runs/<run_id>/
├── request.json
├── route.json
├── facts.json
├── narratives.json
├── report.md
└── events.jsonl
```

保存原始请求、路由和置信度、绑定参数、确定性 facts、场景是否使用 fallback、模型名称、耗时、token 用量（如供应商提供）、错误摘要和最终报告。

不保存 API Key、模型思维链或不必要的完整 Prompt。CSV 保留在外部数据目录，不复制进 run 目录。

### 9.14 错误处理

| 错误 | 处理 |
|---|---|
| 没有匹配模板 | 返回 `unsupported` 或澄清，不执行 CSV |
| 路由结构化输出失败 | 重试一次，仍失败则 `failed` |
| 缺少业务参数 | 返回 `needs_input` 并保存 pending Session |
| 缺少 data context | 要求用户选择或上传数据，不猜路径 |
| CSV 文件或字段缺失 | 确定性执行失败，不进入 SceneAnalyzer |
| 某个 Scene 规则执行失败 | 整份 Report 失败，不交付不完整报告 |
| Scene LLM 超时或输出非法 | 重试一次，再使用 baseline narrative |
| 报告写入失败 | 返回 `failed`，保留审计记录 |

原则是：**facts 失败则整份失败，narrative 失败则允许降级**。前者影响事实完整性，后者只影响表达质量。

### 9.15 建议代码结构

Agent 层放在 v6 项目根目录，不塞进 SKILL 的确定性运行时：

```text
template-analysis-agent-v6/
├── standalone_agent.py          # CLI / Notebook 薄入口
├── agent/
│   ├── models.py                # RouteDecision、AgentState、AgentResult、事件
│   ├── catalog.py               # Report Catalog
│   ├── router.py                # IntentRouter、ParameterBinder
│   ├── llm.py                   # DeepSeekLLMClient、FakeLLMClient
│   ├── analyzer.py              # compact facts、SceneAnalyzer、fallback
│   ├── engine.py                # 唯一核心状态机
│   └── store.py                 # Session Store、Run Store
├── skills/
│   └── monthly-performance-analysis/
│       ├── SKILL.md
│       ├── references/
│       └── scripts/monthly_analysis/   # 现有确定性运行时
└── tests/
    ├── test_minimal_runtime.py
    ├── test_router.py
    ├── test_agent_engine.py
    └── test_agent_e2e.py
```

`standalone_agent.py` 只负责读取配置、构造依赖以及暴露 `chat/chat_stream`，不再定义文件、网络、终端工具或业务逻辑。

### 9.16 关键接口草案

```python
@dataclass
class AgentRequest:
    message: str
    data_context_id: str | None
    session_id: str | None = None

@dataclass
class AgentResult:
    status: str
    message: str
    run_id: str
    report_id: str | None = None
    parameters: dict[str, str] = field(default_factory=dict)
    facts_path: str | None = None
    report_path: str | None = None
    warnings: list[str] = field(default_factory=list)

class TemplateAnalysisAgent:
    def chat(self, request: AgentRequest) -> AgentResult: ...
    async def chat_stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]: ...
```

核心执行伪代码：

```python
route = router.route(request.message, catalog.summaries())
if route.action != "execute":
    return non_execution_result(route)

bound = parameter_binder.bind(route, catalog.get(route.report_id), session)
if bound.missing:
    return clarification_result(bound)

facts_result = report_tool.execute(
    report_id=route.report_id,
    parameters=bound.values,
    data_context_id=request.data_context_id,
)

for scene in facts_result.scenes:
    compact = compact_facts(scene)
    narrative = scene_analyzer.analyze(scene, compact)
    scene.narrative = narrative.content if narrative.valid else scene.baseline_narrative

report = report_assembler.render(facts_result)
return completed_result(report)
```

### 9.17 验收测试


 `DEEPSEEK_API_KEY` 设置在.env中，默认测试套件不访问外部模型：

1. 输入“请基于当前数据生成五月业绩分析报告”。
2. 路由到 `monthly-performance`，使用 `docs` 数据上下文。
3. 标保与价值 facts 必须与现有 12 项确定性测试一致。
4. 两个 Scene 均生成非空分析文字，且不出现 facts 外的机构。
5. 最终报告顺序正确、文件存在，状态为 `completed` 或带明确 warning 的降级完成。

### 9.18 实施顺序

1. **扩展 Report 元数据**：增加 `routing` 和 `params_schema`，不改 Scene DSL。
2. **建立契约模型**：实现 `AgentRequest`、`RouteDecision`、`AgentState`、`AgentResult` 和事件。
3. **实现 Catalog、Router 和 Binder**：先用 Fake LLM 完成路由及澄清测试。
4. **封装 ReportExecutionTool**：复用 v6 `execute_report()`，禁止任意路径和未知参数。
5. **实现 compact facts 与 SceneAnalyzer**：加入一次修复和 baseline 降级。
6. **实现核心状态机与 RunStore**：同步接口先行，再由同一事件生成器提供流式接口。
7. **编写 `standalone_agent.py` 薄入口**：接入 DeepSeek 配置和命令行数据上下文。
8. **完成真实模型冒烟测试**：验证自然语言入口到 Markdown 的完整闭环。

Agent 引擎闭环验证后再迁移“活动人力”等 Scene。这样可以先证明 Agent 与 Scene 数量无关，后续新增场景只扩充模板和确定性测试，不修改 Agent 核心。

### 9.19 当前实现状态（2026-08-12）

`template-analysis-agent-v6` 已完成 Phase 1 Agent 模块：

- Report 已增加 `routing` 与 `params_schema`，Scene DSL 和 Binding 保持不变。
- `agent/` 已实现契约模型、Catalog、Router/Binder、唯一 Report 工具、SceneAnalyzer、Assembler、Session/Run Store 和显式状态机。
- `standalone_agent.py` 已提供同步 `chat()`、异步 `chat_stream()`、DeepSeek 适配和离线模式；模型没有终端、任意文件或网络工具。
- 每次成功运行保存 `request.json`、`route.json`、`facts.json`、`narratives.json`、`report.md` 和 `events.jsonl`。
- 离线自动化覆盖自然语言路由、参数规范化、两场景 facts、Session 续跑、缺列失败、单场景拒绝、模型重试/fallback 和流式事件；真实 DeepSeek 冒烟由 `RUN_REAL_LLM_SMOKE=1` 显式开启。
- 已用不含 CSV 的合成请求验证 DeepSeek 路由适配器连接；包含经营 facts 的真实场景冒烟需在明确允许数据发送给外部模型后执行。

当前实现边界不变：facts 失败则整份失败；场景文案失败可降级；Agent 不动态裁剪 Report 的 Scene。

## 10. 报告格式稳定化方案（2026-08-13 补充）

### 10.1 问题现象与根因

本次在线 DeepSeek 运行产物为：

- 参考报告：`docs/五月业绩分析报告.md`
- 实际报告：`template-analysis-agent-v6/runs/20260813-011236-743-a514b006/report.md`

标保场景中，两个文件表达的是同一组筛选事实，但展示契约不同：

```text
参考：9家机构5月标保达成率高于 70% ：河南、天津、潍坊、云南、新疆、海南、内蒙古、宁波、甘肃；
实际：月度标保达成率领先机构：新疆176.3%、天津107.4%、潍坊94.7%、云南91.4%、海南79.7%、河南78.7%、内蒙古74.1%、宁波73.8%、甘肃72.8%。
```

这不是 facts 计算错误，而是当前链路只稳定了“选出哪些机构”，没有稳定“如何把事实渲染成报告”。具体原因如下：

1. `DeepSeekLLMClient.analyze_scene()` 的结构化输出中，`content` 仍是一段自由 Markdown 字符串。JSON 外壳只能保证字段存在，不能约束句式、标点、名单样式和分句方式。
2. 当前 Scene Prompt 明确允许模型改变句式和侧重点；`narrative.style` 只写了“简洁、先整体后机构”，没有定义家数、阈值、是否显示逐机构数值、空格和标点。
3. `SceneAnalyzer._validate()` 只校验内容非空、fact ID 和一部分“机构（数字）”写法，不校验家数、阈值、机构顺序、每个 action 是否完整呈现，也无法拦截“新疆176.3%”这种不带括号的格式。
4. `standard-premium.yaml` 当前对高低机构按指标值排序。实际输出因此按达成率降序排列；参考报告中的机构名单使用 CSV 源顺序。即使 Prompt 完全不变，模型也无法从已排序的 compact facts 恢复源顺序。
5. 参考报告中的“其中天津、新疆达成目标”“其中深圳达成率不足 5%”属于二级规则，但当前标保 Scene 没有对应的 `target_met`、`extremely_low` action。不能要求模型从高低名单里自行推导，否则每次是否补充这些话都不稳定。
6. `temperature=0` 或 `0.1` 只能降低随机性，不能把自然语言生成变成确定性排版；更换模型或模型版本后仍可能漂移。

因此，格式稳定化的基本原则是：

> facts 由规则引擎确定，事实正文由展示模板和代码确定性渲染；LLM 只生成不影响固定版式的可选评价，不能再直接生成事实段落的最终 Markdown。

### 10.2 目标格式契约

以 `docs/五月业绩分析报告.md` 为风格基线，但不盲目复制参考文件中可能存在的历史遗漏。业务事实仍以当前 CSV、Scene 阈值和执行结果为唯一依据；若参考报告的机构数与规则执行结果不一致，必须先确认业务规则，再通过 Scene 显式修改，不能在渲染阶段删数据。

稳定模式至少要固定以下维度：

| 维度 | 稳定模式约定 |
|---|---|
| 章节 | 按 Report `sections` 顺序，每个 Scene 一个固定标题 |
| 标题级别 | 由 Report 输出配置固定，不由模型决定 |
| 段落 | 每个 Scene 的 bullet 数量、顺序和 action 组合由 Scene 展示模板决定 |
| 选择句 | 固定输出“家数 + 月份/指标 + 比较符语义 + 阈值 + 机构名单” |
| 机构名单 | 默认只显示机构名，使用 `、` 分隔，不逐机构显示指标值 |
| 名单顺序 | 默认保持 CSV 源顺序；只有模板明确要求时才按指标升降序 |
| 数字 | 只使用 facts 的 `display`；百分比、`pt`、万、人等由确定性 formatter 输出 |
| 同比措辞 | 根据数值符号确定性转换为“同比正增/同比负增”，不由模型自由改写 |
| 家数 | 由 `len(fact_rows)` 自动生成，禁止模型填写 |
| 标点 | 中文冒号、顿号、分号、句号及其空格规则由 style profile 固定 |
| 空结果 | 使用模板声明的固定文案，例如“暂无符合条件的机构” |

报告级输出配置建议扩展为：

```yaml
output:
  format: markdown
  render_mode: deterministic
  style_profile: may-performance-v1
  include_report_title: false
  scene_heading_level: 1
  bullet_marker: "-"
```

`include_report_title: false`、一级 Scene 标题可使结构接近当前参考报告；如果产品需要保留“五月业绩分析报告”总标题，则建立另一个 style profile，而不是让模型临场决定。

### 10.3 增加窄范围 Presentation DSL

Scene 的 `analysis` 继续只定义事实计算；新增 `presentation` 只定义 facts 如何展示。第一版不实现任意表达式，只支持白名单占位符和 formatter：

- `{param.xxx}`：Report/Scene 参数。
- `{summary.<fact_id>.<field>}`：汇总事实的展示值。
- `{count(<fact_id>)}`：选择结果家数。
- `{organizations(<fact_id>)}`：按 fact 当前顺序输出机构名。
- `{trend(<fact_id>.<field>)}`：将正负数确定性渲染为“正增/负增 + 绝对值”。
- `{values(<fact_id>, mode=organization_value)}`：仅在模板明确要求时输出“机构+数值”。

标保场景建议改成下面的形式。示例展示的是设计方向，实施时应为占位符建立正式解析器和启动期校验，不能使用 Python `eval` 或把模板再次交给 LLM：

```yaml
parameters:
  high_rate: 70
  target_rate: 100
  low_rate: 50
  extremely_low_rate: 5
  high_year_progress: 60
  exceptional_year_progress: 80
  low_year_progress: 45

analysis:
  - id: high_month_rate
    type: select
    where: {field: month_rate, operator: ">", value: "${high_rate}"}
    # 不配置 order_by，保持 CSV 源顺序
    display_field: month_rate

  - id: month_target_met
    type: select
    where: {field: month_rate, operator: ">=", value: "${target_rate}"}
    display_field: month_rate

  - id: extremely_low_month_rate
    type: select
    where: {field: month_rate, operator: "<", value: "${extremely_low_rate}"}
    display_field: month_rate

  - id: exceptional_year_progress
    type: select
    where: {field: year_progress, operator: ">", value: "${exceptional_year_progress}"}
    display_field: year_progress

presentation:
  bullets:
    - template: >-
        截至{param.cutoff_date}，全系统{param.month_label}标保共达成{summary.overall.month_amount}万，
        达成率 {summary.overall.month_rate}，同比{trend(overall.month_yoy)}，
        全年达成{summary.overall.year_amount}万，全年进度 {summary.overall.year_progress}，
        全年标保同比{trend(overall.year_yoy)}。
    - clauses:
        - template: >-
            {count(high_month_rate)}家机构{param.month_label}标保达成率高于 {param.high_rate}% ：
            {organizations(high_month_rate)}
        - when: month_target_met is not empty
          template: >-
            其中{organizations(month_target_met)}达成{param.month_label}标保目标
        - template: >-
            {count(low_month_rate)}家机构{param.month_label}标保达成率低于 {param.low_rate}% ：
            {organizations(low_month_rate)}
        - when: extremely_low_month_rate is not empty
          template: >-
            其中{organizations(extremely_low_month_rate)}达成率不足 {param.extremely_low_rate}%
      clause_separator: "；"
      end: "；"
```

稳定渲染后，标保高位名单应得到：

```text
9家机构5月标保达成率高于 70% ：河南、天津、潍坊、云南、新疆、海南、内蒙古、宁波、甘肃；
```

这里必须同时落实两个配置：

1. `high_month_rate` 不按达成率排序，保留 CSV 源顺序。
2. `organizations()` 使用 `organization_only` 展示模式，不拼接每家机构的百分比。

如果其他报告确实需要“新疆176.3%、天津107.4%”这种榜单格式，应显式配置 `values(..., mode=organization_value)` 和 `order_by`，不能由同一个 Prompt 猜测使用哪种格式。

### 10.4 LLM 在稳定模式中的职责调整

推荐默认路径：

```text
CSV
  → 确定性 facts
  → Presentation DSL 确定性渲染事实 bullets
  → 格式与事实校验
  → ReportAssembler 确定性组装 report.md
```

在线 DeepSeek 仍可用于：

1. 路由用户意图和抽取月份参数。
2. 在明确的独立区块生成“经营评价/建议”，但该区块不能复述或重排机构清单。
3. 从候选 fact ID 中选择关注点；最终文字仍由模板渲染。

稳定模式下，不再让模型返回自由的 `content` 作为 Scene 最终正文。若必须保留模型润色，可采用受限的结构化输出，例如只返回：

```json
{
  "scene_id": "standard-premium",
  "emphasis_fact_ids": ["high_month_rate", "low_month_rate"],
  "optional_comment": "月度达成分化较明显"
}
```

其中 `optional_comment` 单独展示且不得包含数字和机构名；模型返回非法内容时直接丢弃，不影响固定事实正文。

短期尚未完成确定性渲染器时，可以先做以下临时缓解，但不能把它视为最终保证：

1. 将 SceneAnalyzer 温度改为 `0`。
2. Prompt 提供一条完整目标示例，并明确“必须显示家数和阈值、名单只显示机构名、不得给机构附数值、不得改变输入顺序”。
3. 把自由 `content` 改为 `lines[]`，每行包含 `template_id`、`fact_ids`、`organization_display_mode`，再由代码拼接 Markdown。
4. 补充严格验证，验证失败则使用确定性 baseline。

临时方案仍无法解决 compact facts 已经按数值排序、缺少二级 action 等信息损失，所以必须与 Scene 规则修正一起实施。

### 10.5 格式与事实双重校验

新增 `ReportValidator`，在写入 `report.md` 前校验以下内容：

1. 标题数量、层级和 Scene 顺序与 Report 完全一致。
2. 每个 presentation bullet 引用的 fact ID 存在，所有占位符均已绑定。
3. `{count(...)}` 与对应机构数组长度一致。
4. `{organizations(...)}` 的机构集合和顺序与 facts 完全一致。
5. `organization_only` 模式下，机构名后不得出现百分比或数值。
6. 阈值、月份和截止日期只能来自已绑定参数。
7. 所有报告数字都能回溯到 facts；不得出现 facts 外的数字和机构。
8. presentation 声明为必显的 action 不得遗漏或重复。
9. 标点、bullet marker、空行和句末符号符合 style profile。

确定性渲染或校验失败时，整份报告状态应为 `failed`，因为此时是模板/代码错误；不能回退到模型自由生成。只有可选 LLM 评价失败时，才允许 `completed_with_warnings`。

建议在 run 目录增加：

```text
runs/<run_id>/
├── facts.json
├── rendered-scenes.json
├── validation.json
└── report.md
```

`rendered-scenes.json` 保存每个 bullet 使用的 template ID、fact ID 和最终文本，`validation.json` 保存格式校验结果，便于定位“事实错”还是“展示错”。

### 10.6 测试策略

测试应分为四层：

1. **规则测试**：继续验证阈值边界、机构集合、源顺序和汇总行排除。
2. **渲染单测**：验证家数、阈值、名单显示模式、趋势措辞、空结果和标点。
3. **黄金文件测试**：新增人工审核后的 `tests/golden/monthly-performance-may.md`，对确定性输出做全文 snapshot 对比。黄金文件可以参考 `docs/五月业绩分析报告.md` 的样式，但应按当前规则重新生成并审核，避免固化参考报告中的历史遗漏。
4. **在线重复性测试**：相同输入连续运行至少 5 次；只要路由参数相同，事实正文的 SHA-256 必须一致。模型版本变化不得改变固定正文。

标保场景至少增加以下精确断言：

```python
expected = (
    "9家机构5月标保达成率高于 70% ："
    "河南、天津、潍坊、云南、新疆、海南、内蒙古、宁波、甘肃；"
)
self.assertIn(expected, markdown)
self.assertNotIn("新疆176.3%", markdown)
```

同时增加语义断言，避免 snapshot 只保证“长得一样”却事实错误：

- 高于 `70%` 的机构必须恰好 9 家。
- `70%` 边界按 `>` 执行，等于 `70%` 不应入选。
- 机构名单必须与 `facts.high_month_rate` 完全一致。
- `month_target_met` 必须由 `>=100%` 独立筛选，不能由模型从文字推导。

### 10.7 实施顺序与验收标准

按以下顺序实施，避免先调 Prompt 后反复返工：

1. 从 `docs/五月业绩分析报告.md` 提取并人工确认 `may-performance-v1` 样式规范，确认是否保留报告总标题。
2. 为 Report 增加 `render_mode`、`style_profile`、标题层级和标点配置。
3. 为 Scene 增加最小 `presentation` DSL，并实现安全占位符解析器。
4. 调整需要参考顺序的 select：删除数值 `order_by` 或显式增加 `order_by: source`。
5. 将“达成目标、极低、超额”等需要写进正文的二级结论全部建成独立 action。
6. 实现确定性 SceneRenderer 和趋势/名单 formatter，替换当前自由 `content` 正文路径。
7. 实现 ReportValidator 和 run 审计产物。
8. 建立渲染单测、语义测试和人工审核后的黄金文件。
9. 最后再决定是否保留 DeepSeek 的可选评价区块；稳定事实正文不得依赖该模型调用。

完成标准：

1. 同一 facts 重复渲染得到逐字节一致的 `report.md`。
2. 在线和离线模式在相同参数下的事实正文一致。
3. 标保示例稳定输出“9家机构 + 高于 70% + 仅机构名单 + CSV 源顺序”。
4. 修改月份、阈值或 style profile 只需改参数/模板，不修改 Agent 主流程。
5. 报告中的家数、机构、阈值和数字全部可由 `validation.json` 回溯到 facts。
6. 更换 DeepSeek 模型或升级模型版本不会改变固定事实段落的格式。
