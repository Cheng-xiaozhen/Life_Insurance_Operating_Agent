"""Deterministic routing, parameter binding, and template execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .expression import (
    DeepSeekExpressionProvider,
    DeterministicExpressionProvider,
    ExpressionProvider,
    NarrativeValidator,
    assemble_report,
)
from .models import (
    AgentError,
    AnalysisRequest,
    AnalysisResult,
    AnalysisTemplate,
    BandSpec,
    ClassifyStep,
    ConfigurationError,
    ExecutionError,
    ExpressionInfo,
    ExpressionError,
    Fact,
    FactItem,
    MetricSpec,
    NarrativeBlock,
    RankStep,
    RouteCandidate,
    RoutingResult,
    RuleSpec,
    Scalar,
    SummaryStep,
    ValidationResult,
)
from .templates import load_templates


def _input_digest(request: AnalysisRequest) -> str:
    payload = json.dumps(
        request.dataset.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _number(value: Scalar, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionError(f"{label} 必须是数字，实际为 {value!r}")
    return value


def _format_number(value: int | float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def _format_value(value: int | float, metric: MetricSpec) -> str:
    number = _format_number(value)
    suffixes = {
        "number": "",
        "integer": "",
        "person": "人",
        "pct": "%",
        "pt": "pt",
        "wan": "万",
    }
    return number + suffixes[metric.formatter]


def _compare(left: Scalar, operator: str, right: int | float | None) -> bool:
    if operator == "is_missing":
        return left is None
    if operator == "not_missing":
        return left is not None
    if left is None:
        return False
    left_number = _number(left, "机构指标")
    if right is None:  # validated templates cannot reach this branch
        raise ExecutionError(f"运算符 {operator} 缺少阈值")
    return {
        "eq": left_number == right,
        "gt": left_number > right,
        "gte": left_number >= right,
        "lt": left_number < right,
        "lte": left_number <= right,
    }[operator]


class AnalysisAgent:
    """Small facade for the complete V4 workflow."""

    def __init__(
        self,
        template_dir: str | Path,
        *,
        deepseek_provider: ExpressionProvider | None = None,
    ):
        self.templates = load_templates(template_dir)
        self.deepseek_provider = deepseek_provider
        self.validator = NarrativeValidator()
        self.deterministic_provider = DeterministicExpressionProvider()

    def validate_templates(self) -> dict[str, str]:
        return {
            template_id: template.version
            for template_id, template in self.templates.items()
        }

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        digest = _input_digest(request)
        expression = ExpressionInfo(requested_provider=request.provider)
        try:
            routing = self._route(request)
        except AgentError as exc:
            return self._failed(digest, expression, str(exc))
        if routing.selected_template_id is None:
            return AnalysisResult(
                status="needs_clarification",
                routing=routing,
                input_digest=digest,
                expression=expression,
            )
        template = self.templates[routing.selected_template_id]
        try:
            parameters, missing = self._resolve_parameters(template, request.parameters)
        except AgentError as exc:
            return self._failed(digest, expression, str(exc), routing, template)
        if missing:
            routing.clarification = "请补充必需参数：" + "、".join(missing)
            return AnalysisResult(
                status="needs_clarification",
                routing=routing,
                template_id=template.id,
                template_version=template.version,
                parameters=parameters,
                input_digest=digest,
                expression=expression,
            )
        try:
            facts = self._execute(template, request, parameters)
        except AgentError as exc:
            return self._failed(
                digest, expression, str(exc), routing, template, parameters
            )

        universe = [row.organization for row in request.dataset.rows]
        if request.provider == "deterministic":
            blocks = self.deterministic_provider.express(template.title, facts)
            validation = self.validator.validate(facts, blocks, universe)
            if not validation.valid:
                return self._failed(
                    digest,
                    expression,
                    "规则表达未通过校验：" + "；".join(validation.errors),
                    routing,
                    template,
                    parameters,
                    facts,
                    blocks,
                    validation,
                )
            expression.used_provider = "deterministic"
        else:
            try:
                provider = self.deepseek_provider or DeepSeekExpressionProvider()
            except ConfigurationError as exc:
                return self._failed(
                    digest, expression, str(exc), routing, template, parameters, facts
                )
            try:
                blocks, validation = self._express_or_fallback(
                    provider, template, facts, universe, expression
                )
            except ExpressionError as exc:
                return self._failed(
                    digest,
                    expression,
                    str(exc),
                    routing,
                    template,
                    parameters,
                    facts,
                )
        report = assemble_report(template.title, facts, blocks)
        return AnalysisResult(
            status="completed",
            routing=routing,
            template_id=template.id,
            template_version=template.version,
            parameters=parameters,
            input_digest=digest,
            facts=facts,
            blocks=blocks,
            expression=expression,
            validation=validation,
            report_markdown=report,
        )

    def _route(self, request: AnalysisRequest) -> RoutingResult:
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
        if request.template_id:
            if request.template_id not in self.templates:
                raise ExecutionError(f"未知模板：{request.template_id}")
            return RoutingResult(
                selected_template_id=request.template_id,
                candidates=candidates,
            )
        positive = [candidate for candidate in candidates if candidate.score > 0]
        if positive and (
            len(positive) == 1 or positive[0].score > positive[1].score
        ):
            return RoutingResult(
                selected_template_id=positive[0].template_id,
                candidates=candidates,
            )
        names = "、".join(candidate.title for candidate in (positive or candidates))
        reason = "存在多个同分模板" if positive else "没有匹配到模板"
        return RoutingResult(
            candidates=candidates,
            clarification=f"{reason}，请明确选择：{names}",
        )

    @staticmethod
    def _resolve_parameters(
        template: AnalysisTemplate,
        supplied: dict[str, Scalar],
    ) -> tuple[dict[str, Scalar], list[str]]:
        unknown = sorted(set(supplied) - set(template.parameters))
        if unknown:
            raise ExecutionError("模板未声明参数：" + "、".join(unknown))
        resolved: dict[str, Scalar] = {}
        missing: list[str] = []
        for name, spec in template.parameters.items():
            value = supplied[name] if name in supplied else spec.default
            if value is None:
                if spec.required:
                    missing.append(name)
                continue
            if spec.type == "string" and not isinstance(value, str):
                raise ExecutionError(f"参数 {name} 必须是字符串")
            if spec.type == "number":
                _number(value, f"参数 {name}")
            resolved[name] = value
        return resolved, missing

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
                    value = _number(
                        request.dataset.summary.get(metric_id),
                        f"汇总指标 {metric_id}",
                    )
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
            valued.append((index, row.organization, _number(value, step.metric)))
        if not valued:
            raise ExecutionError(f"排名指标没有有效数据：{step.metric}")
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
            selected = self._order_selected(
                selected, display_metric_id, step.order
            )
            items: list[FactItem] = []
            for _, row in selected:
                raw = row.metrics.get(display_metric_id)
                numeric = None if raw is None else _number(raw, display_metric_id)
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
                    rule_text=self._rule_text(template, match, resolved),
                )
            )
        return result

    @staticmethod
    def _resolve_rule(
        template: AnalysisTemplate,
        rule: RuleSpec,
        parameters: dict[str, Scalar],
    ) -> dict[str, Any]:
        threshold: int | float | None = rule.threshold
        if rule.threshold_param:
            if rule.threshold_param not in parameters:
                raise ExecutionError(f"阈值参数没有值：{rule.threshold_param}")
            threshold = _number(
                parameters[rule.threshold_param],
                f"阈值参数 {rule.threshold_param}",
            )
        threshold_display = None
        if threshold is not None:
            threshold_display = _format_value(
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
            return False, factor * float(_number(value, metric_id)), index

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

    def _express_or_fallback(
        self,
        provider: ExpressionProvider,
        template: AnalysisTemplate,
        facts: list[Fact],
        universe: list[str],
        expression: ExpressionInfo,
    ) -> tuple[list[NarrativeBlock], ValidationResult]:
        try:
            blocks = provider.express(template.title, facts)
            expression.model = getattr(provider, "model_name", None)
            expression.usage = dict(getattr(provider, "last_usage", {}) or {})
            validation = self.validator.validate(facts, blocks, universe)
            if validation.valid:
                expression.used_provider = "deepseek"
                return blocks, validation
            reason = "；".join(validation.errors)
        except Exception as exc:
            expression.model = getattr(provider, "model_name", None)
            expression.usage = dict(getattr(provider, "last_usage", {}) or {})
            reason = str(exc)
        expression.fallback_reason = reason
        blocks = self.deterministic_provider.express(template.title, facts)
        validation = self.validator.validate(facts, blocks, universe)
        if not validation.valid:
            raise ExpressionError(
                "规则降级表达未通过校验：" + "；".join(validation.errors)
            )
        expression.used_provider = "deterministic"
        return blocks, validation

    @staticmethod
    def _failed(
        digest: str,
        expression: ExpressionInfo,
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
            input_digest=digest,
            facts=facts or [],
            blocks=blocks or [],
            expression=expression,
            validation=validation,
            errors=[error],
        )
