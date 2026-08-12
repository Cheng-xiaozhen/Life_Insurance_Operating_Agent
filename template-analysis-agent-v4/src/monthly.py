"""Nine-scene monthly report orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from .data import CsvDatasetLoader, load_csv_profiles
from .engine import AnalysisAgent
from .expression import DeepSeekExpressionProvider, ExpressionProvider
from .models import (
    AgentError,
    AnalysisRequest,
    AnalysisResult,
    DataError,
    Fact,
    MonthlyReportRequest,
    MonthlyReportResult,
    SourceRecord,
)


def _load_report_recipe(path: str | Path) -> dict[str, Any]:
    recipe_path = Path(path).resolve()
    if not recipe_path.is_file():
        raise DataError(f"月报 Recipe 不存在：{recipe_path}")
    raw = yaml.safe_load(recipe_path.read_text(encoding="utf-8")) or {}
    report = raw.get("report")
    if not isinstance(report, dict):
        raise DataError(f"月报 Recipe 缺少 report：{recipe_path}")
    scenes = report.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 9:
        raise DataError("完整月报 Recipe 必须配置九个场景")
    return dict(report)


class MonthlyReportAgent:
    """Load all required sources, run nine scenes, and assemble one report."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        expression_provider: ExpressionProvider | None = None,
        template_dir: str | Path | None = None,
        allow_invalid_expression: bool = False,
    ):
        self.project_root = Path(project_root).resolve()
        self.template_dir = (
            Path(template_dir).resolve()
            if template_dir is not None
            else self.project_root / "templates"
        )
        self.profile_path = self.project_root / "profiles" / "monthly-performance.yaml"
        self.recipe_path = self.project_root / "reports" / "monthly-performance.yaml"
        self.expression_provider = expression_provider
        self.allow_invalid_expression = allow_invalid_expression

    def analyze(self, request: MonthlyReportRequest) -> MonthlyReportResult:
        context = request.context
        sources: list[SourceRecord] = []
        scene_results: list[AnalysisResult] = []
        try:
            recipe = _load_report_recipe(self.recipe_path)
            profiles = load_csv_profiles(self.profile_path)
            loader = CsvDatasetLoader(profiles)
            templates = AnalysisAgent(self.template_dir)
            scene_ids = [str(scene_id) for scene_id in recipe["scenes"]]
            missing_templates = [
                scene_id
                for scene_id in scene_ids
                if scene_id not in templates.templates
            ]
            if missing_templates:
                raise DataError(
                    "月报缺少场景模板：" + "、".join(missing_templates)
                )

            datasets = {}
            for scene_id in scene_ids:
                dataset, source = loader.load(
                    scene_id,
                    request.data_dir,
                    context,
                )
                datasets[scene_id] = dataset
                sources.append(source)
            logger.info("月报九个 CSV 已完成预检和标准化")

            provider = self.expression_provider or DeepSeekExpressionProvider()
            agent = AnalysisAgent(
                self.template_dir,
                expression_provider=provider,
                allow_invalid_expression=self.allow_invalid_expression,
            )
            for scene_id in scene_ids:
                logger.info("开始月报场景：{}", scene_id)
                result = agent.analyze(
                    AnalysisRequest(
                        template_id=scene_id,
                        question=agent.templates[scene_id].title,
                        dataset=datasets[scene_id],
                        context=context,
                    )
                )
                scene_results.append(result)
                if result.status != "completed":
                    errors = result.errors or [
                        result.routing.clarification or f"场景失败：{scene_id}"
                    ]
                    return MonthlyReportResult(
                        status="failed",
                        parameters=context,
                        sources=sources,
                        scene_results=scene_results,
                        errors=[f"{scene_id}：{error}" for error in errors],
                    )

            report = self._assemble_report(
                recipe,
                context,
                scene_results,
                agent,
            )
            logger.info("完整月度报告组装完成")
            return MonthlyReportResult(
                status="completed",
                parameters=context,
                sources=sources,
                scene_results=scene_results,
                report_markdown=report,
            )
        except (OSError, ValueError, AgentError) as exc:
            return MonthlyReportResult(
                status="failed",
                parameters=context,
                sources=sources,
                scene_results=scene_results,
                errors=[str(exc)],
            )

    @staticmethod
    def _assemble_report(
        recipe: dict[str, Any],
        context: dict[str, Any],
        results: list[AnalysisResult],
        agent: AnalysisAgent,
    ) -> str:
        title = str(recipe["title"]).format(**context)
        sections = [f"# {title}"]
        result_index = {
            str(result.template_id): result for result in results
        }
        callout = recipe.get("callout") or {}
        scene_id = str(callout.get("scene_id", ""))
        signal_name = str(callout.get("signal", ""))
        callout_result = result_index.get(scene_id)
        if callout_result and signal_name in callout_result.signals:
            fact_id = callout_result.signals[signal_name]
            fact = next(
                (item for item in callout_result.facts if item.fact_id == fact_id),
                None,
            )
            if isinstance(fact, Fact) and fact.items:
                organizations = "、".join(
                    item.organization for item in fact.items
                )
                callout_title = str(callout.get("title", "贺报"))
                text = str(callout.get("text", "{organizations}"))
                sections.append(
                    f"## {callout_title}\n\n"
                    + text.format(
                        organizations=organizations,
                        **context,
                    )
                )
        for configured_scene_id in recipe["scenes"]:
            result = result_index[str(configured_scene_id)]
            template = agent.templates[str(configured_scene_id)]
            body = "\n".join(block.text.strip() for block in result.blocks)
            sections.append(f"## {template.title}\n\n{body}")
        return "\n\n".join(sections).rstrip() + "\n"
