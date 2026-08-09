"""Configuration registries and deterministic template validation."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from .errors import ConfigurationError, PlanError
from .models import (
    MetricDefinition,
    ParameterDefinition,
    QueryManifest,
    SceneManifest,
    SceneSpec,
    Scalar,
)


PARAM_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SUPPORTED_STEPS = {"summarize", "classify"}
SUPPORTED_OPERATORS = {
    "eq",
    "gt",
    "gte",
    "lt",
    "lte",
    "is_missing",
    "not_missing",
}
SUPPORTED_ORDERS = {"source", "metric_asc", "metric_desc"}
FORBIDDEN_SCENE_KEYS = {
    "column",
    "source",
    "row_sets",
    "prefix",
    "suffix",
    "subject",
    "comparison",
    "presentation",
    "render",
    "sql",
    "jinja",
}


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"找不到配置文件：{path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"YAML 无法解析：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"YAML 根节点必须是对象：{path}")
    return value


def parse_number(value: Any, label: str) -> int | float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "-"}:
        return None
    text = text.removesuffix("%").removesuffix("pt").strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise ConfigurationError(f"{label} 不是合法数值：{value!r}") from exc
    if not math.isfinite(number):
        raise ConfigurationError(f"{label} 必须是有限数值：{value!r}")
    return int(number) if number.is_integer() else number


def resolve_parameters(
    definitions: dict[str, ParameterDefinition],
    supplied: dict[str, Scalar],
    *,
    label: str,
) -> dict[str, Scalar]:
    resolved: dict[str, Scalar] = {}
    for name, definition in definitions.items():
        if name in supplied and supplied[name] not in {None, ""}:
            if not definition.overridable and definition.default != supplied[name]:
                raise PlanError(f"{label} 参数不允许覆盖：{name}")
            raw = supplied[name]
        elif definition.default is not None:
            raw = definition.default
        elif definition.required:
            raise PlanError(f"{label} 缺少必需参数：{name}")
        else:
            continue
        if definition.type == "number":
            value = parse_number(raw, f"参数 {name}")
            if value is None:
                raise PlanError(f"数值参数不能为空：{name}")
            resolved[name] = value
        else:
            resolved[name] = str(raw).strip()
    return resolved


def interpolate(value: str, parameters: dict[str, Scalar]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise ConfigurationError(f"参数没有值：{name}")
        return str(parameters[name])

    result = PARAM_PATTERN.sub(replace, value)
    if "${" in result:
        raise ConfigurationError(f"非法参数占位符：{value}")
    return result


def interpolate_tree(value: Any, parameters: dict[str, Scalar]) -> Any:
    if isinstance(value, str):
        return interpolate(value, parameters)
    if isinstance(value, list):
        return [interpolate_tree(item, parameters) for item in value]
    if isinstance(value, dict):
        return {key: interpolate_tree(item, parameters) for key, item in value.items()}
    return value


def iter_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from iter_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_keys(item)


class QueryRegistry:
    """Load immutable, controlled data-query contracts."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._queries: dict[str, QueryManifest] = {}
        for path in sorted(directory.glob("*.yaml")):
            manifest = QueryManifest.model_validate(load_yaml(path))
            if manifest.id in self._queries:
                raise ConfigurationError(f"数据查询 ID 重复：{manifest.id}")
            self._queries[manifest.id] = manifest
        if not self._queries:
            raise ConfigurationError(f"数据查询清单为空：{directory}")

    def get(self, query_id: str) -> QueryManifest:
        try:
            return self._queries[query_id]
        except KeyError as exc:
            raise ConfigurationError(f"未注册的数据查询：{query_id}") from exc

    def all(self) -> list[QueryManifest]:
        return list(self._queries.values())


class SourceProfileRegistry:
    """Load source-specific physical mappings outside templates."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._profiles: dict[str, dict[str, Any]] = {}
        for path in sorted(directory.glob("*.yaml")):
            value = load_yaml(path)
            meta = value.get("profile", {})
            profile_id = str(meta.get("id", ""))
            if not profile_id:
                raise ConfigurationError(f"数据源 profile 缺少 id：{path}")
            if profile_id in self._profiles:
                raise ConfigurationError(f"数据源 profile ID 重复：{profile_id}")
            self._profiles[profile_id] = value
        if not self._profiles:
            raise ConfigurationError(f"数据源 profile 为空：{directory}")

    def get(self, profile_id: str) -> dict[str, Any]:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ConfigurationError(f"未注册的数据源 profile：{profile_id}") from exc


class MetricRegistry:
    """Load source-independent semantic metric definitions."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._catalogs: dict[str, dict[str, MetricDefinition]] = {}
        for path in sorted(directory.glob("*.yaml")):
            value = load_yaml(path)
            catalog_id = str(value.get("catalog", {}).get("id", ""))
            if not catalog_id:
                raise ConfigurationError(f"指标目录缺少 catalog.id：{path}")
            metrics: dict[str, MetricDefinition] = {}
            for metric_id, definition in value.get("metrics", {}).items():
                metrics[metric_id] = MetricDefinition.model_validate(
                    {"id": metric_id, **definition}
                )
            if not metrics:
                raise ConfigurationError(f"指标目录为空：{path}")
            self._catalogs[catalog_id] = metrics

    def get(self, catalog_id: str) -> dict[str, MetricDefinition]:
        try:
            return self._catalogs[catalog_id]
        except KeyError as exc:
            raise ConfigurationError(f"未注册的指标目录：{catalog_id}") from exc


