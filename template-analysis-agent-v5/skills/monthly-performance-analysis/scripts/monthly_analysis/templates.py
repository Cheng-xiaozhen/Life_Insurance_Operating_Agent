"""Load analysis templates from YAML files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


from .models import (
    AnalysisTemplate,
    BandSpec,
    ClassifyStep,
    ConfigurationError,
    ConditionsSpec,
    MetricSpec,
    NarrativeGroupSpec,
    ParameterSpec,
    RankStep,
    RuleSpec,
    StepSpec,
    SummaryStep,
)


def _load_band(raw: dict[str, Any]) -> BandSpec:
    values = dict(raw)
    conditions = values.get("conditions")
    if conditions:
        values["conditions"] = ConditionsSpec(
            match=conditions.get("match", "all"),
            rules=[RuleSpec(**rule) for rule in conditions["rules"]],
        )
    return BandSpec(**values)


def _load_step(raw: dict[str, Any]) -> StepSpec:
    values = dict(raw)
    if values["op"] == "summarize":
        return SummaryStep(**values)
    if values["op"] == "rank":
        return RankStep(**values)
    if values["op"] != "classify":
        raise ConfigurationError(f"不支持的模板操作：{values['op']}")
    values["bands"] = [_load_band(band) for band in values["bands"]]
    return ClassifyStep(**values)


def load_templates(directory: str | Path) -> dict[str, AnalysisTemplate]:
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ConfigurationError(f"模板目录不存在：{root}")
    result: dict[str, AnalysisTemplate] = {}
    for path in sorted(root.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        template = AnalysisTemplate(
            id=raw["id"],
            version=raw["version"],
            title=raw["title"],
            description=raw["description"],
            keywords=raw["keywords"],
            parameters={
                name: ParameterSpec(**spec)
                for name, spec in raw.get("parameters", {}).items()
            },
            metrics={
                name: MetricSpec(**spec) for name, spec in raw["metrics"].items()
            },
            steps=[_load_step(step) for step in raw["steps"]],
            signals={
                str(name): str(fact_id)
                for name, fact_id in raw.get("signals", {}).items()
            },
            narrative_groups=[
                NarrativeGroupSpec(**group)
                for group in raw.get("narrative_groups", [])
            ],
        )
        if template.id in result:
            raise ConfigurationError(f"模板 ID 重复：{template.id}")
        result[template.id] = template
    if not result:
        raise ConfigurationError(f"模板目录没有 YAML 模板：{root}")
    return result


if __name__ == "__main__":
    import json
    directory = Path(__file__).parents[1] / "templates"
    templates = load_templates(directory)
    for template_id, template in templates.items():
        print(f"模板ID: {template_id}")
        print(json.dumps(template.to_dict(), ensure_ascii=False, indent=2))
