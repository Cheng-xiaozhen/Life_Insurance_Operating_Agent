"""Deterministic step handlers that turn canonical data into auditable facts."""

from __future__ import annotations

from typing import Any, Callable

from .config import SUPPORTED_OPERATORS, parse_number
from .errors import ExecutionError
from .models import (
    CanonicalDataset,
    CanonicalRow,
    CompiledScenePlan,
    Fact,
    FactBundle,
    MetricDefinition,
    Scalar,
)


def format_number(value: Scalar) -> str:
    if value is None:
        raise ExecutionError("缺失值不能格式化")
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def format_value(value: Scalar, formatter: str) -> str:
    if value is None:
        raise ExecutionError("缺失值不能格式化")
    formatters: dict[str, Callable[[Scalar], str]] = {
        "amount_wan": lambda item: f"{format_number(item)}万",
        "person": lambda item: f"{format_number(item)}人",
        "pct": lambda item: f"{format_number(item)}%",
        "abs_pct": lambda item: f"{format_number(abs(float(item)))}%",
        "pt": lambda item: f"{format_number(item)}pt",
        "abs_pt": lambda item: f"{format_number(abs(float(item)))}pt",
        "integer": format_number,
    }
    try:
        return formatters[formatter](value)
    except KeyError as exc:
        raise ExecutionError(f"未知格式化器：{formatter}") from exc


def metric_direction(value: Scalar, semantic: str) -> str | None:
    if value is None or semantic == "value":
        return None
    number = float(value)
    if semantic == "yoy":
        if number > 0:
            return "increase"
        if number < 0:
            return "decrease"
        return "flat"
    if semantic == "gap":
        if number > 0:
            return "excess"
        if number < 0:
            return "gap"
        return "met"
    return None


def compare(left: Scalar, operator: str, right: Scalar) -> bool:
    if operator not in SUPPORTED_OPERATORS:
        raise ExecutionError(f"未知比较运算符：{operator}")
    if operator == "is_missing":
        return left is None
    if operator == "not_missing":
        return left is not None
    if left is None or right is None:
        return False
    operations: dict[str, Callable[[Any, Any], bool]] = {
        "eq": lambda a, b: a == b,
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
    }
    return operations[operator](left, right)


def _resolve_rule(
    rule: dict[str, Any],
    parameters: dict[str, Scalar],
) -> dict[str, Any]:
    operator = str(rule.get("operator", ""))
    if operator not in SUPPORTED_OPERATORS:
        raise ExecutionError(f"未知比较运算符：{operator}")
    if operator in {"is_missing", "not_missing"}:
        threshold = None
    elif "threshold_param" in rule:
        name = str(rule["threshold_param"])
        if name not in parameters:
            raise ExecutionError(f"阈值参数没有值：{name}")
        threshold = parse_number(parameters[name], f"阈值 {name}")
    elif "value" in rule:
        threshold = parse_number(rule["value"], "规则阈值")
    else:
        raise ExecutionError(f"比较规则没有阈值：{rule}")
    return {
        "metric": str(rule["metric"]),
        "operator": operator,
        "threshold": threshold,
    }


def _band_conditions(
    step: dict[str, Any],
    band: dict[str, Any],
    parameters: dict[str, Scalar],
) -> tuple[str, list[dict[str, Any]]]:
    conditions = band.get("conditions")
    if conditions:
        match = str(conditions.get("match", "all"))
        rules = [_resolve_rule(rule, parameters) for rule in conditions["rules"]]
    else:
        match = "all"
        rules = [
            _resolve_rule(
                {
                    "metric": band.get("metric", step.get("metric")),
                    "operator": band.get("operator"),
                    "threshold_param": band.get("threshold_param"),
                },
                parameters,
            )
        ]
    if match not in {"all", "any"}:
        raise ExecutionError(f"复合条件 match 非法：{match}")
    return match, rules


def _matches(row: CanonicalRow, match: str, rules: list[dict[str, Any]]) -> bool:
    results = [
        compare(
            row.values.get(rule["metric"]),
            rule["operator"],
            rule["threshold"],
        )
        for rule in rules
    ]
    return all(results) if match == "all" else any(results)


def _order_rows(
    rows: list[CanonicalRow],
    metric_id: str,
    display_order: str,
) -> list[CanonicalRow]:
    if display_order == "source":
        return sorted(rows, key=lambda row: row.source_index)
    valued = [row for row in rows if row.values.get(metric_id) is not None]
    if display_order == "metric_asc":
        return sorted(
            valued,
            key=lambda row: (
                float(row.values[metric_id]),
                row.source_index,
            ),
        )
    if display_order == "metric_desc":
        return sorted(
            valued,
            key=lambda row: (
                -float(row.values[metric_id]),
                row.source_index,
            ),
        )
    raise ExecutionError(f"未知展示顺序：{display_order}")


