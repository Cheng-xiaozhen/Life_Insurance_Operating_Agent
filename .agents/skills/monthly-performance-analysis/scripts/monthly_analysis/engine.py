"""Deterministic routing, parameter binding, and template execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from .expression import (
    DeepSeekExpressionProvider,
    ExpressionProvider,
    NarrativeValidator,
    assemble_report,
)
from .models import (
    AnalysisRequest,
    AnalysisResult,
    AnalysisTemplate,
    BandSpec,
    ClassifyStep,
    ConfigurationError,
    ExpressionError,
    Fact,
    FactItem,
    MetricSpec,
    NarrativeBlock,
    NarrativeGroupSpec,
    RankStep,
    RouteCandidate,
    RoutingResult,
    RuleSpec,
    Scalar,
    SummaryStep,
    ValidationResult,
)
from .templates import load_templates


def _to_log_data(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_to_log_data(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_log_data(item) for key, item in value.items()}
    return value


def _log_stage(stage: str, value: Any) -> None:
    data = _to_log_data(value)
    rendered = data if isinstance(data, str) else json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    logger.info("{}:\n{}", stage, rendered)


def _finish_analysis(result: AnalysisResult) -> AnalysisResult:
    _log_stage("agent.analyze() 返回", result)
    return result


def _format_number(value: int | float) -> str:
    """
    将数字转化为简洁字符串，100.0  → "100"，61.800000 → "61.8"
    :param value: 数字
    :return: 简洁字符串
    """
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def _format_value(value: int | float, metric: MetricSpec) -> str:
    """
    根据指标的formatter添加单位
    """
    formatted_value = abs(value) if metric.formatter == "abs_pct" else value
    number = _format_number(formatted_value)
    suffixes = {
        "number": "",
        "integer": "",
        "person": "人",
        "pct": "%",
        "pt": "pt",
        "wan": "万",
        "abs_pct": "%",
    }
    return number + suffixes[metric.formatter]


def _format_threshold(value: int | float, metric: MetricSpec) -> str:
    formatter = metric.threshold_formatter or metric.formatter
    threshold_metric = MetricSpec(
        label=metric.label,
        formatter=formatter,
        semantic=metric.semantic,
    )
    return _format_value(value, threshold_metric)


def _direction(value: int | float, metric: MetricSpec) -> str | None:
    if metric.semantic != "yoy":
        return None
    if value > 0:
        return "increase"
    if value < 0:
        return "decrease"
    return "flat"


def _compare(left: Scalar, operator: str, right: int | float | None) -> bool:
    """
    执行分类规则中的比较规则
    """
    if operator == "is_missing":
        return left is None
    if operator == "not_missing":
        return left is not None
    if left is None:
        return False
    return {
        "eq": left == right,
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
    }[operator]


class AnalysisAgent:
    """Small facade for the complete V4 workflow."""

    def __init__(
        self,
        template_dir: str | Path,
        *,
        expression_provider: ExpressionProvider | None = None,
        allow_invalid_expression: bool = False,
    ):
        self.templates = load_templates(template_dir) # 加载模板库
        self.expression_provider = expression_provider # 保存可选的LLM Provider
        self.validator = NarrativeValidator() # 创建文案事实校验器
        self.allow_invalid_expression = allow_invalid_expression

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        _log_stage("agent.analyze() 输入", request)

        routing = self._route(request) # 模板路由
        _log_stage("_route() 返回", routing)
        if routing.selected_template_id is None:
            return _finish_analysis(
                AnalysisResult(
                    status="needs_clarification",
                    routing=routing,
                )
            )
        template = self.templates[routing.selected_template_id] # 取得意图路由选中的模板
        parameters = self._resolve_parameters(template, request.parameters) # 支持覆盖场景配置yaml中的parameters默认值，使用用户提供的参数
        _log_stage("_resolve_parameters() 返回", parameters)
        facts = self._execute(template, request, parameters) # 执行模板，
        _log_stage("_execute() 返回", facts)
        groups = self._resolve_narrative_groups(template, facts)

        universe = [row.organization for row in request.dataset.rows]
        try:
            provider = self.expression_provider or DeepSeekExpressionProvider()
        except ConfigurationError as exc:
            return _finish_analysis(
                self._failed(str(exc), routing, template, parameters, facts)
            )
        try:
            blocks, validation = self._express(
                provider,
                template,
                facts,
                universe,
                groups,
                request.context,
            )
        except ExpressionError as exc:
            return _finish_analysis(
                self._failed(
                    str(exc),
                    routing,
                    template,
                    parameters,
                    facts,
                )
            )
        _log_stage(
            "_express() 返回",
            {"blocks": blocks, "validation": validation},
        )
        report = assemble_report(template.title, groups, blocks)
        _log_stage("assemble_report() 返回", report)
        return _finish_analysis(
            AnalysisResult(
                status="completed",
                routing=routing,
                template_id=template.id,
                template_version=template.version,
                parameters=parameters,
                facts=facts,
                blocks=blocks,
                signals=self._resolve_signals(template, facts),
                validation=validation,
                report_markdown=report,
            )
        )

    def _route(self, request: AnalysisRequest) -> RoutingResult:
        if request.template_id:
            if request.template_id in self.templates:
                template = self.templates[request.template_id]
                return RoutingResult(
                    selected_template_id=template.id,
                    candidates=[
                        RouteCandidate(
                            template_id=template.id,
                            title=template.title,
                            score=1,
                            matched_keywords=[],
                        )
                    ],
                )
            return RoutingResult(
                candidates=[],
                clarification=f"未知模板：{request.template_id}",
            )
        candidates: list[RouteCandidate] = []
        question = request.question.casefold()
        for template in self.templates.values():
            matched = [
                keyword
                for keyword in template.keywords
                if keyword.casefold() in question
            ]
            candidates.append(
                RouteCandidate(
                    template_id=template.id,
                    title=template.title,
                    score=sum(len(keyword) for keyword in matched),
                    matched_keywords=matched,
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.template_id))
        positive = [candidate for candidate in candidates if candidate.score > 0]
        if positive and (
            len(positive) == 1 or positive[0].score > positive[1].score
        ): # 如果有匹配的模板，并且只有一个模板得分最高，或者最高得分模板的分数高于第二名，则直接选中该模板
            return RoutingResult(
                selected_template_id=positive[0].template_id,
                candidates=candidates,
            )
        names = "、".join(candidate.title for candidate in (positive or candidates)) # 存在多个模板
        reason = "存在多个同分模板" if positive else "没有匹配到模板"
        return RoutingResult(
            candidates=candidates,
            clarification=f"{reason}，请明确选择：{names}",
        )

    @staticmethod
    def _resolve_parameters(
        template: AnalysisTemplate,
        supplied: dict[str, Scalar],
    ) -> dict[str, Scalar]:
        defaults = {name: spec.default for name, spec in template.parameters.items()}
        return {**defaults, **supplied} # 支持覆盖场景配置中的parameters默认值，使用用户提供的参数

    def _execute(
        self,
        template: AnalysisTemplate,
        request: AnalysisRequest,
        parameters: dict[str, Scalar],
    ) -> list[Fact]:
        facts: list[Fact] = []
        for step in template.steps:
            if isinstance(step, SummaryStep):
                for metric_id in step.metrics:
                    value = request.dataset.summary[metric_id]
                    metric = template.metrics[metric_id]
                    facts.append(
                        Fact(
                            fact_id=f"{template.id}.{step.id}.{metric_id}",
                            kind="summary",
                            section=step.title,
                            title=metric.label,
                            metric=metric_id,
                            raw_value=value,
                            display_value=_format_value(value, metric),
                            direction=_direction(value, metric),
                        )
                    )
            elif isinstance(step, RankStep):
                facts.append(self._rank(template, step, request))
            elif isinstance(step, ClassifyStep):
                facts.extend(self._classify(template, step, request, parameters))
        return facts

    @staticmethod
    def _rank(
        template: AnalysisTemplate,
        step: RankStep,
        request: AnalysisRequest,
    ) -> Fact:
        metric = template.metrics[step.metric]
        valued: list[tuple[int, str, int | float]] = []
        for index, row in enumerate(request.dataset.rows):
            value = row.metrics.get(step.metric)
            if value is None:
                continue
            valued.append((index, row.organization, value))
        factor = -1 if step.order == "desc" else 1
        valued.sort(key=lambda item: (factor * float(item[2]), item[0]))
        selected = valued[: step.limit]
        items = [
            FactItem(
                organization=name,
                raw_value=value,
                display_value=_format_value(value, metric),
            )
            for _, name, value in selected
        ]
        direction = "降序" if step.order == "desc" else "升序"
        return Fact(
            fact_id=f"{template.id}.{step.id}",
            kind="ranking",
            section=step.title,
            title=step.title,
            metric=step.metric,
            items=items,
            count=len(items),
            rule={"order": step.order, "limit": step.limit},
            rule_text=f"按{metric.label}{direction}取前{step.limit}名",
        )

    def _classify(
        self,
        template: AnalysisTemplate,
        step: ClassifyStep,
        request: AnalysisRequest,
        parameters: dict[str, Scalar],
    ) -> list[Fact]:
        result: list[Fact] = []
        assigned: set[int] = set()
        for band in step.bands:
            match, rules = band.rules()
            resolved = [self._resolve_rule(template, rule, parameters) for rule in rules]
            selected: list[tuple[int, Any]] = []
            for index, row in enumerate(request.dataset.rows):
                if step.exclusive and index in assigned:
                    continue
                matches = [
                    _compare(
                        row.metrics.get(rule["metric"]),
                        rule["operator"],
                        rule["threshold"],
                    )
                    for rule in resolved
                ]
                if all(matches) if match == "all" else any(matches):
                    selected.append((index, row))
                    if step.exclusive:
                        assigned.add(index)
            display_metric_id = band.display_metric or str(resolved[0]["metric"])
            display_metric = template.metrics[display_metric_id]
            display_threshold = None
            if band.display_threshold_param:
                raw_display_threshold = parameters[band.display_threshold_param]
                if not isinstance(raw_display_threshold, (int, float)):
                    raise ConfigurationError(
                        f"展示阈值必须是数值：{band.display_threshold_param}"
                    )
                display_threshold = {
                    "parameter": band.display_threshold_param,
                    "value": raw_display_threshold,
                    "display": _format_threshold(
                        raw_display_threshold,
                        display_metric,
                    ),
                }
            selected = self._order_selected(
                selected, display_metric_id, step.order
            )
            items: list[FactItem] = []
            for _, row in selected:
                raw = row.metrics.get(display_metric_id)
                numeric = raw
                items.append(
                    FactItem(
                        organization=row.organization,
                        raw_value=numeric,
                        display_value=(
                            _format_value(numeric, display_metric)
                            if numeric is not None
                            else None
                        ),
                    )
                )
            rule_payload = {
                "match": match,
                "exclusive": step.exclusive,
                "rules": resolved,
            }
            if display_threshold:
                rule_payload["display_threshold"] = display_threshold
            rule_text = self._rule_text(template, match, resolved)
            if band.rule_text:
                threshold_display = (
                    display_threshold["display"]
                    if display_threshold
                    else resolved[0].get("threshold_display") or ""
                )
                rule_text = band.rule_text.format(
                    threshold_display=threshold_display,
                )
            result.append(
                Fact(
                    fact_id=f"{template.id}.{step.id}.{band.id}",
                    kind="classification",
                    section=step.title,
                    title=band.label,
                    metric=display_metric_id,
                    items=items,
                    count=len(items),
                    rule=rule_payload,
                    rule_text=rule_text,
                    direction=self._classification_direction(
                        display_metric,
                        resolved,
                    ),
                )
            )
        return result

    @staticmethod
    def _classification_direction(
        metric: MetricSpec,
        rules: list[dict[str, Any]],
    ) -> str | None:
        if metric.semantic != "yoy":
            return None
        for rule in rules:
            threshold = rule.get("threshold")
            operator = rule.get("operator")
            if (
                isinstance(threshold, (int, float))
                and threshold <= 0
                and operator in {"lt", "lte"}
            ):
                return "decrease"
            if (
                isinstance(threshold, (int, float))
                and threshold >= 0
                and operator in {"gt", "gte"}
            ):
                return "increase"
        return None

    @staticmethod
    def _resolve_rule(
        template: AnalysisTemplate,
        rule: RuleSpec,
        parameters: dict[str, Scalar],
    ) -> dict[str, Any]:
        threshold: int | float | None = rule.threshold
        if rule.threshold_param:
            threshold = parameters[rule.threshold_param]
        threshold_display = None
        if threshold is not None:
            threshold_display = _format_threshold(
                threshold, template.metrics[rule.metric]
            )
        return {
            "metric": rule.metric,
            "operator": rule.operator,
            "threshold": threshold,
            "threshold_display": threshold_display,
        }

    @staticmethod
    def _order_selected(
        selected: list[tuple[int, Any]],
        metric_id: str,
        order: str,
    ) -> list[tuple[int, Any]]:
        if order == "source":
            return selected
        factor = -1 if order == "metric_desc" else 1

        def key(item: tuple[int, Any]) -> tuple[bool, float, int]:
            index, row = item
            value = row.metrics.get(metric_id)
            if value is None:
                return True, 0, index
            return False, factor * float(value), index

        return sorted(selected, key=key)

    @staticmethod
    def _rule_text(
        template: AnalysisTemplate,
        match: str,
        rules: list[dict[str, Any]],
    ) -> str:
        phrases = {
            "eq": "等于",
            "gt": "高于",
            "gte": "不低于",
            "lt": "低于",
            "lte": "不高于",
            "is_missing": "缺失",
            "not_missing": "非缺失",
        }
        parts = []
        for rule in rules:
            label = template.metrics[rule["metric"]].label
            parts.append(
                label
                + phrases[rule["operator"]]
                + (rule["threshold_display"] or "")
            )
        return ("且" if match == "all" else "或").join(parts)

    def _express(
        self,
        provider: ExpressionProvider,
        template: AnalysisTemplate,
        facts: list[Fact],
        universe: list[str],
        groups: list[NarrativeGroupSpec],
        context: dict[str, Scalar],
    ) -> tuple[list[NarrativeBlock], ValidationResult]:
        blocks = provider.express(
            template.title,
            facts,
            groups,
            context,
        )
        _log_stage("expression_provider.express() 返回", blocks)
        validation = self.validator.validate(
            facts,
            blocks,
            universe,
            groups,
            context,
        )
        if not validation.valid:
            message = "LLM 表达未通过校验：" + "；".join(validation.errors)
            if not self.allow_invalid_expression:
                raise ExpressionError(message)
            logger.warning("{}；已启用非阻断模式，继续主链路", message)
        return blocks, validation

    @staticmethod
    def _resolve_narrative_groups(
        template: AnalysisTemplate,
        facts: list[Fact],
    ) -> list[NarrativeGroupSpec]:
        fact_ids = {fact.fact_id for fact in facts}
        if not template.narrative_groups:
            return [
                NarrativeGroupSpec(
                    id=fact.fact_id,
                    title=fact.section,
                    fact_ids=[fact.fact_id],
                )
                for fact in facts
            ]
        groups: list[NarrativeGroupSpec] = []
        covered: set[str] = set()
        for group in template.narrative_groups:
            resolved_ids = [
                fact_id
                if fact_id.startswith(template.id + ".")
                else f"{template.id}.{fact_id}"
                for fact_id in group.fact_ids
            ]
            unknown = sorted(set(resolved_ids) - fact_ids)
            if unknown:
                raise ConfigurationError(
                    f"模板 {template.id} 文案分组引用未知事实："
                    + "、".join(unknown)
                )
            duplicate = sorted(set(resolved_ids) & covered)
            if duplicate:
                raise ConfigurationError(
                    f"模板 {template.id} 文案分组重复引用事实："
                    + "、".join(duplicate)
                )
            covered.update(resolved_ids)
            groups.append(
                NarrativeGroupSpec(
                    id=group.id,
                    title=group.title,
                    fact_ids=resolved_ids,
                )
            )
        missing = sorted(fact_ids - covered)
        if missing:
            raise ConfigurationError(
                f"模板 {template.id} 文案分组遗漏事实：" + "、".join(missing)
            )
        return groups

    @staticmethod
    def _resolve_signals(
        template: AnalysisTemplate,
        facts: list[Fact],
    ) -> dict[str, str]:
        fact_ids = {fact.fact_id for fact in facts}
        result: dict[str, str] = {}
        for name, configured in template.signals.items():
            fact_id = (
                configured
                if configured.startswith(template.id + ".")
                else f"{template.id}.{configured}"
            )
            if fact_id not in fact_ids:
                raise ConfigurationError(
                    f"模板 {template.id} 信号 {name} 引用未知事实：{fact_id}"
                )
            result[name] = fact_id
        return result

    @staticmethod
    def _failed(
        error: str,
        routing: RoutingResult | None = None,
        template: AnalysisTemplate | None = None,
        parameters: dict[str, Scalar] | None = None,
        facts: list[Fact] | None = None,
        blocks: list[NarrativeBlock] | None = None,
        validation: ValidationResult | None = None,
    ) -> AnalysisResult:
        return AnalysisResult(
            status="failed",
            routing=routing or RoutingResult(),
            template_id=template.id if template else None,
            template_version=template.version if template else None,
            parameters=parameters or {},
            facts=facts or [],
            blocks=blocks or [],
            validation=validation,
            errors=[error],
        )
