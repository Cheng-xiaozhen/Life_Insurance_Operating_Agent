"""Pydantic contracts shared across the V3 pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Scalar = str | int | float | bool | None


class StrictModel(BaseModel):
    """Forbid undeclared fields on public runtime contracts."""

    model_config = ConfigDict(extra="forbid")


class DataBinding(StrictModel):
    """Bind a controlled query or logical dataset to a concrete source."""

    adapter: Literal["csv", "memory"] = "csv"
    source: str | None = None
    profile_id: str | None = None
    records: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "DataBinding":
        if self.adapter == "csv" and not self.source:
            raise ValueError("CSV binding requires source")
        if self.adapter == "memory" and self.records is None:
            raise ValueError("memory binding requires records")
        return self


class AnalysisRequest(StrictModel):
    """User request accepted by the application service."""

    question: str
    report_id: str | None = None
    scene_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Scalar] = Field(default_factory=dict)
    data_bindings: dict[str, DataBinding] = Field(default_factory=dict)
    expression_provider: Literal["deterministic", "deepseek"] = "deterministic"
    output_style: str | None = None
    output_dir: str | None = None


class RoutingDecision(StrictModel):
    """A routing result. It intentionally contains no executable steps."""

    template_id: str | None = None
    report_id: str | None = None
    scene_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Scalar] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    missing_parameters: list[str] = Field(default_factory=list)
    clarification: str | None = None


class ParameterDefinition(StrictModel):
    type: Literal["string", "number"] = "string"
    required: bool = False
    default: Scalar = None
    overridable: bool = True
    description: str = ""


class SceneManifest(StrictModel):
    id: str
    version: str
    title: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    required_parameters: list[str] = Field(default_factory=list)


class SceneSpec(StrictModel):
    scene_id: str
    query_ref: str
    parameters: dict[str, ParameterDefinition] = Field(default_factory=dict)
    analysis_intent: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any]]
    signals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expression: dict[str, Any] = Field(default_factory=dict)


class CompiledScenePlan(StrictModel):
    scene_id: str
    version: str
    title: str
    required: bool = True
    query_ref: str
    parameters: dict[str, Scalar]
    steps: list[dict[str, Any]]
    signals: dict[str, dict[str, Any]]
    expression: dict[str, Any]


class CompiledAnalysisPlan(StrictModel):
    report_id: str | None = None
    report_version: str | None = None
    title: str
    parameters: dict[str, Scalar]
    scenes: list[CompiledScenePlan]
    callouts: list[dict[str, Any]] = Field(default_factory=list)


class QueryParameterDefinition(StrictModel):
    type: Literal["string", "number"] = "string"
    required: bool = False
    description: str = ""


class QuerySourceMetadata(StrictModel):
    kind: Literal["local_binding", "memory_binding"]
    owner: str
    contains_sensitive_data: bool = False


class QueryPermissionMetadata(StrictModel):
    classification: Literal["public", "internal", "confidential"]
    required_scopes: list[str] = Field(default_factory=list)


class QueryManifest(StrictModel):
    id: str
    version: str
    binding_id: str
    description: str
    parameters: dict[str, QueryParameterDefinition] = Field(default_factory=dict)
    output_schema: str
    grain: str
    handler: Literal["csv", "memory"]
    handler_ref: str
    profile_id: str
    source: QuerySourceMetadata
    permissions: QueryPermissionMetadata
    required: bool = True
    timeout_ms: int = Field(default=30_000, gt=0)


class MetricDefinition(StrictModel):
    id: str
    label: str
    unit: str
    formatter: str
    threshold_formatter: str | None = None
    semantic: Literal["value", "yoy", "gap"] = "value"
    operations: list[Literal["summarize", "classify"]]


class QueryRequest(StrictModel):
    query_id: str
    query_version: str
    parameters: dict[str, Scalar]
    binding: DataBinding


class CanonicalRow(StrictModel):
    source_index: int
    role: Literal["total", "detail"]
    organization: str | None = None
    values: dict[str, Scalar]


class CanonicalDataset(StrictModel):
    query_id: str
    query_version: str
    profile_id: str
    source: str
    source_hash: str
    rows: list[CanonicalRow]

    @property
    def total(self) -> CanonicalRow:
        return next(row for row in self.rows if row.role == "total")

    @property
    def details(self) -> list[CanonicalRow]:
        return [row for row in self.rows if row.role == "detail"]


class QueryResult(StrictModel):
    request: QueryRequest
    dataset: CanonicalDataset
    cache_hit: bool = False


class QueryExecutionRecord(StrictModel):
    query_id: str
    query_version: str
    binding_id: str
    adapter: Literal["csv", "memory"]
    profile_id: str
    parameters: dict[str, Scalar] = Field(default_factory=dict)
    source: str
    source_hash: str | None = None
    cache_hit: bool = False
    status: Literal["success", "failed", "timeout"]
    duration_ms: float = Field(ge=0)
    error: str | None = None


class Fact(StrictModel):
    fact_id: str
    scene_id: str
    step_id: str
    fact_type: Literal["summary", "classification"]
    metric_id: str
    label: str
    raw_value: Scalar = None
    display_value: str | None = None
    unit: str | None = None
    direction: Literal["increase", "decrease", "flat", "excess", "gap", "met"] | None = None
    condition: dict[str, Any] | None = None
    threshold: Scalar = None
    threshold_display: str | None = None
    organizations: list[str] = Field(default_factory=list)
    count: int | None = None
    query_id: str
    query_version: str
    source_rows: list[int] = Field(default_factory=list)
    required: bool = True


class FactBundle(StrictModel):
    scene_id: str
    scene_title: str
    status: Literal["ready", "insufficient_data"] = "ready"
    parameters: dict[str, Scalar] = Field(default_factory=dict)
    facts: list[Fact] = Field(default_factory=list)
    signals: dict[str, str] = Field(default_factory=dict)
    organization_universe: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class NarrativeBlock(StrictModel):
    fact_refs: list[str]
    markdown: str


class NarrativeDraft(StrictModel):
    scene_id: str
    blocks: list[NarrativeBlock]


class ExpressionResult(StrictModel):
    draft: NarrativeDraft
    raw_response: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    provider: str
    model: str | None = None


class ValidationReport(StrictModel):
    scene_id: str
    valid: bool
    errors: list[str] = Field(default_factory=list)


class AnalysisRunResult(StrictModel):
    run_id: str
    state_history: list[str]
    routing: RoutingDecision
    plan: CompiledAnalysisPlan
    fact_bundles: list[FactBundle]
    narratives: list[NarrativeDraft]
    validations: list[ValidationReport]
    report_markdown: str
    audit_path: str

    @property
    def report_path(self) -> Path:
        return Path(self.audit_path) / "report.md"
