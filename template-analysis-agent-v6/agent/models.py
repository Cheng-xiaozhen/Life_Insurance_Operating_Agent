from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AgentContractError(ValueError):
    """Raised when an Agent dependency returns data outside its contract."""


class AgentState(str, Enum):
    RECEIVED = "received"
    ROUTING = "routing"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    PARAMETERS_BOUND = "parameters_bound"
    EXECUTING_FACTS = "executing_facts"
    ANALYZING_SCENES = "analyzing_scenes"
    ASSEMBLING_REPORT = "assembling_report"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentRequest:
    message: str
    data_context_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not self.message or not self.message.strip():
            raise AgentContractError("message must not be empty")


@dataclass
class RouteDecision:
    action: str
    report_id: str | None = None
    confidence: float = 0.0
    extracted_params: dict[str, Any] = field(default_factory=dict)
    missing_params: list[str] = field(default_factory=list)
    clarification: str | None = None
    reason: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RouteDecision":
        if not isinstance(value, dict):
            raise AgentContractError("route output must be an object")
        try:
            confidence = float(value.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise AgentContractError("route confidence must be numeric") from exc
        return cls(
            action=str(value.get("action", "")),
            report_id=(str(value["report_id"]) if value.get("report_id") else None),
            confidence=confidence,
            extracted_params=dict(value.get("extracted_params") or {}),
            missing_params=[str(item) for item in value.get("missing_params") or []],
            clarification=(
                str(value["clarification"]) if value.get("clarification") else None
            ),
            reason=str(value.get("reason") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BoundParameters:
    values: dict[str, Any]
    missing: list[str] = field(default_factory=list)
    clarification: str | None = None


@dataclass
class SceneNarrativeResult:
    scene_id: str
    content: str
    used_fact_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SceneNarrativeResult":
        if not isinstance(value, dict):
            raise AgentContractError("scene analysis output must be an object")
        return cls(
            scene_id=str(value.get("scene_id") or ""),
            content=str(value.get("content") or ""),
            used_fact_ids=[str(item) for item in value.get("used_fact_ids") or []],
            warnings=[str(item) for item in value.get("warnings") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneAnalysisOutcome:
    result: SceneNarrativeResult
    used_fallback: bool = False
    warning: str | None = None


@dataclass
class SessionState:
    report_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    pending_parameters: list[str] = field(default_factory=list)
    data_context_id: str | None = None


@dataclass(frozen=True)
class AgentEvent:
    type: str
    run_id: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentResult:
    status: str
    message: str
    run_id: str
    report_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    facts_path: str | None = None
    report_path: str | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "AgentResult":
        return cls(
            status=str(value["status"]),
            message=str(value["message"]),
            run_id=str(value["run_id"]),
            report_id=(str(value["report_id"]) if value.get("report_id") else None),
            parameters=dict(value.get("parameters") or {}),
            facts_path=(str(value["facts_path"]) if value.get("facts_path") else None),
            report_path=(str(value["report_path"]) if value.get("report_path") else None),
            warnings=[str(item) for item in value.get("warnings") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
