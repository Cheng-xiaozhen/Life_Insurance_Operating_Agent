"""Plain data objects for the lightweight analysis workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Scalar = str | int | float | bool | None
Formatter = Literal[
    "number",
    "integer",
    "person",
    "pct",
    "pt",
    "wan",
    "abs_pct",
]
Operator = Literal["eq", "gt", "gte", "lt", "lte", "is_missing", "not_missing"]
Semantic = Literal["value", "yoy", "gap"]
Direction = Literal["increase", "decrease", "flat"]


class AgentError(RuntimeError):
    """Base error for configuration and execution failures."""


class ExpressionError(AgentError):
    """Raised when an expression provider cannot return usable output."""


class ConfigurationError(AgentError):
    """Raised for missing optional runtime configuration."""


class DataError(AgentError):
    """Raised when a configured input dataset cannot be normalized."""


class DataObject:
    def to_dict(self, *, exclude: set[str] | None = None) -> dict[str, Any]:
        result = asdict(self)
        for name in exclude or set():
            result.pop(name, None)
        return result


@dataclass
class OrganizationRow(DataObject):
    organization: str
    metrics: dict[str, Scalar]


@dataclass
class AnalysisDataset(DataObject):
    summary: dict[str, Scalar]
    rows: list[OrganizationRow]


@dataclass
class AnalysisRequest(DataObject):
    dataset: AnalysisDataset
    question: str = ""
    parameters: dict[str, Scalar] = field(default_factory=dict)
    template_id: str | None = None
    context: dict[str, Scalar] = field(default_factory=dict)


@dataclass
class ParameterSpec(DataObject):
    default: Scalar = None
    description: str = ""


@dataclass
class MetricSpec(DataObject):
    label: str
    formatter: Formatter
    semantic: Semantic = "value"
    threshold_formatter: Formatter | None = None


@dataclass
class RuleSpec(DataObject):
    metric: str
    operator: Operator
    threshold_param: str | None = None
    threshold: int | float | None = None


@dataclass
class ConditionsSpec(DataObject):
    rules: list[RuleSpec]
    match: Literal["all", "any"] = "all"


@dataclass
class BandSpec(DataObject):
    id: str
    label: str
    metric: str | None = None
    operator: Operator | None = None
    threshold_param: str | None = None
    threshold: int | float | None = None
    conditions: ConditionsSpec | None = None
    display_metric: str | None = None
    display_threshold_param: str | None = None
    rule_text: str | None = None

    def rules(self) -> tuple[str, list[RuleSpec]]:
        if self.conditions:
            return self.conditions.match, self.conditions.rules
        return "all", [
            RuleSpec(
                metric=str(self.metric),
                operator=self.operator,  # type: ignore[arg-type]
                threshold_param=self.threshold_param,
                threshold=self.threshold,
            )
        ]


@dataclass
class SummaryStep(DataObject):
    id: str
    title: str
    op: Literal["summarize"]
    metrics: list[str]


@dataclass
class RankStep(DataObject):
    id: str
    title: str
    op: Literal["rank"]
    metric: str
    order: Literal["asc", "desc"] = "desc"
    limit: int = 5


@dataclass
class ClassifyStep(DataObject):
    id: str
    title: str
    op: Literal["classify"]
    bands: list[BandSpec]
    exclusive: bool = False
    order: Literal["source", "metric_asc", "metric_desc"] = "source"


StepSpec = SummaryStep | RankStep | ClassifyStep


@dataclass
class NarrativeGroupSpec(DataObject):
    id: str
    title: str
    fact_ids: list[str]


@dataclass
class AnalysisTemplate(DataObject):
    id: str
    version: str
    title: str
    description: str
    keywords: list[str]
    metrics: dict[str, MetricSpec]
    steps: list[StepSpec]
    parameters: dict[str, ParameterSpec] = field(default_factory=dict)
    signals: dict[str, str] = field(default_factory=dict)
    narrative_groups: list[NarrativeGroupSpec] = field(default_factory=list)


@dataclass
class FactItem(DataObject):
    organization: str
    raw_value: int | float | None = None
    display_value: str | None = None


@dataclass
class Fact(DataObject):
    fact_id: str
    kind: Literal["summary", "ranking", "classification"]
    section: str
    title: str
    metric: str
    raw_value: int | float | None = None
    display_value: str | None = None
    items: list[FactItem] = field(default_factory=list)
    count: int | None = None
    rule: dict[str, Any] | None = None
    rule_text: str | None = None
    direction: Direction | None = None
    required: bool = True


@dataclass
class NarrativeBlock(DataObject):
    group_id: str
    fact_ids: list[str]
    text: str


@dataclass
class ValidationResult(DataObject):
    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class RouteCandidate(DataObject):
    template_id: str
    title: str
    score: int
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class RoutingResult(DataObject):
    selected_template_id: str | None = None
    candidates: list[RouteCandidate] = field(default_factory=list)
    clarification: str | None = None


@dataclass
class AnalysisResult(DataObject):
    status: Literal["completed", "needs_clarification", "failed"]
    routing: RoutingResult
    template_id: str | None = None
    template_version: str | None = None
    parameters: dict[str, Scalar] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)
    blocks: list[NarrativeBlock] = field(default_factory=list)
    signals: dict[str, str] = field(default_factory=dict)
    validation: ValidationResult | None = None
    report_markdown: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class SourceRecord(DataObject):
    scene_id: str
    path: str
    source_hash: str
    detail_rows: int


@dataclass
class MonthlyReportRequest(DataObject):
    data_dir: str
    report_month_name: str
    data_month_name: str
    quarter_name: str
    cutoff_date: str

    @property
    def context(self) -> dict[str, Scalar]:
        return {
            "report_month_name": self.report_month_name,
            "data_month_name": self.data_month_name,
            "quarter_name": self.quarter_name,
            "cutoff_date": self.cutoff_date,
        }


@dataclass
class MonthlyReportResult(DataObject):
    status: Literal["completed", "failed"]
    parameters: dict[str, Scalar] = field(default_factory=dict)
    sources: list[SourceRecord] = field(default_factory=list)
    scene_results: list[AnalysisResult] = field(default_factory=list)
    report_markdown: str = ""
    errors: list[str] = field(default_factory=list)
