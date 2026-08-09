# 智能经营分析 Agent · Phase 1 详细设计方案

> 开发级技术设计 —— 面向工程团队的可执行方案

| 项目 | 内容 |
|------|------|
| 版本 | v3.0 · 2026-07-16 |
| 范围 | Phase 1 模板驱动分析 Agent |
| 前置文档 | 智能经营分析Agent_产品规划与设计.md |
| v3.0 变更 | 吸收参考方案3条优势（calculations预计算+query_cache+轻量监控），拒绝3条过度设计（TF-IDF路由/Jinja2结论/Celery），融合1条（Phase3 LangGraph编排底座） |

---

## 目录

1. [技术选型与系统架构](#一技术选型与系统架构)
2. [数据库详细设计（完整 DDL）](#二数据库详细设计完整-ddl)
3. [模板 DSL 规范](#三模板-dsl-规范)
4. [数据查询工具层设计](#四数据查询工具层设计)
5. [意图路由模块设计](#五意图路由模块设计)
6. [Agent 执行引擎详细设计](#六agent-执行引擎详细设计)
7. [API 接口设计](#七api-接口设计)
8. [前端交互设计](#八前端交互设计)
9. [开发任务拆解（WBS）](#九开发任务拆解wbs)
10. [Phase 2/3 技术演进指引](#十phase-23-技术演进指引)
11. [附录 B：v3.0 设计决策说明](#附录-bv30-设计决策说明)

---

## 一、技术选型与系统架构

### 1.1 系统分层架构

系统采用经典五层架构，自上而下依次为：

```
用户
  → 前端层 (Vue 3 SPA)
    对话窗口 / 流式进度 / 报告展示 / 模板管理后台
      ↓ HTTP / SSE
  → Nginx
    → 应用服务层 (FastAPI)
      对话服务 · 执行引擎 · 意图路由 · 模板管理 · 查询管理 · 报告管理
        ↓
  → 工具层
    数据查询工具注册中心 (ToolRegistry)
    API 适配器 · SQL 适配器 · 函数适配器 · (Phase 3: MCP Server)
        ↓
  → 数据层
    PostgreSQL (模板/查询/日志/会话/缓存)
    + Redis (会话/缓存)
    + LLM 服务 (OpenAI 兼容 API)
        ↓
  → 外部源
    现有 BI 系统 / 业务数据库 / 数据 API / 数据仓库
```

### 

---

## 二、数据库详细设计（完整 DDL）

### 2.1 ER 关系概览

```
template  1 ── N  execution_log（一个模板被多次执行）
data_query  N ── N  template（通过 template.steps.data_query_ids 关联，逻辑外键）
chat_session  1 ── N  execution_log（一个会话可触发多次分析）
execution_log  独立存储：steps_executed（含每步取数结果+结论）、final_report
```

### 2.2 完整 DDL（PostgreSQL）

```sql
-- ============================================================
-- 1. 模板表
-- ============================================================
CREATE TABLE template (
    id                   VARCHAR(64) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name                 VARCHAR(200) NOT NULL,
    scenario_desc        TEXT NOT NULL,           -- 适用场景描述（供意图路由匹配）
    intent_examples      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 示例问法数组
    params_schema        JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 参数定义
    playbook             TEXT,                        -- 自然语言剧本全文
    steps                JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 结构化步骤（核心）
    referenced_query_ids JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 引用的查询ID列表（冗余）
    output_template      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 报告章节大纲
    version              INTEGER NOT NULL DEFAULT 1,
    status               VARCHAR(20) NOT NULL DEFAULT 'draft',  -- draft / active / deprecated
    tags                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by           VARCHAR(64),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 2. 数据查询表
-- ============================================================
CREATE TABLE data_query (
    id              VARCHAR(64) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    code            VARCHAR(100) UNIQUE NOT NULL,      -- 业务编码，如 Q_MONTHLY_REVENUE
    name            VARCHAR(200) NOT NULL,
    description     TEXT NOT NULL,                  -- 给 LLM 看的自然语言说明
    category        VARCHAR(50) NOT NULL,             -- 财务/销售/库存/用户/...
    params_schema   JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 入参定义
    return_schema   JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 返回字段及类型
    example         JSONB,                           -- 示例返回数据
    handler_type    VARCHAR(20) NOT NULL DEFAULT 'api',  -- api / sql / function
    handler_config  JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 调用配置（见 4.3）
    timeout_ms      INTEGER NOT NULL DEFAULT 30000,
    cache_ttl       INTEGER NOT NULL DEFAULT 0,       -- 缓存秒数，0=不缓存
    status          VARCHAR(20) NOT NULL DEFAULT 'active',  -- active / inactive
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


```



### 2.3 种子数据示例（data_query 表）

```sql
INSERT INTO data_query (code, name, description, category, params_schema, return_schema, example, handler_type, handler_config, timeout_ms, cache_ttl, status)
VALUES (
  'Q_MONTHLY_REVENUE',
  '月度营收查询',
  '查询指定时间范围、业务单元的月度营收数据，含同比环比',
  '财务',
  '[{"name":"time_range","type":"string","required":true,"description":"YYYY-MM格式，如2026-07"},
     {"name":"business_unit","type":"string","required":false,"description":"业务单元，如华东/华南，默认全部"}]'::jsonb,
  '{"type":"array","items":{"type":"object","properties":{"month":{"type":"string"},"revenue":{"type":"number"},
     "yoy":{"type":"number"},"mom":{"type":"number"}}}}'::jsonb,
  '[{"month":"2026-07","revenue":12000000,"yoy":0.08,"mom":0.05}]'::jsonb,
  'api',
  '{"url":"http://bi-internal/api/revenue/monthly","method":"GET","param_mapping":{"time_range":"month","business_unit":"bu"},"response_path":"data"}'::jsonb,
  30000, 300, 'active'
);
```

---

## 三、模板 规范

模板是整个系统的"程序"——它用结构化 JSON 描述了一篇经营分析报告的完整生成逻辑。引擎是解释器，模板是脚本。

### 3.1 模板生命周期与状态流转

```
[draft] 新建/编辑中 → 人工审核通过 → [active]
[active] 被新版替代或弃用 → [deprecated]
[draft] / [deprecated] 不参与意图路由匹配，仅 [active] 状态参与
```

> **版本管理：** 每次修改 active 模板会创建新版本（version + 1），旧版本保留可回溯。执行日志记录 `template_version`，确保历史报告可复现。



---

## 四、意图路由模块设计

### 4.1 路由流程

```
用户 输入自然语言需求
→ IntentRouter 构建路由 Prompt（注入所有 active 模板的 scenario_desc + intent_examples + params_schema）
→ LLMClient 调用 LLM（function calling，强制输出 {template_id, params, confidence, missing_params, clarification}）
  ├─ 分支 A: confidence ≥ 0.7 且无 missing_params → 直接进入执行引擎
  ├─ 分支 B: confidence ≥ 0.7 但有 missing_params → 追问用户补齐参数
  └─ 分支 C: confidence < 0.7 → 返回澄清问题或回退到"通用经营概览"模板
```



## 五、Agent 执行引擎详细设计

执行引擎是系统的心脏。它是一个"模板解释器"——把模板 steps 逐步执行：每步确定性地取数，然后把数据和前序结论交给 LLM 做分析，最终综合成报告。

### 5.1 步骤分析 Prompt 模板

```text
你是资深经营分析师。请基于以下数据完成本步分析任务。

## 本步任务
/* 步骤描述 */
{{step_desc}}

/* 分析逻辑 */
{{analysis_logic}}

## 本步数据
{{data}}

## 预计算指标（已由代码计算，直接引用，勿自行运算）
{{computed_metrics}}

## 前序分析结论（上下文）
{{previous_conclusions}}

## 期望输出
{{expected_output}}

## 分析规则
1. **只基于提供的数据进行分析，不要编造任何数据**
2. **预计算指标已由代码算好，直接引用即可，不要自行重复计算**
3. 引用数据时标注来源（如"根据 Q_MONTHLY_REVENUE 查询结果..."）
4. 如果数据缺失、为空或异常，在 alerts 中明确说明
5. 数值变化需同时给出绝对值和相对变化（同比/环比）

请调用 output_step_conclusion 工具返回结构化结论。
```

