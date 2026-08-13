from __future__ import annotations

import csv
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml


SUPPORTED_OPERATIONS = {"summary", "select"}
SUPPORTED_OPERATORS = {">", ">=", "<", "<=", "=", "is_empty"}
PARAMETER_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
PARAMETER_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class TemplateExecutionError(ValueError):
    """Raised when a declarative template cannot be executed safely."""


def _load_yaml(path: Path, resource_name: str) -> dict[str, Any]:
    if not path.is_file():
        raise TemplateExecutionError(f"{resource_name} not found: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TemplateExecutionError(f"Invalid YAML in {resource_name} {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise TemplateExecutionError(f"{resource_name} must be a YAML mapping: {path}")
    return document


def _required(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "":
        raise TemplateExecutionError(f"Missing required field '{key}' in {context}")
    return value


def _format_text(value: Any, parameters: Mapping[str, Any], context: str) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return value.format_map(parameters)
    except KeyError as exc:
        raise TemplateExecutionError(
            f"Unknown parameter '{exc.args[0]}' while resolving {context}"
        ) from exc


def _resolve_parameter(value: Any, parameters: Mapping[str, Any], context: str) -> Any:
    if not isinstance(value, str):
        return value
    match = PARAMETER_REFERENCE.fullmatch(value)
    if not match:
        return value
    name = match.group(1)
    if name not in parameters:
        raise TemplateExecutionError(f"Unknown scene parameter '{name}' in {context}")
    return parameters[name]


def _format_scene_text(value: Any, parameters: Mapping[str, Any], context: str) -> Any:
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise TemplateExecutionError(f"Unknown scene parameter '{name}' in {context}")
        return str(parameters[name])

    return PARAMETER_PLACEHOLDER.sub(replace, value)


def _parse_cell(raw_value: Any) -> dict[str, Any]:
    display = "" if raw_value is None else str(raw_value).strip()
    if display in {"", "-"}:
        return {"value": None, "display": display}

    numeric_text = display.replace(",", "")
    if numeric_text.endswith("%"):
        numeric_text = numeric_text[:-1].strip()
    elif numeric_text.lower().endswith("pt"):
        numeric_text = numeric_text[:-2].strip()

    try:
        number = float(numeric_text)
    except ValueError:
        return {"value": display, "display": display}

    value: int | float = int(number) if number.is_integer() else number
    return {"value": value, "display": display}


def _read_csv(path: Path, encoding: str) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise TemplateExecutionError(f"CSV file not found: {path}")
    try:
        with path.open("r", encoding=encoding, newline="") as stream:
            reader = csv.reader(stream)
            raw_headers = next(reader, None)
            if raw_headers is None:
                raise TemplateExecutionError(f"CSV file is empty: {path}")
            headers = [str(header).strip() for header in raw_headers]
            if len(headers) != len(set(headers)):
                raise TemplateExecutionError(f"CSV has duplicate headers after trimming: {path}")

            rows: list[dict[str, str]] = []
            for row_number, values in enumerate(reader, start=2):
                if not any(str(value).strip() for value in values):
                    continue
                if len(values) != len(headers):
                    raise TemplateExecutionError(
                        f"CSV row {row_number} has {len(values)} columns; expected {len(headers)}: {path}"
                    )
                rows.append(
                    {header: str(value).strip() for header, value in zip(headers, values)}
                )
    except UnicodeError as exc:
        raise TemplateExecutionError(f"Cannot decode CSV with {encoding}: {path}") from exc
    return headers, rows


def _coerce_threshold(value: Any, context: str) -> int | float | str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    parsed = _parse_cell(value)["value"]
    if parsed is None:
        raise TemplateExecutionError(f"Empty threshold in {context}")
    return parsed


def _matches(value: Any, operator: str, threshold: Any, context: str) -> bool:
    if operator == "is_empty":
        return value is None
    if value is None:
        return False

    threshold = _coerce_threshold(threshold, context)
    try:
        if operator == ">":
            return value > threshold
        if operator == ">=":
            return value >= threshold
        if operator == "<":
            return value < threshold
        if operator == "<=":
            return value <= threshold
        return value == threshold
    except TypeError as exc:
        raise TemplateExecutionError(
            f"Incompatible comparison values in {context}: {value!r} {operator} {threshold!r}"
        ) from exc


def _prepare_scene_rows(
    *,
    data_dir: Path,
    binding: Mapping[str, Any],
    scene_id: str,
    report_parameters: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    binding_scenes = _required(binding, "scenes", "binding")
    if not isinstance(binding_scenes, dict) or scene_id not in binding_scenes:
        raise TemplateExecutionError(
            f"Binding '{binding.get('id', '<unknown>')}' has no mapping for scene '{scene_id}'"
        )
    scene_binding = binding_scenes[scene_id]
    if not isinstance(scene_binding, dict):
        raise TemplateExecutionError(f"Binding for scene '{scene_id}' must be a mapping")

    file_name = _format_text(
        _required(scene_binding, "file", f"binding scene '{scene_id}'"),
        report_parameters,
        f"CSV file for scene '{scene_id}'",
    )
    source_path = data_dir / str(file_name)
    headers, raw_rows = _read_csv(source_path, str(binding.get("encoding", "utf-8-sig")))

    organization_column = str(
        _required(scene_binding, "organization_column", f"binding scene '{scene_id}'")
    ).strip()
    total_row = _required(scene_binding, "total_row", f"binding scene '{scene_id}'")
    if not isinstance(total_row, dict):
        raise TemplateExecutionError(f"total_row for scene '{scene_id}' must be a mapping")
    total_column = str(_required(total_row, "column", f"total_row for scene '{scene_id}'")).strip()
    total_value = str(_required(total_row, "value", f"total_row for scene '{scene_id}'")).strip()

    field_mapping = _required(scene_binding, "fields", f"binding scene '{scene_id}'")
    if not isinstance(field_mapping, dict) or not field_mapping:
        raise TemplateExecutionError(f"fields for scene '{scene_id}' must be a non-empty mapping")
    resolved_fields = {
        semantic_name: str(
            _format_text(
                physical_name,
                report_parameters,
                f"column mapping '{semantic_name}' for scene '{scene_id}'",
            )
        ).strip()
        for semantic_name, physical_name in field_mapping.items()
    }

    required_columns = {organization_column, total_column, *resolved_fields.values()}
    missing_columns = sorted(required_columns - set(headers))
    if missing_columns:
        raise TemplateExecutionError(
            f"CSV for scene '{scene_id}' is missing columns: {', '.join(missing_columns)}"
        )

    prepared_rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        prepared_rows.append(
            {
                "organization": raw_row[organization_column],
                "is_total": raw_row[total_column] == total_value,
                "values": {
                    semantic_name: _parse_cell(raw_row[physical_name])
                    for semantic_name, physical_name in resolved_fields.items()
                },
            }
        )
    return prepared_rows, str(source_path.resolve())


def _assert_known_metric(metric: str, rows: list[dict[str, Any]], context: str) -> None:
    if not rows or metric not in rows[0]["values"]:
        raise TemplateExecutionError(f"Unknown semantic field '{metric}' in {context}")


def _execute_summary(
    action: Mapping[str, Any], rows: list[dict[str, Any]], context: str
) -> dict[str, Any]:
    total_rows = [row for row in rows if row["is_total"]]
    if len(total_rows) != 1:
        raise TemplateExecutionError(
            f"Expected exactly one total row in {context}; found {len(total_rows)}"
        )
    fields = _required(action, "fields", context)
    if not isinstance(fields, list) or not fields:
        raise TemplateExecutionError(f"summary fields must be a non-empty list in {context}")
    result: dict[str, Any] = {}
    for field in fields:
        field_name = str(field)
        _assert_known_metric(field_name, rows, context)
        result[field_name] = deepcopy(total_rows[0]["values"][field_name])
    return result


def _execute_select(
    action: Mapping[str, Any],
    rows: list[dict[str, Any]],
    parameters: Mapping[str, Any],
    context: str,
) -> list[dict[str, Any]]:
    where = _required(action, "where", context)
    if not isinstance(where, dict):
        raise TemplateExecutionError(f"where must be a mapping in {context}")
    field = str(_required(where, "field", f"where in {context}"))
    operator = str(_required(where, "operator", f"where in {context}"))
    _assert_known_metric(field, rows, context)
    if operator not in SUPPORTED_OPERATORS:
        raise TemplateExecutionError(f"Unsupported operator '{operator}' in {context}")
    threshold = _resolve_parameter(where.get("value"), parameters, f"where in {context}")

    selected = [
        row
        for row in rows
        if not row["is_total"]
        and row["organization"]
        and _matches(row["values"][field]["value"], operator, threshold, context)
    ]

    order_by = action.get("order_by")
    if order_by is not None:
        if not isinstance(order_by, dict):
            raise TemplateExecutionError(f"order_by must be a mapping in {context}")
        order_field = str(_required(order_by, "field", f"order_by in {context}"))
        direction = str(order_by.get("direction", "asc")).lower()
        if direction not in {"asc", "desc"}:
            raise TemplateExecutionError(f"Unsupported sort direction '{direction}' in {context}")
        _assert_known_metric(order_field, rows, context)
        with_value = [row for row in selected if row["values"][order_field]["value"] is not None]
        without_value = [row for row in selected if row["values"][order_field]["value"] is None]
        with_value.sort(
            key=lambda row: row["values"][order_field]["value"],
            reverse=direction == "desc",
        )
        selected = with_value + without_value

    display_field = str(action.get("display_field") or field)
    _assert_known_metric(display_field, rows, context)

    return [
        {
            "organization": row["organization"],
            "values": deepcopy(row["values"]),
        }
        for row in selected
    ]


def _render_scene(
    scene: Mapping[str, Any],
    actions: list[Mapping[str, Any]],
    facts: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> str:
    metrics = scene.get("metrics", {})
    lines: list[str] = []
    for action in actions:
        action_id = str(action["id"])
        action_type = str(action["type"])
        title = str(_format_scene_text(action.get("title", action_id), parameters, action_id))
        fact = facts[action_id]

        if action_type == "summary":
            fragments: list[str] = []
            for field in action["fields"]:
                cell = fact[str(field)]
                metric = metrics.get(str(field), {}) if isinstance(metrics, dict) else {}
                label = metric.get("label", field) if isinstance(metric, dict) else field
                unit = metric.get("unit", "") if isinstance(metric, dict) else ""
                shown = cell["display"] or "缺失"
                fragments.append(f"{label} {shown}{unit}")
            lines.append(f"- {title}：{'；'.join(fragments)}。")
            continue

        display_field = str(action.get("display_field") or action["where"]["field"])
        entries = [
            f"{row['organization']}（{row['values'][display_field]['display'] or '缺失'}）"
            for row in fact
        ]
        if entries:
            lines.append(f"- {title}（{len(entries)}家）：{'、'.join(entries)}。")
        else:
            lines.append(f"- {title}：暂无符合条件的机构。")
    return "\n".join(lines)


def execute_report(
    report_path: str | Path,
    data_dir: str | Path,
    runtime_parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report_path = Path(report_path).resolve()
    data_dir = Path(data_dir).resolve()
    report = _load_yaml(report_path, "Report")

    report_id = str(_required(report, "id", "Report"))
    binding_id = str(_required(report, "binding", f"Report '{report_id}'"))
    parameters = deepcopy(report.get("parameters") or {})
    if not isinstance(parameters, dict):
        raise TemplateExecutionError(f"parameters in Report '{report_id}' must be a mapping")
    if runtime_parameters:
        parameters.update(runtime_parameters)

    references_dir = report_path.parent.parent
    binding_path = references_dir / "bindings" / f"{binding_id}.yaml"
    binding = _load_yaml(binding_path, f"Binding '{binding_id}'")
    if str(_required(binding, "id", f"Binding '{binding_id}'")) != binding_id:
        raise TemplateExecutionError(f"Binding id does not match filename: {binding_path}")
    if binding.get("type") != "csv":
        raise TemplateExecutionError(f"Unsupported binding type: {binding.get('type')!r}")

    sections = _required(report, "sections", f"Report '{report_id}'")
    if not isinstance(sections, list) or not sections:
        raise TemplateExecutionError(f"sections in Report '{report_id}' must be a non-empty list")

    scene_results: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise TemplateExecutionError(f"Report section {section_index} must be a mapping")
        scene_id = str(_required(section, "scene", f"Report section {section_index}"))
        scene_path = references_dir / "scenes" / f"{scene_id}.yaml"
        scene = _load_yaml(scene_path, f"Scene '{scene_id}'")
        if str(_required(scene, "id", f"Scene '{scene_id}'")) != scene_id:
            raise TemplateExecutionError(f"Scene id does not match filename: {scene_path}")

        scene_parameters = deepcopy(scene.get("parameters") or {})
        overrides = section.get("parameters") or {}
        if not isinstance(scene_parameters, dict) or not isinstance(overrides, dict):
            raise TemplateExecutionError(f"Scene parameters must be mappings for '{scene_id}'")
        scene_parameters.update(overrides)

        rows, source_path = _prepare_scene_rows(
            data_dir=data_dir,
            binding=binding,
            scene_id=scene_id,
            report_parameters=parameters,
        )
        actions = _required(scene, "analysis", f"Scene '{scene_id}'")
        if not isinstance(actions, list) or not actions:
            raise TemplateExecutionError(f"analysis in Scene '{scene_id}' must be a non-empty list")

        facts: dict[str, Any] = {}
        normalized_actions: list[Mapping[str, Any]] = []
        for action_index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                raise TemplateExecutionError(
                    f"Action {action_index} in Scene '{scene_id}' must be a mapping"
                )
            action_id = str(_required(action, "id", f"Scene '{scene_id}' action {action_index}"))
            action_type = str(_required(action, "type", f"Scene '{scene_id}' action '{action_id}'"))
            context = f"Scene '{scene_id}' action '{action_id}'"
            if action_type not in SUPPORTED_OPERATIONS:
                raise TemplateExecutionError(f"Unsupported operation '{action_type}' in {context}")
            if action_id in facts:
                raise TemplateExecutionError(f"Duplicate action id '{action_id}' in Scene '{scene_id}'")
            if action_type == "summary":
                facts[action_id] = _execute_summary(action, rows, context)
            else:
                facts[action_id] = _execute_select(action, rows, scene_parameters, context)
            normalized_actions.append(action)

        narrative = _render_scene(scene, normalized_actions, facts, scene_parameters)
        scene_results.append(
            {
                "scene_id": scene_id,
                "title": str(_required(scene, "title", f"Scene '{scene_id}'")),
                "source": source_path,
                "facts": facts,
                "narrative": narrative,
                "warnings": [],
            }
        )

    return {
        "report_id": report_id,
        "title": str(
            _format_text(_required(report, "title", f"Report '{report_id}'"), parameters, "report title")
        ),
        "parameters": parameters,
        "output": deepcopy(report.get("output") or {"format": "markdown", "heading_level": 1}),
        "scenes": scene_results,
    }


def render_markdown(result: Mapping[str, Any]) -> str:
    output = result.get("output") or {}
    if output.get("format", "markdown") != "markdown":
        raise TemplateExecutionError(f"Unsupported output format: {output.get('format')!r}")
    heading_level = output.get("heading_level", 1)
    if not isinstance(heading_level, int) or not 1 <= heading_level <= 5:
        raise TemplateExecutionError("Markdown heading_level must be an integer from 1 to 5")
    report_heading = "#" * heading_level
    scene_heading = "#" * (heading_level + 1)
    lines = [f"{report_heading} {result['title']}"]
    for scene in result["scenes"]:
        lines.extend(["", f"{scene_heading} {scene['title']}", "", scene["narrative"]])
    return "\n".join(lines).rstrip() + "\n"
