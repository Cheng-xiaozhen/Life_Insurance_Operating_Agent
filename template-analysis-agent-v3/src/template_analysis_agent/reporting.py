"""Deterministic report assembly and local audit recording."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    AnalysisRequest,
    CompiledAnalysisPlan,
    ExpressionResult,
    FactBundle,
    NarrativeDraft,
    QueryExecutionRecord,
    QueryManifest,
    RoutingDecision,
    ValidationReport,
)


class ReportAssembler:
    """Own headings, scene order, callouts, and insufficient-data notices."""

    def assemble(
        self,
        plan: CompiledAnalysisPlan,
        bundles: list[FactBundle],
        drafts: list[NarrativeDraft],
    ) -> str:
        bundle_index = {bundle.scene_id: bundle for bundle in bundles}
        draft_index = {draft.scene_id: draft for draft in drafts}
        sections = [f"# {plan.title}"]
        for callout in plan.callouts:
            scene_id = str(callout["scene_id"])
            signal_name = str(callout["signal"])
            bundle = bundle_index.get(scene_id)
            if not bundle or signal_name not in bundle.signals:
                continue
            fact_id = bundle.signals[signal_name]
            fact = next(
                (item for item in bundle.facts if item.fact_id == fact_id),
                None,
            )
            if not fact or not fact.organizations:
                continue
            title = str(callout.get("title", "重点信号"))
            organizations = "、".join(fact.organizations)
            sections.append(
                f"## {title}\n\n- {fact.label}达到目标的机构：{organizations}。"
            )
        for scene in plan.scenes:
            bundle = bundle_index[scene.scene_id]
            if bundle.status == "insufficient_data":
                detail = "；".join(bundle.errors) or "必需数据不可用"
                sections.append(
                    f"## {scene.title}\n\n- 数据不足，未生成分析：{detail}。"
                )
                continue
            draft = draft_index[scene.scene_id]
            body = "\n".join(block.markdown.strip() for block in draft.blocks)
            sections.append(f"## {scene.title}\n\n{body}")
        return "\n\n".join(sections).rstrip() + "\n"


class AuditRecorder:
    """Persist one complete, reproducible local run."""

    def __init__(self, output_root: Path):
        self.output_root = output_root

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    def record(
        self,
        *,
        run_id: str,
        request: AnalysisRequest,
        routing: RoutingDecision,
        plan: CompiledAnalysisPlan,
        query_manifests: list[QueryManifest],
        query_records: list[QueryExecutionRecord],
        bundles: list[FactBundle],
        expressions: list[ExpressionResult],
        validations: list[ValidationReport],
        report_markdown: str,
        state_history: list[str],
    ) -> Path:
        run_directory = (self.output_root / run_id).resolve()
        run_directory.mkdir(parents=True, exist_ok=False)
        self._write_json(run_directory / "request.json", request.model_dump(mode="json"))
        self._write_json(run_directory / "routing.json", routing.model_dump(mode="json"))
        self._write_json(run_directory / "plan.json", plan.model_dump(mode="json"))
        self._write_json(
            run_directory / "query-manifest.json",
            {
                "contracts": [
                    item.model_dump(mode="json") for item in query_manifests
                ],
                "calls": [
                    item.model_dump(mode="json") for item in query_records
                ],
            },
        )
        self._write_json(
            run_directory / "facts.json",
            [bundle.model_dump(mode="json") for bundle in bundles],
        )
        self._write_json(
            run_directory / "model-response.json",
            [result.model_dump(mode="json") for result in expressions],
        )
        self._write_json(
            run_directory / "validation.json",
            {
                "state_history": state_history,
                "scenes": [
                    report.model_dump(mode="json") for report in validations
                ],
            },
        )
        (run_directory / "report.md").write_text(
            report_markdown,
            encoding="utf-8",
        )
        return run_directory