class TemplateRegistry:
    """Load scene manifests/specs and report recipes from one Skill package."""

    def __init__(
        self,
        assets_directory: Path,
        queries: QueryRegistry,
        metrics: MetricRegistry,
        profiles: SourceProfileRegistry,
    ):
        self.assets_directory = assets_directory
        self.queries = queries
        self.metrics = metrics
        self.profiles = profiles
        self._manifests: dict[str, SceneManifest] = {}
        self._scenes: dict[str, SceneSpec] = {}
        self._reports: dict[str, dict[str, Any]] = {}
        self._load_scenes()
        self._load_reports()

    def _load_scenes(self) -> None:
        for directory in sorted((self.assets_directory / "scenes").iterdir()):
            if not directory.is_dir():
                continue
            manifest = SceneManifest.model_validate(load_yaml(directory / "manifest.yaml"))
            spec_raw = load_yaml(directory / "scene.yaml")
            forbidden = sorted(set(iter_keys(spec_raw)) & FORBIDDEN_SCENE_KEYS)
            if forbidden:
                raise ConfigurationError(
                    f"场景 {manifest.id} 包含禁用字段：{', '.join(forbidden)}"
                )
            spec = SceneSpec.model_validate(spec_raw)
            if manifest.id != directory.name or spec.scene_id != manifest.id:
                raise ConfigurationError(
                    f"场景目录、manifest 和 spec ID 不一致：{directory}"
                )
            if manifest.id in self._scenes:
                raise ConfigurationError(f"场景 ID 重复：{manifest.id}")
            self._validate_scene(manifest, spec)
            self._manifests[manifest.id] = manifest
            self._scenes[manifest.id] = spec
        if not self._scenes:
            raise ConfigurationError("模板库没有场景")

    def _load_reports(self) -> None:
        for path in sorted((self.assets_directory / "reports").glob("*.yaml")):
            value = load_yaml(path)
            meta = value.get("report", {})
            report_id = str(meta.get("id", ""))
            if not report_id:
                raise ConfigurationError(f"报告配方缺少 report.id：{path}")
            if report_id in self._reports:
                raise ConfigurationError(f"报告 ID 重复：{report_id}")
            scene_entries = value.get("scenes", [])
            scene_ids = [
                str(item["id"]) if isinstance(item, dict) else str(item)
                for item in scene_entries
            ]
            if any(
                isinstance(item, dict)
                and set(item) - {"id", "required"}
                for item in scene_entries
            ):
                raise ConfigurationError(
                    f"报告 {report_id} 的场景条目包含未知字段"
                )
            if len(scene_ids) != len(set(scene_ids)):
                raise ConfigurationError(f"报告 {report_id} 的场景重复")
            unknown = [
                scene_id
                for scene_id in scene_ids
                if scene_id not in self._scenes
            ]
            if unknown:
                raise ConfigurationError(
                    f"报告 {report_id} 引用了未知场景：{', '.join(unknown)}"
                )
            self._reports[report_id] = value

    def _validate_scene(self, manifest: SceneManifest, spec: SceneSpec) -> None:
        query = self.queries.get(spec.query_ref)
        metric_catalog = self.metrics.get(manifest.id)
        if query.profile_id != manifest.id:
            raise ConfigurationError(
                f"场景 {manifest.id} 的查询 profile 不一致：{query.profile_id}"
            )
        profile_metric_ids = set(
            self.profiles.get(query.profile_id).get("metrics", {})
        )
        missing_profile_metrics = sorted(set(metric_catalog) - profile_metric_ids)
        if missing_profile_metrics:
            raise ConfigurationError(
                f"数据源 profile {query.profile_id} 缺少规范指标映射："
                + ", ".join(missing_profile_metrics)
            )
        step_ids: set[str] = set()
        for step in spec.steps:
            step_id = str(step.get("id", ""))
            step_type = str(step.get("type", ""))
            if not step_id or step_id in step_ids:
                raise ConfigurationError(f"场景 {manifest.id} 的步骤 ID 缺失或重复")
            step_ids.add(step_id)
            if step_type not in SUPPORTED_STEPS:
                raise ConfigurationError(
                    f"场景 {manifest.id} 使用未知步骤：{step_type}"
                )
            if step_type == "summarize":
                metric_ids = [str(item) for item in step.get("metrics", [])]
                if not metric_ids:
                    raise ConfigurationError(f"汇总步骤没有指标：{manifest.id}/{step_id}")
                for metric_id in metric_ids:
                    self._validate_metric_operation(
                        manifest.id, metric_catalog, metric_id, "summarize"
                    )
            else:
                order = step.get("display_order", "source")
                if order not in SUPPORTED_ORDERS:
                    raise ConfigurationError(
                        f"未知排序方式：{manifest.id}/{step_id}/{order}"
                    )
                bands = step.get("bands", [])
                if not bands:
                    raise ConfigurationError(f"分类步骤没有 bands：{manifest.id}/{step_id}")
                for band in bands:
                    self._validate_band(manifest.id, spec, step, band, metric_catalog)
        for signal_name, signal in spec.signals.items():
            source = signal.get("from", {})
            if source.get("step") not in step_ids:
                raise ConfigurationError(
                    f"信号引用未知步骤：{manifest.id}/{signal_name}"
                )

    def _validate_band(
        self,
        scene_id: str,
        spec: SceneSpec,
        step: dict[str, Any],
        band: dict[str, Any],
        metrics: dict[str, MetricDefinition],
    ) -> None:
        if not band.get("id"):
            raise ConfigurationError(f"分类 band 缺少 ID：{scene_id}/{step['id']}")
        conditions = band.get("conditions")
        if conditions:
            if conditions.get("match", "all") not in {"all", "any"}:
                raise ConfigurationError(f"分类条件 match 非法：{scene_id}/{band['id']}")
            rules = conditions.get("rules", [])
        else:
            rules = [
                {
                    "metric": band.get("metric", step.get("metric")),
                    "operator": band.get("operator"),
                    "threshold_param": band.get("threshold_param"),
                }
            ]
        if not rules:
            raise ConfigurationError(f"分类 band 没有规则：{scene_id}/{band['id']}")
        for rule in rules:
            metric_id = str(rule.get("metric", ""))
            self._validate_metric_operation(scene_id, metrics, metric_id, "classify")
            operator = rule.get("operator")
            if operator not in SUPPORTED_OPERATORS:
                raise ConfigurationError(
                    f"未知比较运算符：{scene_id}/{band['id']}/{operator}"
                )
            if operator not in {"is_missing", "not_missing"}:
                parameter = rule.get("threshold_param")
                if parameter not in spec.parameters and "value" not in rule:
                    raise ConfigurationError(
                        f"分类阈值参数未声明：{scene_id}/{band['id']}/{parameter}"
                    )
        display_metric = band.get("display_metric")
        if display_metric:
            self._validate_metric_operation(
                scene_id, metrics, str(display_metric), "classify"
            )
        display_parameter = band.get("display_threshold_param")
        if display_parameter and display_parameter not in spec.parameters:
            raise ConfigurationError(
                f"展示阈值参数未声明：{scene_id}/{band['id']}/{display_parameter}"
            )

    @staticmethod
    def _validate_metric_operation(
        scene_id: str,
        metrics: dict[str, MetricDefinition],
        metric_id: str,
        operation: str,
    ) -> None:
        if metric_id not in metrics:
            raise ConfigurationError(f"场景 {scene_id} 使用未知指标：{metric_id}")
        if operation not in metrics[metric_id].operations:
            raise ConfigurationError(
                f"指标不允许用于 {operation}：{scene_id}/{metric_id}"
            )

    def scene_manifest(self, scene_id: str) -> SceneManifest:
        try:
            return self._manifests[scene_id]
        except KeyError as exc:
            raise ConfigurationError(f"未知场景：{scene_id}") from exc

    def scene_spec(self, scene_id: str) -> SceneSpec:
        try:
            return self._scenes[scene_id]
        except KeyError as exc:
            raise ConfigurationError(f"未知场景：{scene_id}") from exc

    def scene_manifests(self) -> list[SceneManifest]:
        return list(self._manifests.values())

    def report(self, report_id: str) -> dict[str, Any]:
        try:
            return self._reports[report_id]
        except KeyError as exc:
            raise ConfigurationError(f"未知报告：{report_id}") from exc

    def reports(self) -> list[dict[str, Any]]:
        return list(self._reports.values())

    def report_scene_entries(self, report_id: str) -> list[dict[str, Any]]:
        report = self.report(report_id)
        return [
            {
                "id": str(item["id"]),
                "required": bool(item.get("required", True)),
            }
            if isinstance(item, dict)
            else {"id": str(item), "required": True}
            for item in report.get("scenes", [])
        ]

    def report_scene_ids(self, report_id: str) -> list[str]:
        return [item["id"] for item in self.report_scene_entries(report_id)]

    def validate_expression_style(self, style_id: str) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", style_id):
            raise ConfigurationError(f"表达风格 ID 非法：{style_id}")
        path = self.assets_directory / "styles" / f"{style_id}.yaml"
        value = load_yaml(path)
        actual_id = str(value.get("style", {}).get("id", ""))
        if actual_id != style_id:
            raise ConfigurationError(
                f"表达风格文件与 ID 不一致：{style_id}/{actual_id}"
            )
