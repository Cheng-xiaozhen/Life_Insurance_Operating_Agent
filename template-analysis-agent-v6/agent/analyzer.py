from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from .models import (
    AgentContractError,
    SceneAnalysisOutcome,
    SceneNarrativeResult,
)


def _format_scene_text(value: Any, parameters: Mapping[str, Any]) -> str:
    text = str(value)
    for name, parameter in parameters.items():
        text = text.replace(f"${{{name}}}", str(parameter))
    return text


def compact_scene_facts(
    scene_result: Mapping[str, Any],
    scene_definition: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only facts that are useful and safe for scene narrative generation."""
    facts = scene_result.get("facts") or {}
    actions = scene_definition.get("analysis") or []
    metrics = scene_definition.get("metrics") or {}
    parameters = scene_definition.get("parameters") or {}
    compact: dict[str, Any] = {}
    for action in actions:
        action_id = str(action["id"])
        if action_id not in facts:
            raise AgentContractError(
                f"scene result is missing fact '{action_id}' for '{scene_result.get('scene_id')}'"
            )
        action_type = str(action["type"])
        entry: dict[str, Any] = {
            "type": action_type,
            "title": _format_scene_text(action.get("title", action_id), parameters),
        }
        if action_type == "summary":
            entry["values"] = {
                str(field): {
                    "label": (metrics.get(str(field)) or {}).get("label", str(field)),
                    "unit": (metrics.get(str(field)) or {}).get("unit", ""),
                    "value": facts[action_id][str(field)]["value"],
                    "display": facts[action_id][str(field)]["display"],
                }
                for field in action["fields"]
            }
        elif action_type == "select":
            display_field = str(action.get("display_field") or action["where"]["field"])
            entry["display_field"] = display_field
            entry["rows"] = [
                {
                    "organization": row["organization"],
                    "value": row["values"][display_field]["value"],
                    "display": row["values"][display_field]["display"],
                }
                for row in facts[action_id]
            ]
        else:
            raise AgentContractError(f"unsupported compact fact type: {action_type}")
        compact[action_id] = entry
    return compact


def build_scene_context(
    scene_result: Mapping[str, Any],
    scene_definition: Mapping[str, Any],
    report_parameters: Mapping[str, Any],
) -> dict[str, Any]:
    compact = compact_scene_facts(scene_result, scene_definition)
    return {
        "scene_id": scene_result["scene_id"],
        "title": scene_definition.get("title", scene_result.get("title", "")),
        "description": scene_definition.get("description", ""),
        "metrics": deepcopy(scene_definition.get("metrics") or {}),
        "narrative_instruction": deepcopy(scene_definition.get("narrative") or {}),
        "report_parameters": dict(report_parameters),
        "facts": compact,
        "baseline_narrative": str(scene_result.get("narrative") or ""),
    }


_PARENTHESIZED_LABEL = re.compile(
    r"(?:^|[：:、，,；;\s])([A-Za-z\u4e00-\u9fff]{2,20})[（(]([^）)]+)[）)]",
    re.MULTILINE,
)


class SceneAnalyzer:
    def __init__(self, llm_client: Any, max_attempts: int = 2):
        self.llm_client = llm_client
        self.max_attempts = max_attempts

    @staticmethod
    def _validate(
        result: SceneNarrativeResult,
        context: Mapping[str, Any],
    ) -> None:
        scene_id = str(context["scene_id"])
        if result.scene_id != scene_id:
            raise AgentContractError(
                f"scene analysis id '{result.scene_id}' does not match '{scene_id}'"
            )
        if not result.content.strip():
            raise AgentContractError("scene analysis content must not be empty")
        fact_ids = set(context["facts"])
        if not result.used_fact_ids:
            raise AgentContractError("scene analysis must cite at least one fact id")
        unknown_fact_ids = sorted(set(result.used_fact_ids) - fact_ids)
        if unknown_fact_ids:
            raise AgentContractError(
                f"scene analysis cites unknown facts: {', '.join(unknown_fact_ids)}"
            )

        organizations = {
            row["organization"]
            for fact in context["facts"].values()
            for row in fact.get("rows", [])
        }
        mentions = {
            label
            for label, shown in _PARENTHESIZED_LABEL.findall(result.content)
            if re.match(r"[-+]?\d", shown.strip())
            and not shown.strip().endswith("家")
            and (label in organizations or label.endswith(("公司", "分公司", "机构")))
        }
        unknown_organizations = sorted(mentions - organizations)
        if unknown_organizations:
            raise AgentContractError(
                "scene analysis contains organizations outside compact facts: "
                + ", ".join(unknown_organizations)
            )

    def analyze(self, context: dict[str, Any]) -> SceneAnalysisOutcome:
        errors: list[str] = []
        for _ in range(self.max_attempts):
            try:
                raw = self.llm_client.analyze_scene(
                    context,
                    feedback=errors[-1] if errors else None,
                )
                result = (
                    raw
                    if isinstance(raw, SceneNarrativeResult)
                    else SceneNarrativeResult.from_mapping(raw)
                )
                self._validate(result, context)
                return SceneAnalysisOutcome(result=result)
            except Exception as exc:  # model and contract failures share the same fallback path
                errors.append(str(exc))

        warning = f"场景 {context['scene_id']} 的模型分析失败，已使用确定性文案：{errors[-1]}"
        fallback = SceneNarrativeResult(
            scene_id=str(context["scene_id"]),
            content=str(context["baseline_narrative"]),
            used_fact_ids=list(context["facts"]),
            warnings=[warning],
        )
        return SceneAnalysisOutcome(result=fallback, used_fallback=True, warning=warning)


class ReportAssembler:
    def __init__(self, render_markdown: Any):
        self._render_markdown = render_markdown

    def render(
        self,
        deterministic_result: Mapping[str, Any],
        narratives: Mapping[str, SceneNarrativeResult],
    ) -> str:
        assembled = deepcopy(deterministic_result)
        for scene in assembled["scenes"]:
            scene_id = scene["scene_id"]
            if scene_id not in narratives:
                raise AgentContractError(f"missing narrative for scene: {scene_id}")
            scene["narrative"] = narratives[scene_id].content
        return self._render_markdown(assembled)
