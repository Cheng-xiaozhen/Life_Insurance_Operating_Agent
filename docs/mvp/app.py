"""
寿险经营分析助手 Agent - MVP 后端
FastAPI + 场景匹配 + Mock数据 + DeepSeek LLM推理 + SSE流式输出
"""
import json
import logging
import os
import re
import asyncio
import sys
import time
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, AsyncGenerator, Union

import jieba
from openai import AsyncOpenAI
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── 配置 ──────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")
KG_DIR = BASE_DIR / "knowledge_graph"
MOCK_DIR = BASE_DIR / "mock_data"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# ── DeepSeek 客户端 ──
_deepseek_client: Optional[AsyncOpenAI] = None


def _get_deepseek_client() -> AsyncOpenAI:
    """懒加载 DeepSeek AsyncOpenAI 客户端"""
    global _deepseek_client
    if _deepseek_client is None:
        _deepseek_client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
    return _deepseek_client
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# ── 日志系统 ──────────────────────────────────────────
def setup_logging() -> logging.Logger:
    """配置双通道日志：控制台 + 按日滚动的文件"""
    logger = logging.getLogger("agent")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.DEBUG))
    logger.handlers.clear()

    # 控制台 Handler —— 简洁格式
    console_fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    # 文件 Handler —— 详细格式，单文件最大 10MB，保留 7 个历史文件
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(funcName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        LOG_DIR / "agent.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger

log = setup_logging()

app = FastAPI(title="寿险经营分析助手 Agent MVP", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── 数据加载 ──────────────────────────────────────────
def load_json(path: Path) -> dict:
    log.debug(f"加载数据文件: {path.relative_to(BASE_DIR)}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    log.debug(f"  ├─ 文件大小: {path.stat().st_size:,} bytes")
    return data

log.info("=" * 50)
log.info("Agent 启动中 — 加载知识图谱与模拟数据")
log.info("=" * 50)
scenarios_data = load_json(KG_DIR / "scenarios.json")
patterns_data = load_json(KG_DIR / "patterns.json")
metrics_data = load_json(KG_DIR / "metrics.json")
mock_data = load_json(MOCK_DIR / "may_data.json")

scenarios = scenarios_data["scenarios"]
patterns = {p["id"]: p for p in patterns_data["patterns"]}
metrics = metrics_data["metrics"]
categories = scenarios_data["categories"]

log.info(f"知识图谱加载完成: {len(scenarios)} 个场景, {len(patterns)} 个分析模式, "
         f"{len(metrics)} 个指标定义, {len(categories)} 个分类")
log.info(f"模拟数据加载完成: {len(mock_data.get('institutions', {}))} 家机构, "
         f"{len(mock_data.get('summary', {}))} 项汇总指标")
log.info(f"LLM 配置状态: {'已配置' if DEEPSEEK_API_KEY else '⚠️ 未配置（仅规则引擎模式）'}")


# ── 场景匹配引擎 ──────────────────────────────────────
def match_scenario(query: str) -> list[dict]:
    """基于关键词和 jieba 分词的场景匹配"""
    log.debug(f"[match_scenario] 开始匹配, query='{query}'")
    query_lower = query.lower()
    scores = []

    for scenario in scenarios:
        score = 0
        matched_keywords = []
        # 关键词匹配
        for pattern in scenario.get("trigger_patterns", []):
            if pattern.lower() in query_lower:
                score += 30
                matched_keywords.append(pattern)
        # jieba 分词匹配场景名称和描述
        keywords = set(w for w in jieba.cut(query) if len(w) >= 2)
        name_words = set(w for w in jieba.cut(scenario["name"]) if len(w) >= 2)
        desc_words = set(w for w in jieba.cut(scenario.get("description", "")) if len(w) >= 2)
        overlap_name = keywords & name_words
        overlap_desc = keywords & desc_words
        score += len(overlap_name) * 15 + len(overlap_desc) * 5

        if score > 0:
            log.debug(f"  ├─ 候选: {scenario['name']} (得分{score}, 关键词:{matched_keywords})")
            scores.append({
                "scenario": scenario,
                "score": score,
                "matched_keywords": matched_keywords,
            })

    scores.sort(key=lambda x: x["score"], reverse=True)
    log.info(f"[match_scenario] 匹配完成: {len(scores)} 个候选, Top1={scores[0]['scenario']['name']}({scores[0]['score']}分)" if scores else "[match_scenario] 匹配完成: 0 个候选 ⚠️")
    return scores


# ── 数据查询引擎 ──────────────────────────────────────
def query_data(scenario: dict, institutions: Optional[list[str]] = None) -> dict:
    """根据场景获取所需指标数据"""
    required_metrics = scenario.get("required_metrics", [])
    log.debug(f"[query_data] 场景={scenario['name']}, 指标={required_metrics}, 机构={institutions or '全量'}")
    result = {
        "scenario_name": scenario["name"],
        "scenario_id": scenario["id"],
        "summary": {},
        "institutions": {},
        "metric_definitions": {},
    }

    # 全系统汇总
    for m_id in required_metrics:
        if m_id in mock_data["summary"]:
            result["summary"][m_id] = mock_data["summary"][m_id]
        if m_id in metrics:
            result["metric_definitions"][m_id] = {
                "name": metrics[m_id]["name"],
                "unit": metrics[m_id]["unit"],
            }

    # 机构数据
    target_institutions = institutions or list(mock_data["institutions"].keys())
    for inst in target_institutions:
        if inst in mock_data["institutions"]:
            inst_data = {}
            for m_id in required_metrics:
                if m_id in mock_data["institutions"][inst]:
                    inst_data[m_id] = mock_data["institutions"][inst][m_id]
            if inst_data:
                result["institutions"][inst] = inst_data

    log.info(f"[query_data] 查询完成: 汇总{len(result['summary'])}项, {len(result['institutions'])}家机构")
    return result


# ── LLM 推理服务 ──────────────────────────────────────
async def call_deepseek(messages: list[dict], temperature: float = 0.1) -> str:
    """调用 DeepSeek API（非流式），使用 OpenAI 兼容客户端"""
    if not DEEPSEEK_API_KEY:
        log.warning("[call_deepseek] API Key 未配置，跳过 LLM 调用")
        return None
    try:
        input_chars = sum(len(m.get("content", "")) for m in messages)
        log.info(f"[call_deepseek] 发起请求: model=deepseek-v4-flash, msgs={len(messages)}, input_chars={input_chars}, temp={temperature}")
        t0 = time.time()
        client = _get_deepseek_client()
        response = await client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=messages,
            temperature=temperature,
            max_tokens=8192,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        content = response.choices[0].message.content
        usage = response.usage
        log.info(f"[call_deepseek] ✓ 成功: latency={elapsed_ms}ms, output_chars={len(content) if content else 0}, "
                 f"prompt_tokens={usage.prompt_tokens if usage else '?'}, "
                 f"completion_tokens={usage.completion_tokens if usage else '?'}")
        return content
    except Exception as e:
        log.error(f"[call_deepseek] ✗ 异常: {type(e).__name__}: {e}")
        return None


async def call_deepseek_stream(messages: list[dict], temperature: float = 0.1) -> AsyncGenerator[str, None]:
    """调用 DeepSeek API（流式），逐 token 产出，使用 OpenAI 兼容客户端"""
    if not DEEPSEEK_API_KEY:
        log.warning("[call_deepseek_stream] API Key 未配置")
        return
    try:
        input_chars = sum(len(m.get("content", "")) for m in messages)
        log.info(f"[call_deepseek_stream] 发起流式请求: model=deepseek-v4-flash, msgs={len(messages)}, input_chars={input_chars}, temp={temperature}")
        log.info(f"messages={messages}")
        t0 = time.time()
        token_count = 0
        client = _get_deepseek_client()
        stream = await client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=messages,
            temperature=temperature,
            max_tokens=8192,
            stream=True,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                token_count += 1
                yield delta.content
        elapsed_ms = int((time.time() - t0) * 1000)
        log.info(f"[call_deepseek_stream] ✓ 流式完成: latency={elapsed_ms}ms, tokens={token_count}")
    except Exception as e:
        log.error(f"[call_deepseek_stream] ✗ 异常: {type(e).__name__}: {e}")
        return


def generate_analysis_text(scenario: dict, data: dict, matched_scenarios: list, patterns: list = None) -> str:
    """根据数据和场景生成分析文本（离线规则引擎 + 可选LLM增强）"""
    log.debug(f"[generate_analysis_text] 场景={scenario['name']}, 机构数={len(data.get('institutions', {}))}, 模式数={len(patterns) if patterns else 0}")
    scenario_name = scenario["name"]
    summary = data.get("summary", {})
    institutions = data.get("institutions", {})
    metric_defs = data.get("metric_definitions", {})

    lines = []

    # ── 全系统概况 ──
    lines.append(f"## 📊 {scenario_name} — 全系统概况\n")
    for m_id, value in summary.items():
        metric_info = metric_defs.get(m_id, {})
        name = metric_info.get("name", m_id)
        unit = metric_info.get("unit", "")
        val_str = f"{value}{unit}" if unit == "%" else f"{value}{unit}"
        lines.append(f"- **{name}**：{val_str}")
    log.debug(f"[generate_analysis_text] 汇总指标: {list(summary.keys())}")

    # ── 分析模式说明 ──
    if patterns:
        lines.append(f"\n### 🧩 分析方法论")
        for p in patterns:
            lines.append(f"- **{p['name']}**：{p.get('description', '')}（{p.get('logic', '')}）")

    # ── 机构排名分析 ──
    steps = scenario.get("analysis_steps", [])
    for step in steps:
        if "group_thresholds" in step.get("params", {}):
            thresholds = step["params"]["group_thresholds"]
            metric = step.get("metrics", [None])[0]
            metric_name = metric_defs.get(metric, {}).get("name", metric) if metric else "指标"

            excellent_threshold = thresholds.get("excellent", 0)
            warning_threshold = thresholds.get("warning", 0)

            excellent_list = []
            warning_list = []
            for inst, vals in institutions.items():
                if metric and metric in vals:
                    v = vals[metric]
                    if v >= excellent_threshold:
                        excellent_list.append((inst, v))
                    if v < warning_threshold:
                        warning_list.append((inst, v))

            excellent_list.sort(key=lambda x: x[1], reverse=True)
            warning_list.sort(key=lambda x: x[1])

            lines.append(f"\n### {step['action']}\n")
            if excellent_list:
                names = ", ".join([f"**{n}**({v:.1f}%)" for n, v in excellent_list])
                lines.append(f"🟢 **优秀（{metric_name} ≥ {excellent_threshold}%）**：{names}")
            if warning_list:
                names = ", ".join([f"**{n}**({v:.1f}%)" for n, v in warning_list])
                lines.append(f"🔴 **预警（{metric_name} < {warning_threshold}%）**：{names}")
            log.debug(f"[generate_analysis_text] {step['action']}: 优秀{len(excellent_list)}家, 预警{len(warning_list)}家")

    # ── 生成图表数据 ──
    lines.append(f"\n---")
    lines.append(f"<!--CHART_DATA_START-->")
    chart_data = _generate_chart_data(scenario["id"], institutions, metric_defs)
    lines.append(json.dumps(chart_data, ensure_ascii=False))
    lines.append(f"<!--CHART_DATA_END-->")

    result = "\n".join(lines)
    log.info(f"[generate_analysis_text] 完成: {len(result)} chars, 图表类型={chart_data.get('type','?')}")
    return result


def _generate_chart_data(scenario_id: str, institutions: dict, metric_defs: dict) -> dict:
    """生成前端图表所需数据"""
    chart = {"type": "bar", "title": "", "labels": [], "datasets": []}

    inst_list = []
    if scenario_id == "scenario-biaobao-achievement":
        chart["title"] = "各机构标保达成率排名"
        chart["type"] = "bar"
        inst_list = sorted(institutions.items(), key=lambda x: x[1].get("monthly_biaobao_rate", 0), reverse=True)
        chart["labels"] = [i[0] for i in inst_list]
        chart["datasets"] = [
            {"name": "当月达成率(%)", "data": [round(i[1].get("monthly_biaobao_rate", 0), 1) for i in inst_list], "color": "#3b82f6"},
            {"name": "同比增长率(%)", "data": [round(i[1].get("monthly_biaobao_yoy", 0), 1) for i in inst_list], "color": "#10b981"},
        ]
        chart["alertLine"] = 50

    elif scenario_id == "scenario-manpower-active":
        chart["title"] = "活动 vs 阳光人力 — 机构散点分布"
        chart["type"] = "scatter"
        chart["labels"] = list(institutions.keys())
        chart["datasets"] = [
            {"name": "各机构", "data": [{
                "x": round(v.get("active_manpower_rate", 0), 1),
                "y": round(v.get("sunshine_ratio", 0), 1),
                "label": k
            } for k, v in institutions.items()], "color": "#8b5cf6"}
        ]
        chart["xAxis"] = "活动人力达成率(%)"
        chart["yAxis"] = "阳光占比(%)"

    elif scenario_id == "scenario-supervisor-activity":
        chart["title"] = "主管活动率 vs 标准组占比"
        chart["type"] = "quadrant"
        chart["labels"] = list(institutions.keys())
        chart["datasets"] = [
            {"name": "各机构", "data": [{
                "x": round(v.get("supervisor_active_rate", 0), 1),
                "y": round(v.get("standard_group_ratio", 0), 1),
                "label": k
            } for k, v in institutions.items()], "color": "#f59e0b"}
        ]
        chart["xAxis"] = "主管活动率(%)"
        chart["yAxis"] = "标准组占比(%)"

    elif scenario_id == "scenario-recruitment":
        chart["title"] = "新增达成率 vs 本科占比"
        chart["type"] = "scatter"
        chart["labels"] = list(institutions.keys())
        chart["datasets"] = [
            {"name": "各机构", "data": [{
                "x": round(v.get("new_recruit_rate", 0), 1),
                "y": round(v.get("bachelor_plus_ratio", 0), 1),
                "label": k
            } for k, v in institutions.items()], "color": "#ec4899"}
        ]
        chart["xAxis"] = "新增达成率(%)"
        chart["yAxis"] = "本科占比(%)"

    elif scenario_id == "scenario-comprehensive-evaluation":
        chart["title"] = "机构综合评级 — 红黄蓝矩阵"
        chart["type"] = "heatmap"
        evaluation = mock_data.get("comprehensive_evaluation", {})
        all_levels = {
            "四项跑赢（红）": evaluation.get("四项指标跑赢大盘", []),
            "三/两项跑赢（黄）": evaluation.get("三两项指标跑赢大盘", []),
            "一项跑赢（蓝）": evaluation.get("一项指标跑赢大盘", []),
            "零项跑赢（灰）": evaluation.get("零项指标跑赢大盘", []),
        }
        chart["labels"] = list(all_levels.keys())
        chart["datasets"] = [{"name": k, "data": v, "count": len(v)} for k, v in all_levels.items()]

    elif scenario_id == "scenario-tongyin":
        chart["title"] = "同引季度达成率"
        chart["type"] = "bar"
        sorted_inst = sorted(institutions.items(), key=lambda x: x[1].get("tongyin_quarter_rate", 0), reverse=True)
        chart["labels"] = [i[0] for i in sorted_inst]
        chart["datasets"] = [
            {"name": "季度达成率(%)", "data": [round(i[1].get("tongyin_quarter_rate", 0), 1) for i in sorted_inst], "color": "#14b8a6"}
        ]

    elif scenario_id == "scenario-value-achievement":
        chart["title"] = "标保 vs 价值达成率对比"
        chart["type"] = "bar"
        sorted_inst = sorted(institutions.items(), key=lambda x: x[1].get("monthly_value_rate", 0), reverse=True)
        chart["labels"] = [i[0] for i in sorted_inst]
        chart["datasets"] = [
            {"name": "标保达成率(%)", "data": [round(i[1].get("monthly_biaobao_rate", 0), 1) for i in sorted_inst], "color": "#3b82f6"},
            {"name": "价值达成率(%)", "data": [round(i[1].get("monthly_value_rate", 0), 1) for i in sorted_inst], "color": "#f59e0b"},
        ]

    return chart


# ── LLM 分析 Prompt 构建 ──────────────────────────────────
def format_data_table(data: dict) -> str:
    """将结构化数据转为 LLM 友好的 Markdown 表格"""
    lines = []
    summary = data.get("summary", {})
    institutions = data.get("institutions", {})
    metric_defs = data.get("metric_definitions", {})

    # 全系统汇总
    lines.append("## 全系统汇总数据\n")
    lines.append("| 指标名称 | 数值 |")
    lines.append("|----------|------|")
    for m_id, value in summary.items():
        info = metric_defs.get(m_id, {})
        name = info.get("name", m_id)
        unit = info.get("unit", "")
        val_str = f"{value}{unit}" if unit else str(value)
        lines.append(f"| {name} | {val_str} |")

    # 机构明细表
    lines.append(f"\n## 各机构明细数据（共 {len(institutions)} 家机构）\n")
    all_metrics = list(metric_defs.keys())
    if all_metrics:
        header = "| 机构 | " + " | ".join(
            f"{metric_defs[m].get('name', m)}({metric_defs[m].get('unit', '')})" for m in all_metrics
        ) + " |"
        lines.append(header)
        lines.append("|" + "|".join(["------"] * (len(all_metrics) + 1)) + "|")
        for inst_name, vals in institutions.items():
            cells = []
            for m in all_metrics:
                v = vals.get(m, "-")
                unit = metric_defs[m].get("unit", "")
                cells.append(f"{v}{unit}" if unit else str(v))
            lines.append(f"| {inst_name} | " + " | ".join(cells) + " |")

    return "\n".join(lines)


def build_llm_analysis_messages(
    scenario: dict,
    data: dict,
    applied_patterns: list,
    user_query: str = "",
) -> tuple[str, str, str]:
    """构建 LLM 分析所需的 system prompt 和 user prompt
    返回: (system_prompt, user_prompt, analysis_steps_text)
    """
    # ── 分析模式 ──
    pattern_text = ""
    if applied_patterns:
        pattern_lines = [f"- **{p['name']}**：{p.get('description', '')}\n  方法论：{p.get('logic', '')}\n  输出格式：{p.get('output_format', '')}" for p in applied_patterns]
        pattern_text = "\n".join(pattern_lines)

    # ── 分析步骤 ──
    analysis_steps = scenario.get("analysis_steps", [])
    steps_text = "\n".join([
        f"{s['step']}. {s['action']}"
        + (f"（阈值：优秀≥{s['params']['group_thresholds']['excellent']}%，预警<{s['params']['group_thresholds']['warning']}%）"
           if "group_thresholds" in s.get("params", {}) else "")
        for s in analysis_steps
    ])

    # ── 数据表格 ──
    data_table = format_data_table(data)

    # ── 动态输出要求（基于匹配到的分析模式）──
    output_req_lines = []
    output_req_lines.append("## 输出要求\n")

    # 1. 通用基础要求
    output_req_lines.append("### 基础要求")
    output_req_lines.append('1. **全系统概况**：先用一段话概括全系统汇总数据的关键指标，点出最突出的数字')

    # 2. 按匹配到的模式动态生成分析要求
    req_index = 2
    if applied_patterns:
        output_req_lines.append(f"\n### 按分析模式逐项分析")
        for p in applied_patterns:
            pattern_id = p["id"]
            pattern_name = p["name"]
            p_output = p.get("output_format", "")

            if pattern_id == "pattern-benchmark-ranking":
                output_req_lines.append(f'{req_index}. **{pattern_name}**：按分析步骤中的阈值，将所有机构分为🟢优秀组、🟡达标组、🔴预警组，每组用表格列出机构名称和关键指标数据')
            elif pattern_id == "pattern-yoy-trend":
                output_req_lines.append(f'{req_index}. **{pattern_name}**：列出同比正增长的机构和负增长的机构，标注变化幅度，识别增长/下滑最显著的TOP3')
            elif pattern_id == "pattern-progress-tracking":
                output_req_lines.append(f'{req_index}. **{pattern_name}**：列出各机构的年度目标进度百分比，标注进度超前（>45%）、正常（35%-45%）、滞后（<35%）的状态')
            elif pattern_id == "pattern-structure-distribution":
                output_req_lines.append(f'{req_index}. **{pattern_name}**：计算各子项在总体中的占比，与目标值/基准值对比，指出偏离较大的项目')
            elif pattern_id == "pattern-cross-evaluation":
                output_req_lines.append(f'{req_index}. **{pattern_name}**：从多维度对各机构逐一打分对比，统计跑赢系统均值的项数，按跑赢项数分类（如全能型/偏科型/全面落后型），给出矩阵表格')
            elif pattern_id == "pattern-anomaly-detection":
                output_req_lines.append(f'{req_index}. **{pattern_name}**：筛选出指标极端异常或挂零的机构，列举异常指标明细，分析可能的原因')
            else:
                output_req_lines.append(f'{req_index}. **{pattern_name}**（{p_output}）：根据方法论执行分析')
            req_index += 1

    # 3. 交叉洞察（仅当匹配到 >= 2 个模式时）
    if applied_patterns and len(applied_patterns) >= 2:
        output_req_lines.append(f'\n### 交叉洞察')
        output_req_lines.append(f'{req_index}. 结合上述多个分析维度，挖掘交叉异常（如某机构达成率高但同比下滑、新人力指标好但活动率低等矛盾场景），给出全局性判断')
        req_index += 1

    # 4. 建议
    output_req_lines.append(f'\n### 收尾')
    output_req_lines.append(f'{req_index}. **洞察与建议**：基于以上分析给出 2-3 条具体的经营建议或需关注的追问方向')
    req_index += 1

    # 5. 格式与硬约束
    output_req_lines.append(f'\n### 格式与约束')
    output_req_lines.append(f'{req_index}. 使用 Markdown 格式，合理使用 emoji 增强可读性')
    req_index += 1
    output_req_lines.append(f'{req_index}. **严禁编造数据**：所有引用的数字必须来自下方数据表格，不得凭空生成')

    output_requirements = "\n".join(output_req_lines)

    # 6. 图表 JSON 输出指令（独立段落，更显眼）
    chart_instruction = """
## 📊 图表数据输出（必须执行）

在报告正文全部结束后，输出一个 ` ```chartjson ` 代码块，包含前端图表配置 JSON。根据当前分析场景选择最合适的图表类型：

### 1. 柱状图 (bar) — 排名/对比场景
```chartjson
{"type":"bar","title":"各机构标保达成率","labels":["新疆","天津","深圳","北京","上海"],"datasets":[{"name":"当月达成率","data":[100.5,100.1,85.3,72.1,60.4],"color":"#3b82f6"}],"alertLine":45}
```
- labels: 机构名称数组
- datasets: 支持多组数据，每组含 name、data（数值数组）、color
- alertLine: 可选，预警线数值（如达标线 45%）
- 适用于：排名分组、同比对比、进度追踪

### 2. 散点图/象限图 (scatter / quadrant) — 双维度交叉分析
```chartjson
{"type":"scatter","title":"活动率 vs 标准组占比","xAxis":"活动率(%)","yAxis":"标准组占比(%)","quadrantX":70,"quadrantY":30,"datasets":[{"name":"机构","data":[{"x":85,"y":35,"label":"深圳"},{"x":60,"y":20,"label":"北京"}],"color":"#8b5cf6"}]}
```
- xAxis / yAxis: 横纵轴名称
- datasets[0].data: 对象数组，每个含 x、y（数值）、label（机构名）
- quadrantX / quadrantY: 可选，象限分割线的位置（如全系统均值），不填则不画分割线
- 适用于：交叉评价、双指标关联分析

### 3. 热力图 (heatmap) — 多维度矩阵评价
```chartjson
{"type":"heatmap","title":"机构综合评级矩阵","labels":["红牌预警","黄牌关注","蓝牌良好"],"datasets":[{"name":"蓝牌良好","data":["新疆","天津"]},{"name":"黄牌关注","data":["深圳","北京"]},{"name":"红牌预警","data":["上海"]}]}
```
- labels: 评级/分类标签数组
- datasets: 每个数据集 name=标签名，data=属于该标签的机构名数组
- 适用于：综合经营评价（红黄蓝）、多维度分类

### 通用规则
- 使用数据表格中的实际数字，严禁编造
- color 推荐: "#3b82f6"(蓝) "#10b981"(绿) "#f59e0b"(黄) "#ef4444"(红) "#8b5cf6"(紫)
- labels/data 至少包含 5 家机构"""

    # ── System Prompt ──
    system_prompt = f"""你是一位资深的寿险经营数据分析师。你将收到一份结构化的经营数据表格，请严格按照以下框架进行专业分析并生成报告。

## 当前分析场景
**{scenario['name']}**：{scenario.get('description', '')}

## 应用的分析模式
{pattern_text if pattern_text else '无特定分析模式'}

## 分析步骤（请严格按此顺序执行）
{steps_text}

{output_requirements}
{chart_instruction}"""

    # ── User Prompt ──
    user_prompt = f"""{data_table}

请基于以上数据，生成「{scenario['name']}」的完整经营分析报告。**请务必在报告末尾按格式输出图表 JSON。**"""

    return system_prompt, user_prompt, steps_text


def _extract_chart_data(answer: str) -> Optional[dict]:
    """从规则引擎输出中提取图表 JSON"""
    if "<!--CHART_DATA_START-->" not in answer:
        return None
    try:
        json_str = answer.split("<!--CHART_DATA_START-->")[1].split("<!--CHART_DATA_END-->")[0].strip()
        return json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        log.warning("[_extract_chart_data] 图表数据解析失败")
        return None


def _parse_llm_output(llm_answer: str, scenario: dict, data: dict) -> tuple[str, Optional[dict]]:
    """解析 LLM 输出，分离分析文本和图表 JSON
    返回: (clean_answer, chart_data_or_None)
    """
    chart_data = None
    clean_answer = llm_answer

    # 尝试从 LLM 输出中提取图表 JSON
    import re

    # 策略1: 提取 <!--CHART-->...<!--ENDCHART--> 标记（保留兼容）

    chart_match = re.search(r'<!--CHART-->\s*(.+?)\s*<!--ENDCHART-->', llm_answer, re.DOTALL)

    # 策略2: 提取 ```chartjson ... ``` 代码块（新增，LLM 更友好）
    if not chart_match:
        chart_match = re.search(r'`{3}chartjson\s*\n(.*?)`{3}', llm_answer, re.DOTALL)

    # 策略3: 提取末尾的 ```json ... ``` 代码块（兜底）
    if not chart_match:
        # 从后往前找最后一个 ```json 代码块
        json_blocks = list(re.finditer(r'`{3}json\s*\n(.*?)`{3}', llm_answer, re.DOTALL))
        if json_blocks:
            chart_match = json_blocks[-1]  # 取最后一个
    if chart_match:
        raw_json = chart_match.group(1).strip()
        # 处理 LLM 常见输出：包裹在 ```json ... ``` 或 ``` ... ``` 代码块中
        code_fence_match = re.match(r'```(?:json)?\s*(.+?)\s*```', raw_json, re.DOTALL)
        if code_fence_match:
            raw_json = code_fence_match.group(1).strip()
        try:
            chart_data = json.loads(raw_json)
            clean_answer = llm_answer[:chart_match.start()].strip() + llm_answer[chart_match.end():].strip()
            log.info(f"[_parse_llm_output] LLM 生成了图表数据: type={chart_data.get('type','?')}")
        except json.JSONDecodeError as e:
            log.warning(f"[_parse_llm_output] LLM 图表 JSON 解析失败: {e.msg[:80]}, raw={raw_json[:100]}")
            chart_data = None

    # 如果 LLM 没生成图表，用规则引擎兜底
    if chart_data is None:
        chart_data = _generate_chart_data(scenario["id"], data.get("institutions", {}), data.get("metric_definitions", {}))
        log.info(f"[_parse_llm_output] 使用规则引擎生成图表: type={chart_data.get('type','?')}")
        # 把图表数据以注释形式嵌入，保持前端兼容
        clean_answer += f"\n\n---\n<!--CHART_DATA_START-->{json.dumps(chart_data, ensure_ascii=False)}<!--CHART_DATA_END-->"

    return clean_answer, chart_data


# ── API 模型 ──────────────────────────────────────────
class ChatRequest(BaseModel):
    query: str
    use_llm: bool = True
    conversation_history: list[dict] = []


class ChatResponse(BaseModel):
    answer: str
    matched_scenario: Optional[dict] = None
    scenario_confidence: float = 0.0
    alternative_scenarios: list = []
    chart_data: Optional[dict] = None
    reasoning_steps: list = []
    used_llm: bool = False


# ── API 路由 ──────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "llm_configured": bool(DEEPSEEK_API_KEY)}


@app.get("/api/scenarios")
def list_scenarios():
    log.debug("[GET /api/scenarios] 返回场景列表")
    return {"scenarios": scenarios, "categories": categories}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    query = req.query.strip()
    log.info(f"[POST /api/chat] 请求: query='{query[:80]}{'...' if len(query)>80 else ''}', use_llm={req.use_llm}")
    t0 = time.time()
    if not query:
        log.warning("[POST /api/chat] 空查询被拒绝")
        raise HTTPException(status_code=400, detail="问题不能为空")

    # Step 1: 场景匹配
    matches = match_scenario(query)
    if not matches:
        log.warning(f"[POST /api/chat] 无匹配场景, 返回提示")
        return ChatResponse(
            answer="抱歉，我目前的知识库还没有覆盖到您问的这个场景。\n\n我目前支持的场景包括：\n- 📊 标保/价值达成分析\n- 👥 活动人力/阳光人力分析\n- 📋 主管活动与标准组分析\n- 🆕 新增人力分析\n- 🔄 同引分析\n- 🏆 综合经营评价（红黄蓝）\n\n请尝试用这些场景的关键词提问。",
            alternative_scenarios=[],
            reasoning_steps=[],
        )

    top_match = matches[0] 
    scenario = top_match["scenario"] # 最佳适配场景
    alternatives = [{"name": m["scenario"]["name"], "score": m["score"]} for m in matches[1:4]] # 3个备选场景

    # Step 3: 分析模式匹配
    scenario_pattern_ids = set(scenario.get("applied_patterns", [])) # 
    applied_patterns = [
        p for p in patterns_data.get("patterns", [])
        if p["id"] in scenario_pattern_ids
    ]

    reasoning_steps = [
        {"step": 1, "action": "语义分析", "detail": f"识别问题关键词：{', '.join(top_match['matched_keywords'][:5])}"},
        {"step": 2, "action": "场景匹配", "detail": f"最佳匹配：{scenario['name']}（置信度 {top_match['score']}分）"},
        {"step": 3, "action": "模式匹配", "detail": f"匹配到 {len(applied_patterns)} 个分析模式：{', '.join([p['name'] for p in applied_patterns]) if applied_patterns else '无'}"},
    ]

    # Step 4: 数据查询
    data = query_data(scenario)
    reasoning_steps.append({
        "step": 4,
        "action": "数据查询",
        "detail": f"查询指标 {len(data.get('summary', {}))} 项，涉及 {len(data.get('institutions', {}))} 家机构"
    })

    # Step 5: 生成分析
    used_llm = False
    chart_data = None

    if req.use_llm and DEEPSEEK_API_KEY:
        # ── LLM 直接分析模式 ──
        reasoning_steps.append({
            "step": 5,
            "action": "LLM 分析",
            "detail": "将结构化数据与分析方法论提交给 LLM，由 LLM 直接执行排名、分组、趋势识别与洞察提炼"
        })

        system_prompt, user_prompt, _ = build_llm_analysis_messages(
            scenario, data, applied_patterns, query
        )

        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        llm_answer = await call_deepseek(llm_messages)
        if llm_answer:
            # 解析 LLM 输出中的图表 JSON
            answer, chart_data = _parse_llm_output(llm_answer, scenario, data)
            used_llm = True
            reasoning_steps.append({
                "step": 6,
                "action": "分析完成",
                "detail": f"LLM 完成 {len(llm_answer)} 字符的专业分析报告"
            })
        else:
            # LLM 失败，fallback 到规则引擎
            log.warning("[POST /api/chat] LLM 调用失败，回退到规则引擎")
            answer = generate_analysis_text(scenario, data, matches, applied_patterns)
            chart_data = _extract_chart_data(answer)
            answer = answer.split("<!--CHART_DATA_START-->")[0].strip() if chart_data else answer
    else:
        # ── 规则引擎模式（无 LLM）──
        answer = generate_analysis_text(scenario, data, matches, applied_patterns)
        reasoning_steps.append({
            "step": 5,
            "action": "规则分析",
            "detail": "基于规则引擎生成结构化分析报告"
        })
        chart_data = _extract_chart_data(answer)
        answer = answer.split("<!--CHART_DATA_START-->")[0].strip() if chart_data else answer

    elapsed = int((time.time() - t0) * 1000)
    log.info(f"[POST /api/chat] 响应: scenario={scenario['name']}, llm={used_llm}, answer_chars={len(answer)}, chart={'有' if chart_data else '无'}, elapsed={elapsed}ms")
    return ChatResponse(
        answer=answer,
        matched_scenario={
            "id": scenario["id"],
            "name": scenario["name"],
            "category": scenario["category"],
        },
        scenario_confidence=min(top_match["score"] / 50.0, 1.0),
        alternative_scenarios=alternatives,
        chart_data=chart_data,
        reasoning_steps=reasoning_steps,
        used_llm=used_llm,
    )


# ── 请求中间件 — 全局请求日志 ────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录每个 HTTP 请求的路径、耗时和状态码"""
    start = time.time()
    response = await call_next(request)
    elapsed_ms = int((time.time() - start) * 1000)
    log.debug(f"[HTTP] {request.method} {request.url.path} → {response.status_code} ({elapsed_ms}ms)")
    return response


# ── SSE 流式响应助手 ──────────────────────────────────
def sse_event(event: str, data: Union[dict, str]) -> str:
    """格式化 SSE 事件"""
    payload = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
    return f"event: {event}\ndata: {payload}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """流式 SSE 接口：逐步推送推理过程和内容"""
    query = req.query.strip()
    log.info(f"[POST /api/chat/stream] 请求: query='{query[:80]}{'...' if len(query)>80 else ''}', use_llm={req.use_llm}")
    if not query:
        log.warning("[POST /api/chat/stream] 空查询被拒绝")
        return StreamingResponse(
            iter([sse_event("error", {"message": "问题不能为空"})]),
            media_type="text/event-stream",
        )

    async def generate():
        t0 = time.time()

        # ── Step 1: 问题解析 ──
        log.debug(f"[stream] Step 1 - 问题解析开始")
        yield sse_event("reasoning", {
            "step": 1, "action": "🔍 问题解析", "status": "running",
            "detail": f"正在解析用户提问：「{query}」"
        })
        await asyncio.sleep(0.15)

        # jieba 分词
        seg_words = [w for w in jieba.cut(query) if len(w) >= 2]
        # 实体识别（机构名）
        all_institutions = list(mock_data.get("institutions", {}).keys())
        mentioned_insts = [inst for inst in all_institutions if inst in query]
        # 意图分类关键词
        intent_hints = {cat["id"]: [] for cat in categories}
        for cat in categories:
            for sid in cat.get("scenarios", []):
                for s in scenarios:
                    if s["id"] == sid:
                        for kw in s.get("trigger_patterns", []):
                            if kw.lower() in query.lower():
                                intent_hints[cat["id"]].append(kw)

        yield sse_event("reasoning", {
            "step": 1, "action": "🔍 问题解析", "status": "done",
            "detail": f"分词结果：{', '.join(seg_words)}\n识别实体：{', '.join(mentioned_insts) if mentioned_insts else '无特定机构'}\n意图信号：{'、'.join([f'{k}({len(v)}个) ' for k,v in intent_hints.items() if v])}"
        })
        log.debug(f"[stream] Step 1 完成: 分词={len(seg_words)}词, 实体={mentioned_insts}")
        await asyncio.sleep(0.1)

        # ── Step 2: 场景检索 ──
        log.debug(f"[stream] Step 2 - 场景检索开始")
        yield sse_event("reasoning", {
            "step": 2, "action": "🔗 场景检索", "status": "running",
            "detail": f"在知识图谱 {len(scenarios)} 个场景中检索匹配..."
        })
        await asyncio.sleep(0.15)

        matches = match_scenario(query)
        if not matches:
            log.warning(f"[stream] 无匹配场景，跳过分析")
            # 补全 Step 2 done 状态，再显示未匹配结论
            yield sse_event("reasoning", {
                "step": 2, "action": "🔗 场景检索", "status": "done",
                "detail": f"在知识图谱 {len(scenarios)} 个场景中检索完毕，未找到匹配场景。"
            })
            await asyncio.sleep(0.1)
            # 后续步骤标记为跳过，保持推理链完整
            for i, (step_num, action, desc) in enumerate([
                (3, "🧩 模式匹配", "未匹配场景，跳过"),
                (4, "📋 指标映射", "未匹配场景，跳过"),
                (5, "📡 数据查询", "未匹配场景，跳过"),
                (6, "🧠 执行分析", "未匹配场景，跳过"),
                (7, "📝 报告生成", "无需生成"),
            ], start=1):
                yield sse_event("reasoning", {
                    "step": step_num, "action": action, "status": "done",
                    "detail": desc
                })
                await asyncio.sleep(0.05)

            yield sse_event("content", {"text": "抱歉，我目前的知识库还没有覆盖到您问的这个场景。\n\n我目前支持的场景包括：\n- 标保/价值达成分析\n- 活动人力/阳光人力分析\n- 主管活动与标准组分析\n- 新增人力分析\n- 同引分析\n- 综合经营评价（红黄蓝）\n\n请尝试用这些场景的关键词提问。"})
            yield sse_event("done", {"used_llm": False, "elapsed_ms": int((time.time() - t0) * 1000)})
            return

        # 展示所有候选场景及得分
        candidate_detail = " | ".join([
            f"{m['scenario']['name']}({m['score']}分)"
            for m in matches[:5]
        ])
        top_match = matches[0]
        scenario = top_match["scenario"]
        log.info(f"[stream] 匹配场景: {scenario['name']} (得分{top_match['score']}, 类别={scenario.get('category','?')})")

        yield sse_event("reasoning", {
            "step": 2, "action": "🔗 场景检索", "status": "done",
            "detail": f"候选场景（Top 5）：{candidate_detail}\n最佳匹配：「{scenario['name']}」，得分 {top_match['score']}"
        })
        await asyncio.sleep(0.1)

        # ── Step 3: 分析模式匹配 ──
        # 通过场景的 applied_patterns 字段直接匹配 pattern id
        scenario_pattern_ids = set(scenario.get("applied_patterns", []))
        applied_patterns = [
            p for p in patterns_data.get("patterns", [])
            if p["id"] in scenario_pattern_ids
        ]

        yield sse_event("reasoning", {
            "step": 3, "action": "🧩 模式匹配", "status": "done",
            "detail": f"匹配到 {len(applied_patterns)} 个可复用分析模式：{', '.join([p['name'] for p in applied_patterns]) if applied_patterns else '专用分析流程'}"
        })
        await asyncio.sleep(0.1)

        # 发送场景信息给前端
        alternatives = [{"name": m["scenario"]["name"], "score": m["score"]} for m in matches[1:4]]
        yield sse_event("scenario", {
            "matched_scenario": {
                "id": scenario["id"],
                "name": scenario["name"],
                "category": scenario["category"],
                "description": scenario.get("description", ""),
            },
            "confidence": min(top_match["score"] / 50.0, 1.0),
            "alternatives": alternatives,
        })

        # ── Step 4: 指标映射 ──
        required_metrics = scenario.get("required_metrics", [])
        metric_names = []
        for m_id in required_metrics:
            mn = metrics.get(m_id, {}).get("name", m_id)
            metric_names.append(mn)

        yield sse_event("reasoning", {
            "step": 4, "action": "📋 指标映射", "status": "running",
            "detail": f"分析场景需要 {len(required_metrics)} 个指标\n指标清单：{', '.join(metric_names[:8])}{'...' if len(metric_names) > 8 else ''}"
        })
        await asyncio.sleep(0.2)

        yield sse_event("reasoning", {
            "step": 4, "action": "📋 指标映射", "status": "done",
            "detail": f"指标映射完成：{len(required_metrics)} 个指标已关联到数据源"
        })
        await asyncio.sleep(0.1)

        # ── Step 5: 数据查询 ──
        yield sse_event("reasoning", {
            "step": 5, "action": "📡 数据查询", "status": "running",
            "detail": f"正在查询 {len(required_metrics)} 项指标，覆盖 {len(mock_data.get('institutions', {}))} 家机构..."
        })
        await asyncio.sleep(0.2)

        data = query_data(scenario, mentioned_insts if mentioned_insts else None)
        inst_count = len(data.get("institutions", {}))
        summary_count = len(data.get("summary", {}))

        yield sse_event("reasoning", {
            "step": 5, "action": "📡 数据查询", "status": "done",
            "detail": f"查询完成：获取全系统汇总 {summary_count} 项，{inst_count} 家机构明细数据\n数据口径：截至 5月31日，T+1 刷新"
        })
        await asyncio.sleep(0.1)

        # ── Step 6: 执行分析 ──
        use_llm = req.use_llm and bool(DEEPSEEK_API_KEY)

        if use_llm:
            # ── LLM 直接分析模式 ──
            yield sse_event("reasoning", {
                "step": 6, "action": "🧠 LLM 分析", "status": "running",
                "detail": f"将 {len(data.get('institutions', {}))} 家机构的 {len(data.get('summary', {}))} 项指标数据提交给 LLM，结合 {len(applied_patterns)} 个分析模式进行深度分析..."
            })

            system_prompt, user_prompt, _ = build_llm_analysis_messages(
                scenario, data, applied_patterns, query
            )
            log.info(f"system_prompt={system_prompt}")
            log.info(f"user_prompt={user_prompt}")


            llm_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            llm_full = ""
            async for token in call_deepseek_stream(llm_messages):
                llm_full += token
                yield sse_event("content", {"text": token})

            # 解析 LLM 输出中的图表 JSON 或回退到规则引擎
            clean_answer, chart_data = _parse_llm_output(llm_full, scenario, data)

            yield sse_event("reasoning", {
                "step": 6, "action": "🧠 LLM 分析", "status": "done",
                "detail": f"LLM 完成分析报告，共 {len(llm_full)} 字符"
            })
        else:
            # ── 规则引擎模式 ──
            yield sse_event("reasoning", {
                "step": 6, "action": "🧠 规则分析", "status": "running",
                "detail": f"正在按「{scenario['name']}」分析逻辑执行推理..."
            })

            base_answer = generate_analysis_text(scenario, data, matches, applied_patterns)
            chart_data = _extract_chart_data(base_answer)
            clean_answer = base_answer.split("<!--CHART_DATA_START-->")[0].strip() if chart_data else base_answer
            await asyncio.sleep(0.3)

            # 统计优秀/预警机构数
            institutions_data = data.get("institutions", {})
            excellent_count = 0
            warning_count = 0
            for step in scenario.get("analysis_steps", []):
                if "group_thresholds" in step.get("params", {}):
                    thr = step["params"]["group_thresholds"]
                    metric = step.get("metrics", [None])[0]
                    for vals in institutions_data.values():
                        if metric and metric in vals:
                            if vals[metric] >= thr.get("excellent", 0):
                                excellent_count += 1
                            elif vals[metric] < thr.get("warning", 0):
                                warning_count += 1

            yield sse_event("reasoning", {
                "step": 6, "action": "🧠 规则分析", "status": "done",
                "detail": f"分析执行完毕：完成机构排名、分组评估、同比趋势等维度分析\n识别优秀机构 {excellent_count} 家，预警机构 {warning_count} 家"
            })
            await asyncio.sleep(0.1)

            # ── Step 7: 报告生成（规则模式流式输出）──
            yield sse_event("reasoning", {
                "step": 7, "action": "📝 报告生成", "status": "running",
                "detail": "基于规则引擎生成结构化分析报告..."
            })
            await asyncio.sleep(0.1)

            paragraphs = clean_answer.split("\n\n")
            for i, para in enumerate(paragraphs):
                text = para + ("\n\n" if i < len(paragraphs) - 1 else "")
                yield sse_event("content", {"text": text})
                await asyncio.sleep(0.03)

            yield sse_event("reasoning", {
                "step": 7, "action": "📝 报告生成", "status": "done",
                "detail": f"报告生成完毕，共 {len(paragraphs)} 个段落"
            })

        # ── Step 8: 图表数据 ──
        # 各类型图表的数据结构不同：
        # bar/heatmap 需要 labels 数组，scatter/quadrant 需要 datasets
        has_chart = chart_data and (
            chart_data.get("labels") or
            (chart_data.get("type") in ("scatter", "quadrant") and chart_data.get("datasets"))
        )
        if has_chart:
            yield sse_event("chart", chart_data)

        # ── Done ──
        elapsed_ms = int((time.time() - t0) * 1000)
        log.info(f"[stream] 完成: used_llm={use_llm}, scenario={scenario['id']}, elapsed={elapsed_ms}ms")
        yield sse_event("done", {
            "used_llm": use_llm,
            "elapsed_ms": elapsed_ms,
            "scenario_id": scenario["id"],
        })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 启动 / 关闭事件 ──────────────────────────────────
@app.on_event("startup")
async def startup_event():
    log.info("=" * 50)
    log.info("🚀 Agent 服务已启动 — 监听 0.0.0.0:8000")
    log.info(f"   模式: {'LLM增强' if DEEPSEEK_API_KEY else '规则引擎'}")
    log.info(f"   日志级别: {LOG_LEVEL}")
    log.info(f"   日志文件: {LOG_DIR / 'agent.log'}")
    log.info("=" * 50)


@app.on_event("shutdown")
async def shutdown_event():
    log.info("🛑 Agent 服务已关闭")


# ── 静态文件 ──────────────────────────────────────────
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)


@app.get("/")
def serve_frontend():
    return FileResponse(static_dir / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
