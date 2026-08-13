from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import AgentContractError


@dataclass(frozen=True)
class ReportDefinition:
    report_id: str
    title: str
    path: Path
    scenario_desc: str
    intent_examples: tuple[str, ...]
    parameters: dict[str, Any]
    params_schema: dict[str, dict[str, Any]]
    scene_ids: tuple[str, ...]

    def routing_summary(self) -> dict[str, Any]:
        return {
            "id": self.report_id,
            "title": self.title,
            "scenario_desc": self.scenario_desc,
            "intent_examples": list(self.intent_examples),
            "parameters": deepcopy(self.parameters),
            "params_schema": deepcopy(self.params_schema),
            "scene_ids": list(self.scene_ids),
        }


class ReportCatalog:
    """Loads only the metadata required for report-level routing."""

    def __init__(self, reports_dir: str | Path):
        self.reports_dir = Path(reports_dir).resolve()
        self.references_dir = self.reports_dir.parent
        self._reports = self._load_reports()

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise AgentContractError(f"cannot load report metadata {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise AgentContractError(f"report must be a YAML mapping: {path}")
        return value

    def _load_reports(self) -> dict[str, ReportDefinition]:
        if not self.reports_dir.is_dir():
            raise AgentContractError(f"reports directory not found: {self.reports_dir}")
        reports: dict[str, ReportDefinition] = {}
        for path in sorted(self.reports_dir.glob("*.yaml")):
            raw = self._load_yaml(path)
            report_id = str(raw.get("id") or "").strip()
            if not report_id:
                raise AgentContractError(f"report id is required: {path}")
            if report_id in reports:
                raise AgentContractError(f"duplicate report id: {report_id}")
            routing = raw.get("routing") or {}
            if not isinstance(routing, dict):
                raise AgentContractError(f"routing must be a mapping: {path}")
            parameters = raw.get("parameters") or {}
            schema = raw.get("params_schema") or {}
            sections = raw.get("sections") or []
            if not isinstance(parameters, dict) or not isinstance(schema, dict):
                raise AgentContractError(f"parameters and params_schema must be mappings: {path}")
            if not isinstance(sections, list) or not sections:
                raise AgentContractError(f"report sections must be a non-empty list: {path}")
            scene_ids: list[str] = []
            for section in sections:
                if not isinstance(section, dict) or not section.get("scene"):
                    raise AgentContractError(f"each report section needs a scene id: {path}")
                scene_ids.append(str(section["scene"]))
            reports[report_id] = ReportDefinition(
                report_id=report_id,
                title=str(raw.get("title") or report_id),
                path=path.resolve(),
                scenario_desc=str(routing.get("scenario_desc") or ""),
                intent_examples=tuple(str(item) for item in routing.get("intent_examples") or []),
                parameters=deepcopy(parameters),
                params_schema={str(key): dict(value or {}) for key, value in schema.items()},
                scene_ids=tuple(scene_ids),
            )
        if not reports:
            raise AgentContractError(f"no report templates found: {self.reports_dir}")
        return reports

    def get(self, report_id: str) -> ReportDefinition:
        try:
            return self._reports[report_id]
        except KeyError as exc:
            raise AgentContractError(f"unknown report id: {report_id}") from exc

    def summaries(self) -> list[dict[str, Any]]:
        return [definition.routing_summary() for definition in self._reports.values()]

    def load_scene(self, report_id: str, scene_id: str) -> dict[str, Any]:
        report = self.get(report_id)
        if scene_id not in report.scene_ids:
            raise AgentContractError(
                f"scene '{scene_id}' is not part of report '{report_id}'"
            )
        path = self.references_dir / "scenes" / f"{scene_id}.yaml"
        scene = self._load_yaml(path)
        if str(scene.get("id") or "") != scene_id:
            raise AgentContractError(f"scene id does not match filename: {path}")
        return scene