class DeterministicAnalyzer:
    """Execute a compiled scene without any LLM participation."""

    def execute_scene(
        self,
        plan: CompiledScenePlan,
        dataset: CanonicalDataset,
        metrics: dict[str, MetricDefinition],
    ) -> FactBundle:
        facts: list[Fact] = []
        band_fact_index: dict[tuple[str, str], str] = {}
        for step in plan.steps:
            if step["type"] == "summarize":
                facts.extend(self._summarize(plan, step, dataset, metrics))
            elif step["type"] == "classify":
                step_facts = self._classify(plan, step, dataset, metrics)
                facts.extend(step_facts)
                for fact in step_facts:
                    band_id = fact.fact_id.rsplit(".", 1)[-1]
                    band_fact_index[(step["id"], band_id)] = fact.fact_id
            else:
                raise ExecutionError(f"未知步骤类型：{step['type']}")
        signals: dict[str, str] = {}
        for signal_name, signal in plan.signals.items():
            source = signal.get("from", {})
            key = (str(source.get("step")), str(source.get("band")))
            if key not in band_fact_index:
                raise ExecutionError(
                    f"信号引用未知分类结果：{plan.scene_id}/{signal_name}"
                )
            signals[signal_name] = band_fact_index[key]
        return FactBundle(
            scene_id=plan.scene_id,
            scene_title=plan.title,
            parameters=plan.parameters,
            facts=facts,
            signals=signals,
            organization_universe=[
                str(row.organization) for row in dataset.details if row.organization
            ],
        )

    def _summarize(
        self,
        plan: CompiledScenePlan,
        step: dict[str, Any],
        dataset: CanonicalDataset,
        metrics: dict[str, MetricDefinition],
    ) -> list[Fact]:
        result: list[Fact] = []
        for metric_id in step.get("metrics", []):
            definition = metrics[str(metric_id)]
            value = dataset.total.values.get(str(metric_id))
            if value is None:
                raise ExecutionError(
                    f"必需汇总指标缺失：{plan.scene_id}/{metric_id}"
                )
            result.append(
                Fact(
                    fact_id=f"{plan.scene_id}.{step['id']}.{metric_id}",
                    scene_id=plan.scene_id,
                    step_id=str(step["id"]),
                    fact_type="summary",
                    metric_id=f"{plan.scene_id}.{metric_id}",
                    label=definition.label,
                    raw_value=value,
                    display_value=format_value(value, definition.formatter),
                    unit=definition.unit,
                    direction=metric_direction(value, definition.semantic),
                    query_id=dataset.query_id,
                    query_version=dataset.query_version,
                    source_rows=[dataset.total.source_index],
                )
            )
        return result

    def _classify(
        self,
        plan: CompiledScenePlan,
        step: dict[str, Any],
        dataset: CanonicalDataset,
        metrics: dict[str, MetricDefinition],
    ) -> list[Fact]:
        result: list[Fact] = []
        display_order = str(step.get("display_order", "source"))
        for band in step.get("bands", []):
            match, rules = _band_conditions(step, band, plan.parameters)
            display_metric_id = str(
                band.get("display_metric")
                or band.get("metric")
                or step.get("metric")
                or rules[0]["metric"]
            )
            definition = metrics[display_metric_id]
            selected = [
                row
                for row in dataset.details
                if _matches(row, match, rules)
            ]
            selected = _order_rows(selected, display_metric_id, display_order)
            threshold: Scalar = None
            if "display_threshold_param" in band:
                threshold = plan.parameters[str(band["display_threshold_param"])]
            elif len(rules) == 1 and rules[0]["operator"] not in {
                "is_missing",
                "not_missing",
            }:
                threshold = rules[0]["threshold"]
            threshold_formatter = (
                definition.threshold_formatter or definition.formatter
            )
            organizations = [
                str(row.organization) for row in selected if row.organization
            ]
            condition = {
                "match": match,
                "rules": [
                    {
                        "metric_id": f"{plan.scene_id}.{rule['metric']}",
                        "operator": rule["operator"],
                        "threshold": rule["threshold"],
                    }
                    for rule in rules
                ],
            }
            result.append(
                Fact(
                    fact_id=f"{plan.scene_id}.{step['id']}.{band['id']}",
                    scene_id=plan.scene_id,
                    step_id=str(step["id"]),
                    fact_type="classification",
                    metric_id=f"{plan.scene_id}.{display_metric_id}",
                    label=definition.label,
                    condition=condition,
                    threshold=threshold,
                    threshold_display=format_value(
                        threshold, threshold_formatter
                    )
                    if threshold is not None
                    else None,
                    organizations=organizations,
                    count=len(organizations),
                    query_id=dataset.query_id,
                    query_version=dataset.query_version,
                    source_rows=[row.source_index for row in selected],
                    required=bool(band.get("required", True)),
                )
            )
        return result
