"""Strict public contracts and the compact template DSL."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Scalar = str | int | float | bool | None
Formatter = Literal["number", "integer", "person", "pct", "pt", "wan"]
Operator = Literal["eq", "gt", "gte", "lt", "lte", "is_missing", "not_missing"]


class AgentError(RuntimeError):
    """Base error for configuration and execution failures."""


class TemplateError(AgentError):
    """Raised when a template is invalid."""


class ExecutionError(AgentError):
    """Raised when input data cannot satisfy a valid template."""


class ExpressionError(AgentError):
    """Raised when an expression provider cannot return usable output."""


class ConfigurationError(AgentError):
    """Raised for missing optional runtime configuration."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationRow(StrictModel):
    organization: str = Field(min_length=1)
    metrics: dict[str, Scalar]


class AnalysisDataset(StrictModel):
    summary: dict[str, Scalar]
    rows: list[OrganizationRow] = Field(min_length=1)

    @model_validator(mode="after")
    def organizations_are_unique(self) -> "AnalysisDataset":
        names = [row.organization for row in self.rows]
        if len(names) != len(set(names)):
            raise ValueError("机构名称必须唯一")
        return self


class AnalysisRequest(StrictModel):
    question: str = ""
    dataset: AnalysisDataset
    template_id: str | None = None
    parameters: dict[str, Scalar] = Field(default_factory=dict)
    provider: Literal["deterministic", "deepseek"] = "deterministic"

    @model_validator(mode="after")
    def has_routing_input(self) -> "AnalysisRequest":
        if not self.question.strip() and not self.template_id:
            raise ValueError("question 和 template_id 至少提供一个")
        return self


class ParameterSpec(StrictModel):
    type: Literal["string", "number"] = "string"
    required: bool = False
    default: Scalar = None
    description: str = ""

    @model_validator(mode="after")
    def default_matches_type(self) -> "ParameterSpec":
        if self.default is None:
            return self
        if self.type == "string" and not isinstance(self.default, str):
            raise ValueError("string 参数默认值必须是字符串")
        if self.type == "number" and (
            isinstance(self.default, bool)
            or not isinstance(self.default, (int, float))
        ):
            raise ValueError("number 参数默认值必须是数字")
        return self


class MetricSpec(StrictModel):
    label: str = Field(min_length=1)
    formatter: Formatter


class RuleSpec(StrictModel):
    metric: str
    operator: Operator
    threshold_param: str | None = None
    threshold: int | float | None = None

    @model_validator(mode="after")
    def threshold_shape(self) -> "RuleSpec":
        missing_operator = self.operator in {"is_missing", "not_missing"}
        supplied = int(self.threshold_param is not None) + int(self.threshold is not None)
        if missing_operator and supplied:
            raise ValueError(f"{self.operator} 不允许阈值")
        if not missing_operator and supplied != 1:
            raise ValueError(f"{self.operator} 必须且只能提供一个阈值")
        return self


class ConditionsSpec(StrictModel):
    match: Literal["all", "any"] = "all"
    rules: list[RuleSpec] = Field(min_length=1)


class BandSpec(StrictModel):
    id: str
    label: str
    metric: str | None = None
    operator: Operator | None = None
    threshold_param: str | None = None
    threshold: int | float | None = None
    conditions: ConditionsSpec | None = None
    display_metric: str | None = None

    @model_validator(mode="after")
    def has_one_condition_form(self) -> "BandSpec":
        direct_fields = (
            self.metric,
            self.operator,
            self.threshold_param,
            self.threshold,
        )
        has_direct = any(value is not None for value in direct_fields)
        if self.conditions and has_direct:
            raise ValueError("band 不能同时使用直接规则和 conditions")
        if not self.conditions:
            if self.metric is None or self.operator is None:
                raise ValueError("band 直接规则必须提供 metric 和 operator")
            RuleSpec(
                metric=self.metric,
                operator=self.operator,
                threshold_param=self.threshold_param,
                threshold=self.threshold,
            )
        return self

    def rules(self) -> tuple[str, list[RuleSpec]]:
        if self.conditions:
            return self.conditions.match, self.conditions.rules
        return "all", [
            RuleSpec(
                metric=str(self.metric),
                operator=self.operator,
                threshold_param=self.threshold_param,
                threshold=self.threshold,
            )
        ]


class SummaryStep(StrictModel):
    id: str
    title: str
    op: Literal["summarize"]
    metrics: list[str] = Field(min_length=1)


class RankStep(StrictModel):
    id: str
    title: str
    op: Literal["rank"]
    metric: str
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=5, gt=0)


class ClassifyStep(StrictModel):
    id: str
    title: str
    op: Literal["classify"]
    exclusive: bool = False
    order: Literal["source", "metric_asc", "metric_desc"] = "source"
    bands: list[BandSpec] = Field(min_length=1)


StepSpec = Annotated[
    SummaryStep | RankStep | ClassifyStep,
    Field(discriminator="op"),
]


class AnalysisTemplate(StrictModel):
    id: str
    version: str
    title: str
    description: str
    keywords: list[str] = Field(min_length=1)
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    metrics: dict[str, MetricSpec]
    steps: list[StepSpec] = Field(min_length=1)


class FactItem(StrictModel):
    organization: str
    raw_value: int | float | None = None
    display_value: str | None = None


class Fact(StrictModel):
    fact_id: str
    kind: Literal["summary", "ranking", "classification"]
    section: str
    title: str
    metric: str
    raw_value: int | float | None = None
    display_value: str | None = None
    items: list[FactItem] = Field(default_factory=list)
    count: int | None = None
    rule: dict[str, Any] | None = None
    rule_text: str | None = None
    required: bool = True


class NarrativeBlock(StrictModel):
    fact_id: str
    text: str = Field(min_length=1)


class ValidationResult(StrictModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class RouteCandidate(StrictModel):
    template_id: str
    title: str
    score: int = Field(ge=0)
    matched_keywords: list[str] = Field(default_factory=list)


class RoutingResult(StrictModel):
    selected_template_id: str | None = None
    candidates: list[RouteCandidate] = Field(default_factory=list)
    clarification: str | None = None


class ExpressionInfo(StrictModel):
    requested_provider: Literal["deterministic", "deepseek"]
    used_provider: Literal["deterministic", "deepseek"] | None = None
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    fallback_reason: str | None = None


class AnalysisResult(StrictModel):
    status: Literal["completed", "needs_clarification", "failed"]
    routing: RoutingResult
    template_id: str | None = None
    template_version: str | None = None
    parameters: dict[str, Scalar] = Field(default_factory=dict)
    input_digest: str
    facts: list[Fact] = Field(default_factory=list)
    blocks: list[NarrativeBlock] = Field(default_factory=list)
    expression: ExpressionInfo
    validation: ValidationResult | None = None
    report_markdown: str = ""
    errors: list[str] = Field(default_factory=list)
