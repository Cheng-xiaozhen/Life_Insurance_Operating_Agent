"""Load and semantically validate one-file analysis templates."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import (
    AnalysisTemplate,
    ClassifyStep,
    RankStep,
    SummaryStep,
    TemplateError,
)


def _unique(values: list[str], label: str, path: Path) -> None:
    if len(values) != len(set(values)):
        raise TemplateError(f"{path}: {label} 不能重复")


def _validate_template(template: AnalysisTemplate, path: Path) -> None:
    _unique([step.id for step in template.steps], "步骤 ID", path)
    known_metrics = set(template.metrics)
    known_parameters = set(template.parameters)
    for step in template.steps:
        if isinstance(step, SummaryStep):
            referenced = step.metrics
        elif isinstance(step, RankStep):
            referenced = [step.metric]
        elif isinstance(step, ClassifyStep):
            _unique([band.id for band in step.bands], f"步骤 {step.id} 的 band ID", path)
            referenced = []
            for band in step.bands:
                if band.display_metric:
                    referenced.append(band.display_metric)
                _, rules = band.rules()
                for rule in rules:
                    referenced.append(rule.metric)
                    if rule.threshold_param:
                        if rule.threshold_param not in known_parameters:
                            raise TemplateError(
                                f"{path}: 未声明参数 {rule.threshold_param}"
                            )
                        if template.parameters[rule.threshold_param].type != "number":
                            raise TemplateError(
                                f"{path}: 阈值参数 {rule.threshold_param} 必须是 number"
                            )
        else:  # pragma: no cover - discriminated union already prevents this
            raise TemplateError(f"{path}: 未知步骤")
        unknown = sorted(set(referenced) - known_metrics)
        if unknown:
            raise TemplateError(f"{path}: 未声明指标 {', '.join(unknown)}")


def load_templates(directory: str | Path) -> dict[str, AnalysisTemplate]:
    root = Path(directory).resolve()
    if not root.is_dir():
        raise TemplateError(f"模板目录不存在：{root}")
    result: dict[str, AnalysisTemplate] = {}
    for path in sorted(root.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            template = AnalysisTemplate.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError, TypeError) as exc:
            raise TemplateError(f"模板加载失败 {path}: {exc}") from exc
        _validate_template(template, path)
        if template.id in result:
            raise TemplateError(f"模板 ID 重复：{template.id}")
        result[template.id] = template
    if not result:
        raise TemplateError(f"模板目录没有 YAML 文件：{root}")
    return result
