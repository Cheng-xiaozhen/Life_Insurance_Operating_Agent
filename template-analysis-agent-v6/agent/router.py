from __future__ import annotations

import re
from typing import Any, Mapping

from .catalog import ReportCatalog, ReportDefinition
from .models import (
    AgentContractError,
    BoundParameters,
    RouteDecision,
    SessionState,
)


MONTH_NAMES = (
    "一月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "十一月",
    "十二月",
)


def normalize_month(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    if text in MONTH_NAMES:
        month_number = MONTH_NAMES.index(text) + 1
        return text, f"{month_number}月"
    match = re.search(r"(?:\d{4}年)?\s*(1[0-2]|0?[1-9])\s*月", text)
    if not match:
        raise AgentContractError(f"cannot recognize month: {text!r}")
    month_number = int(match.group(1))
    return MONTH_NAMES[month_number - 1], f"{month_number}月"


class IntentRouter:
    def __init__(self, llm_client: Any, catalog: ReportCatalog, confidence_threshold: float = 0.7):
        self.llm_client = llm_client
        self.catalog = catalog
        self.confidence_threshold = confidence_threshold

    def route(
        self,
        message: str,
        pending_report_id: str | None = None,
        pending_parameters: list[str] | None = None,
    ) -> RouteDecision:
        candidates = self.catalog.summaries()
        if pending_report_id:
            candidates = [item for item in candidates if item["id"] == pending_report_id]
        raw = self.llm_client.route(
            message,
            candidates,
            pending_report_id=pending_report_id,
            pending_parameters=pending_parameters or [],
        )
        decision = raw if isinstance(raw, RouteDecision) else RouteDecision.from_mapping(raw)
        if decision.action not in {"execute", "clarify", "unsupported"}:
            raise AgentContractError(f"unsupported route action: {decision.action!r}")
        if not 0 <= decision.confidence <= 1:
            raise AgentContractError("route confidence must be between 0 and 1")
        if decision.report_id:
            self.catalog.get(decision.report_id)
        if decision.action == "execute" and not decision.report_id:
            raise AgentContractError("execute route must include report_id")
        if decision.action == "execute" and decision.confidence < self.confidence_threshold:
            return RouteDecision(
                action="clarify",
                report_id=decision.report_id,
                confidence=decision.confidence,
                extracted_params=decision.extracted_params,
                clarification=decision.clarification or "请确认需要生成哪一种分析报告。",
                reason=decision.reason or "模板匹配置信度不足",
            )
        return decision


class ParameterBinder:
    """Merges declared parameters and normalizes the current month pair."""

    def bind(
        self,
        route: RouteDecision,
        report: ReportDefinition,
        session: SessionState | None = None,
    ) -> BoundParameters:
        allowed = set(report.params_schema)
        unknown = sorted(set(route.extracted_params) - allowed)
        if unknown:
            raise AgentContractError(f"unknown report parameters: {', '.join(unknown)}")

        values = dict(report.parameters)
        if session:
            values.update({key: value for key, value in session.parameters.items() if key in allowed})
        explicit_month_inputs = [
            route.extracted_params[key]
            for key in ("report_month", "month_label")
            if key in route.extracted_params and str(route.extracted_params[key]).strip()
        ]
        values.update(route.extracted_params)
        month_inputs = explicit_month_inputs or [
            values[key]
            for key in ("report_month", "month_label")
            if key in values and str(values[key]).strip()
        ]
        normalized_months: set[tuple[str, str]] = set()
        for month_input in month_inputs:
            normalized_months.add(normalize_month(month_input))
        if len(normalized_months) > 1:
            return BoundParameters(
                values=values,
                clarification="月份参数相互冲突，请明确需要分析的月份。",
            )
        if normalized_months:
            report_month, month_label = next(iter(normalized_months))
            if "report_month" in allowed:
                values["report_month"] = report_month
            if "month_label" in allowed:
                values["month_label"] = month_label

        missing = [
            name
            for name, schema in report.params_schema.items()
            if bool(schema.get("required")) and not str(values.get(name, "")).strip()
        ]
        if missing:
            return BoundParameters(
                values=values,
                missing=missing,
                clarification=f"请补充参数：{', '.join(missing)}。",
            )
        return BoundParameters(values={key: values[key] for key in allowed if key in values})
