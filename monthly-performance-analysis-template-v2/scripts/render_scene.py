#!/usr/bin/env python3
"""Execute hand-authored analysis steps and render auditable Markdown."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


MISSING_MARKERS = {"", "-"}
PARAM_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SUPPORTED_STEP_TYPES = {"summarize", "classify"}
SUPPORTED_DISPLAY_ORDERS = {"source", "metric_asc", "metric_desc"}
SUPPORTED_OPERATORS = {
    "eq",
    "gt",
    "gte",
    "lt",
    "lte",
    "is_missing",
    "not_missing",
}
SUPPORTED_PRESENTATION_STYLES = {
    "threshold_list",
    "organization_text",
    "organization_threshold",
    "count_list",
    "count_text",
}


class SceneError(RuntimeError):
    """Raised when configuration or source data cannot be executed safely."""


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SceneError(f"找不到配置文件：{path}")
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise SceneError(f"YAML 根节点必须是对象：{path}")
    return value


def parse_number(value: Any, label: str) -> int | float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in MISSING_MARKERS:
        return None
    text = text.removesuffix("%").removesuffix("pt").strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise SceneError(f"无法把 {label} 转换为数值：{value!r}") from exc
    if not math.isfinite(number):
        raise SceneError(f"{label} 必须是有限数值：{value!r}")
    return int(number) if number.is_integer() else number


def resolve_parameters(
    definitions: dict[str, Any], supplied: dict[str, Any], label: str
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for name, definition in definitions.items():
        if name in supplied and str(supplied[name]).strip():
            value = supplied[name]
        elif "default" in definition:
            value = definition["default"]
        elif definition.get("required"):
            raise SceneError(f"{label} 缺少必需参数：{name}")
        else:
            continue

        parameter_type = definition.get("type", "string")
        if parameter_type == "number":
            number = parse_number(value, f"参数 {name}")
            if number is None:
                raise SceneError(f"数值参数不能为空：{name}")
            resolved[name] = number
        elif parameter_type == "string":
            resolved[name] = str(value).strip()
        else:
            raise SceneError(f"不支持的参数类型：{name}/{parameter_type}")
    return resolved


def interpolate(value: str, params: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise SceneError(f"参数没有值：{name}")
        return str(params[name])

    resolved = PARAM_PATTERN.sub(replace, value)
    if "${" in resolved:
        raise SceneError(f"非法参数占位符：{value}")
    return resolved


def interpolate_tree(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return interpolate(value, params)
    if isinstance(value, list):
        return [interpolate_tree(item, params) for item in value]
    if isinstance(value, dict):
        return {key: interpolate_tree(item, params) for key, item in value.items()}
    return value


def referenced_metrics(steps: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for step in steps:
        step_type = step.get("type")
        if step_type not in SUPPORTED_STEP_TYPES:
            raise SceneError(f"不支持的分析步骤类型：{step_type!r}")
        if step_type == "summarize":
            for item in step.get("metrics", []):
                result.add(str(item["metric"]))
        else:
            if step.get("metric"):
                result.add(str(step["metric"]))
            for band in step.get("bands", []):
                if band.get("metric"):
                    result.add(str(band["metric"]))
                if band.get("display_metric"):
                    result.add(str(band["display_metric"]))
                conditions = band.get("conditions")
                if conditions:
                    for rule in conditions.get("rules", []):
                        result.add(str(rule["metric"]))
                else:
                    metric_id = band.get("metric", step.get("metric"))
                    if not metric_id:
                        raise SceneError(f"分类分组缺少指标：{band.get('id', '<unknown>')}")
                    result.add(str(metric_id))
    return result


def load_rows(
    path: Path,
    catalog: dict[str, Any],
    metric_ids: set[str],
    params: dict[str, Any],
    encoding: str,
) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SceneError(f"找不到数据文件：{path}")

    metric_catalog = catalog["metrics"]
    unknown_metrics = sorted(metric_ids - set(metric_catalog))
    if unknown_metrics:
        raise SceneError(f"指标目录中不存在：{', '.join(unknown_metrics)}")

    definitions: dict[str, Any] = dict(catalog["dimensions"])
    definitions.update({metric_id: metric_catalog[metric_id] for metric_id in metric_ids})
    resolved_columns = {
        name: interpolate(definition["column"], params)
        for name, definition in definitions.items()
    }
    if len(resolved_columns.values()) != len(set(resolved_columns.values())):
        raise SceneError("多个规范字段解析到了同一 CSV 列")

    with path.open("r", encoding=encoding, newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise SceneError(f"CSV 没有表头：{path}")
        headers = [header.strip() for header in reader.fieldnames]
        if len(headers) != len(set(headers)):
            raise SceneError(f"CSV 清理后出现重复表头：{path}")
        missing = [column for column in resolved_columns.values() if column not in headers]
        if missing:
            raise SceneError(f"CSV 缺少必需列：{', '.join(missing)}")

        rows: list[dict[str, Any]] = []
        for index, raw in enumerate(reader):
            normalized = {
                str(header).strip(): value.strip() if isinstance(value, str) else value
                for header, value in raw.items()
            }
            canonical: dict[str, Any] = {"__source_index__": index}
            for name, definition in definitions.items():
                raw_value = normalized.get(resolved_columns[name])
                if definition["type"] == "number":
                    canonical[name] = parse_number(
                        raw_value, f"第{index + 2}行/{resolved_columns[name]}"
                    )
                elif definition["type"] == "string":
                    canonical[name] = "" if raw_value is None else str(raw_value).strip()
                else:
                    raise SceneError(f"不支持的字段类型：{name}/{definition['type']}")
            rows.append(canonical)
    return rows


def compare(left: Any, operator: str, right: Any) -> bool:
    if operator not in SUPPORTED_OPERATORS:
        raise SceneError(f"不支持的比较运算符：{operator}")
    if operator == "is_missing":
        return left is None
    if operator == "not_missing":
        return left is not None
    if left is None or right is None:
        return False
    operations: dict[str, Callable[[Any, Any], bool]] = {
        "eq": lambda a, b: a == b,
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
    }
    return operations[operator](left, right)


def select_row_sets(
    rows: list[dict[str, Any]], catalog: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total_definition = catalog["row_sets"]["total"]
    total_matches = [
        row
        for row in rows
        if compare(
            row[total_definition["field"]],
            total_definition["operator"],
            total_definition["value"],
        )
    ]
    if len(total_matches) != 1:
        raise SceneError(f"全系统汇总行必须恰好1行，实际为{len(total_matches)}行")
    total = total_matches[0]
    organization_field = catalog["row_sets"]["details"]["organization_field"]
    details = [
        row
        for row in rows
        if row["__source_index__"] != total["__source_index__"]
        and str(row[organization_field]).strip()
    ]
    return total, details


def order_rows(
    rows: list[dict[str, Any]], metric_id: str, display_order: str
) -> list[dict[str, Any]]:
    if display_order == "source":
        return sorted(rows, key=lambda row: row["__source_index__"])
    if display_order == "metric_asc":
        return sorted(rows, key=lambda row: (row[metric_id], row["__source_index__"]))
    if display_order == "metric_desc":
        return sorted(rows, key=lambda row: (-float(row[metric_id]), row["__source_index__"]))
    raise SceneError(f"不支持的展示顺序：{display_order}")


def execute_summary(
    step: dict[str, Any],
    total: dict[str, Any],
    metric_catalog: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    metrics = []
    for item in step.get("metrics", []):
        metric_id = item["metric"]
        if metric_id not in metric_catalog:
            raise SceneError(f"指标目录中不存在：{metric_id}")
        definition = metric_catalog[metric_id]
        if "summarize" not in definition.get("operations", []):
            raise SceneError(f"指标不允许用于 summarize：{metric_id}")
        value = total.get(metric_id)
        if value is None:
            raise SceneError(f"必需汇总指标缺失：{metric_id}")
        metrics.append(
            {
                "id": metric_id,
                "label": definition["label"],
                "value": value,
                "unit": definition.get("unit"),
                "formatter": definition["formatter"],
                "prefix": interpolate(str(item.get("prefix", "")), params),
                "separator_after": interpolate(
                    str(item["separator_after"]), params
                )
                if "separator_after" in item
                else None,
            }
        )
    return {
        "id": step["id"],
        "type": "summarize",
        "metrics": metrics,
        "presentation": interpolate_tree(step.get("presentation", {}), params),
    }


def validate_metric_operation(
    metric_id: str, metric_catalog: dict[str, Any], operation: str
) -> dict[str, Any]:
    if metric_id not in metric_catalog:
        raise SceneError(f"指标目录中不存在：{metric_id}")
    definition = metric_catalog[metric_id]
    if operation not in definition.get("operations", []):
        raise SceneError(f"指标不允许用于 {operation}：{metric_id}")
    return definition


def resolve_condition_rule(
    rule: dict[str, Any], metric_catalog: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    metric_id = str(rule.get("metric", ""))
    if not metric_id:
        raise SceneError("复合条件缺少指标")
    validate_metric_operation(metric_id, metric_catalog, "classify")
    operator = str(rule.get("operator", ""))
    if operator not in SUPPORTED_OPERATORS:
        raise SceneError(f"不支持的比较运算符：{operator}")
    if operator in {"is_missing", "not_missing"}:
        threshold = None
    elif "threshold_param" in rule:
        threshold_param = str(rule["threshold_param"])
        if threshold_param not in params:
            raise SceneError(f"分类阈值参数没有值：{threshold_param}")
        threshold = params[threshold_param]
    elif "value" in rule:
        threshold = rule["value"]
    else:
        raise SceneError(f"条件缺少阈值：{metric_id}/{operator}")
    return {
        "metric": metric_id,
        "operator": operator,
        "threshold": threshold,
    }


def build_band_conditions(
    step: dict[str, Any],
    band: dict[str, Any],
    metric_catalog: dict[str, Any],
    params: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    raw_conditions = band.get("conditions")
    if raw_conditions is None:
        metric_id = str(band.get("metric", step.get("metric", "")))
        if not metric_id:
            raise SceneError(f"分类分组缺少指标：{band.get('id', '<unknown>')}")
        raw_conditions = {
            "match": "all",
            "rules": [
                {
                    "metric": metric_id,
                    "operator": band.get("operator"),
                    "threshold_param": band.get("threshold_param"),
                }
            ],
        }
    match = str(raw_conditions.get("match", "all"))
    if match not in {"all", "any"}:
        raise SceneError(f"复合条件 match 必须是 all 或 any：{match}")
    raw_rules = raw_conditions.get("rules", [])
    if not raw_rules:
        raise SceneError(f"分类分组没有条件：{band.get('id', '<unknown>')}")
    rules = [resolve_condition_rule(rule, metric_catalog, params) for rule in raw_rules]
    return match, rules


def evaluate_conditions(
    row: dict[str, Any], match: str, rules: list[dict[str, Any]]
) -> bool:
    results = [
        compare(row.get(rule["metric"]), rule["operator"], rule["threshold"])
        for rule in rules
    ]
    return all(results) if match == "all" else any(results)


def execute_classification(
    step: dict[str, Any],
    details: list[dict[str, Any]],
    metric_catalog: dict[str, Any],
    organization_field: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    display_order = step.get("display_order", "source")
    if display_order not in SUPPORTED_DISPLAY_ORDERS:
        raise SceneError(f"不支持的展示顺序：{display_order}")
    bands = []
    for band in step.get("bands", []):
        match, rules = build_band_conditions(step, band, metric_catalog, params)
        display_metric_id = str(
            band.get("display_metric")
            or band.get("metric")
            or step.get("metric")
            or rules[0]["metric"]
        )
        display_definition = validate_metric_operation(
            display_metric_id, metric_catalog, "classify"
        )
        selected = [
            row for row in details if evaluate_conditions(row, match, rules)
        ]
        if display_order != "source":
            selected = [
                row for row in selected if row.get(display_metric_id) is not None
            ]
        selected = order_rows(selected, display_metric_id, display_order)
        presentation = interpolate_tree(band.get("presentation", {}), params)
        style = presentation.get("style")
        if style not in SUPPORTED_PRESENTATION_STYLES:
            raise SceneError(f"不支持的分组展示样式：{style!r}")
        display_threshold = None
        if "display_threshold_param" in band:
            threshold_param = str(band["display_threshold_param"])
            if threshold_param not in params:
                raise SceneError(f"分类阈值参数没有值：{threshold_param}")
            display_threshold = params[threshold_param]
        elif len(rules) == 1 and rules[0]["operator"] not in {
            "is_missing",
            "not_missing",
        }:
            display_threshold = rules[0]["threshold"]
        if style in {"threshold_list", "organization_threshold"} and display_threshold is None:
            raise SceneError(
                f"展示样式需要阈值：{band.get('id', '<unknown>')}/{style}"
            )
        formatter = display_definition.get(
            "threshold_formatter", display_definition["formatter"]
        )
        organizations = [str(row[organization_field]).strip() for row in selected]
        result_band = {
            "id": band["id"],
            "metric": {
                "id": display_metric_id,
                "label": display_definition["label"],
                "unit": display_definition.get("unit"),
                "formatter": display_definition["formatter"],
            },
            "conditions": {"match": match, "rules": rules},
            "threshold": display_threshold,
            "organizations": organizations,
            "count": len(organizations),
            "formatter": formatter,
            "presentation": presentation,
        }
        if len(rules) == 1:
            result_band["operator"] = rules[0]["operator"]
        bands.append(result_band)
    step_metric_id = step.get("metric")
    step_metric = None
    if step_metric_id:
        definition = validate_metric_operation(
            str(step_metric_id), metric_catalog, "classify"
        )
        step_metric = {
            "id": str(step_metric_id),
            "label": definition["label"],
            "unit": definition.get("unit"),
            "formatter": definition["formatter"],
        }
    return {
        "id": step["id"],
        "type": "classify",
        "metric": step_metric,
        "display_order": display_order,
        "bands": bands,
        "presentation": interpolate_tree(step.get("presentation", {}), params),
    }


def build_signals(
    definitions: dict[str, Any], steps: list[dict[str, Any]]
) -> dict[str, Any]:
    step_index = {step["id"]: step for step in steps}
    signals: dict[str, Any] = {}
    for name, definition in definitions.items():
        source = definition["from"]
        step_id = source["step"]
        band_id = source["band"]
        if step_id not in step_index:
            raise SceneError(f"信号引用了不存在的步骤：{name}/{step_id}")
        step = step_index[step_id]
        if step["type"] != "classify":
            raise SceneError(f"信号只能引用分类步骤：{name}/{step_id}")
        band = next((item for item in step["bands"] if item["id"] == band_id), None)
        if band is None:
            raise SceneError(f"信号引用了不存在的分组：{name}/{step_id}/{band_id}")
        signals[name] = {
            "organizations": list(band["organizations"]),
            "count": band["count"],
            "step": step_id,
            "band": band_id,
        }
    return signals


def build_scene_context(
    config: dict[str, Any],
    catalog: dict[str, Any],
    dataset_path: Path,
    supplied_params: dict[str, Any],
    encoding: str = "utf-8-sig",
) -> dict[str, Any]:
    params = resolve_parameters(config["inputs"]["parameters"], supplied_params, "场景")
    steps_config = config.get("steps", [])
    metric_ids = referenced_metrics(steps_config)
    rows = load_rows(dataset_path, catalog, metric_ids, params, encoding)
    total, details = select_row_sets(rows, catalog)
    metric_catalog = catalog["metrics"]
    organization_field = catalog["row_sets"]["details"]["organization_field"]

    steps = []
    seen_ids: set[str] = set()
    for step in steps_config:
        step_id = step["id"]
        if step_id in seen_ids:
            raise SceneError(f"分析步骤 ID 重复：{step_id}")
        seen_ids.add(step_id)
        if step["type"] == "summarize":
            result = execute_summary(step, total, metric_catalog, params)
        elif step["type"] == "classify":
            result = execute_classification(
                step, details, metric_catalog, organization_field, params
            )
        else:
            raise SceneError(f"不支持的分析步骤类型：{step['type']!r}")
        steps.append(result)

    return {
        "scene": dict(config["scene"]),
        "params": params,
        "steps": steps,
        "signals": build_signals(config.get("signals", {}), steps),
    }


def format_number(value: Any) -> str:
    if value is None:
        raise SceneError("缺失值不能格式化为报告文本")
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def direction_phrase(value: Any, unit: str) -> str:
    number = float(value)
    if number == 0:
        return "同比持平"
    direction = "正增" if number > 0 else "负增"
    return f"同比{direction}{format_number(abs(number))}{unit}"


def gap_phrase(value: Any) -> str:
    number = float(value)
    if number == 0:
        return "达标"
    direction = "超额" if number > 0 else "缺口"
    return f"{direction}{format_number(abs(number))}pt"


def format_metric(value: Any, formatter: str) -> str:
    formatters: dict[str, Callable[[Any], str]] = {
        "amount_wan": lambda item: f"{format_number(item)}万",
        "person": lambda item: f"{format_number(item)}人",
        "pct": lambda item: f"{format_number(item)}%",
        "abs_pct": lambda item: f"{format_number(abs(float(item)))}%",
        "pt": lambda item: f"{format_number(item)}pt",
        "integer": lambda item: format_number(item),
        "yoy_pct_phrase": lambda item: direction_phrase(item, "%"),
        "yoy_pt_phrase": lambda item: direction_phrase(item, "pt"),
        "gap_pt_phrase": gap_phrase,
    }
    try:
        return formatters[formatter](value)
    except KeyError as exc:
        raise SceneError(f"不支持的指标格式化器：{formatter}") from exc


def make_environment(template_dir: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    environment.filters.update(
        {
            "format_metric": format_metric,
            "amount_wan": lambda value: format_metric(value, "amount_wan"),
            "person": lambda value: format_metric(value, "person"),
            "pct": lambda value: format_metric(value, "pct"),
            "abs_pct": lambda value: format_metric(value, "abs_pct"),
            "pt": lambda value: format_metric(value, "pt"),
            "cn_join": lambda values: "、".join(values),
            "yoy_pct_phrase": lambda value: format_metric(value, "yoy_pct_phrase"),
            "yoy_pt_phrase": lambda value: format_metric(value, "yoy_pt_phrase"),
            "gap_pt_phrase": gap_phrase,
        }
    )
    return environment


def render_scene_file(
    scene_path: Path,
    datasets: dict[str, Path],
    params: dict[str, Any],
    encoding: str = "utf-8-sig",
) -> tuple[str, dict[str, Any]]:
    scene_path = scene_path.resolve()
    config = load_yaml(scene_path)
    catalog_path = (scene_path.parent / config["metric_catalog"]).resolve()
    catalog = load_yaml(catalog_path)
    dataset_id = catalog["catalog"]["dataset"]
    if dataset_id not in datasets:
        raise SceneError(f"缺少逻辑数据集绑定：{dataset_id}")
    context = build_scene_context(
        config, catalog, datasets[dataset_id].resolve(), params, encoding
    )
    rendered = make_environment(scene_path.parent).get_template(
        config["render"]["template"]
    ).render(**context)
    return rendered, context


def render_report_file(
    report_path: Path,
    datasets: dict[str, Path],
    params: dict[str, Any],
    encoding: str = "utf-8-sig",
) -> tuple[str, dict[str, Any]]:
    report_path = report_path.resolve()
    config = load_yaml(report_path)
    report_params = resolve_parameters(config["inputs"]["parameters"], params, "报告")
    report = dict(config["report"])
    report["title"] = interpolate(report["title"], report_params)

    scene_contexts: dict[str, Any] = {}
    fragments: dict[str, str] = {}
    scene_order: list[str] = []
    for item in config["scenes"]:
        scene_id = item["id"]
        if scene_id in scene_contexts:
            raise SceneError(f"报告包含重复场景：{scene_id}")
        scene_path = (report_path.parent / item["config"]).resolve()
        fragment, scene_context = render_scene_file(scene_path, datasets, params, encoding)
        if scene_context["scene"]["id"] != scene_id:
            raise SceneError(
                f"报告场景 ID 与配置不一致：{scene_id} != {scene_context['scene']['id']}"
            )
        scene_contexts[scene_id] = scene_context
        fragments[scene_id] = fragment.rstrip()
        scene_order.append(scene_id)

    context = {
        "report": report,
        "params": report_params,
        "scenes": scene_contexts,
        "fragments": fragments,
        "scene_order": scene_order,
    }
    rendered = make_environment(report_path.parent).get_template(
        config["render"]["template"]
    ).render(**context)
    return rendered, context


def parse_bindings(values: list[str], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key.strip() or not item.strip():
            raise SceneError(f"{label} 参数必须使用 name=value：{value!r}")
        key = key.strip()
        if key in result:
            raise SceneError(f"{label} 参数重复：{key}")
        result[key] = item.strip()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行手工 YAML 分析步骤并渲染 Markdown")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--scene", type=Path, help="单场景 YAML 路径")
    target.add_argument("--report", type=Path, help="组合报告 YAML 路径")
    parser.add_argument(
        "--dataset", action="append", default=[], metavar="ID=PATH", help="逻辑数据集绑定"
    )
    parser.add_argument(
        "--param", action="append", default=[], metavar="NAME=VALUE", help="运行参数"
    )
    parser.add_argument("--source-encoding", default="utf-8-sig", help="CSV 编码")
    parser.add_argument("--output", type=Path, required=True, help="Markdown 输出路径")
    parser.add_argument("--context-output", type=Path, help="可选的审计 JSON 输出路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        raw_datasets = parse_bindings(args.dataset, "dataset")
        params = parse_bindings(args.param, "param")
        datasets = {name: Path(path) for name, path in raw_datasets.items()}
        if args.scene:
            rendered, context = render_scene_file(
                args.scene, datasets, params, args.source_encoding
            )
        else:
            rendered, context = render_report_file(
                args.report, datasets, params, args.source_encoding
            )
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(f"报告已生成：{output}")
        if args.context_output:
            context_output = args.context_output.resolve()
            context_output.parent.mkdir(parents=True, exist_ok=True)
            context_output.write_text(
                json.dumps(context, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"上下文已生成：{context_output}")
    except (SceneError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise SystemExit(f"错误：{exc}") from exc


if __name__ == "__main__":
    main()
