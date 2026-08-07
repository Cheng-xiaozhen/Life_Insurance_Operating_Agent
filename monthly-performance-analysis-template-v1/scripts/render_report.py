#!/usr/bin/env python3
"""Render a monthly performance report from the CSV data contract."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Callable

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError as exc:
    raise SystemExit(
        "缺少 Jinja2。请先执行：python -m pip install -r requirements.txt"
    ) from exc


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = BASE_DIR / "templates" / "data-contract.json"
DEFAULT_OUTPUT = BASE_DIR / "output" / "五月业绩分析报告.md"


class ReportError(RuntimeError):
    """Raised when source data violates the report contract."""


def resolve_name(value: str, meta: dict[str, str]) -> str:
    return value.format(**meta)


def format_number(value: Any) -> str:
    if value is None:
        raise ReportError("缺失值不能格式化为报告文本")
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def parse_number(value: Any, missing_markers: set[str]) -> int | float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in missing_markers:
        return None
    text = text.removesuffix("%").removesuffix("pt").strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise ReportError(f"无法转换为数值：{value!r}") from exc
    return int(number) if number.is_integer() else number


def compare(value: float, operator: str, threshold: float) -> bool:
    operations: dict[str, Callable[[float, float], bool]] = {
        ">": lambda left, right: left > right,
        ">=": lambda left, right: left >= right,
        "<": lambda left, right: left < right,
        "<=": lambda left, right: left <= right,
        "==": lambda left, right: left == right,
    }
    try:
        return operations[operator](value, threshold)
    except KeyError as exc:
        raise ReportError(f"不支持的比较运算符：{operator}") from exc


class CsvStore:
    def __init__(self, data_dir: Path, encoding: str, missing_markers: list[str]):
        self.data_dir = data_dir
        self.encoding = encoding
        self.missing_markers = set(missing_markers)
        self._cache: dict[str, dict[str, Any]] = {}

    def load(self, source: str) -> dict[str, Any]:
        if source in self._cache:
            return self._cache[source]
        path = self.data_dir / source
        if not path.is_file():
            raise ReportError(f"找不到数据文件：{path}")
        with path.open("r", encoding=self.encoding, newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise ReportError(f"CSV 没有表头：{path}")
            headers = [header.strip() for header in reader.fieldnames]
            if len(headers) != len(set(headers)):
                raise ReportError(f"CSV 清洗后出现重复表头：{path}")
            rows: list[dict[str, Any]] = []
            for index, raw in enumerate(reader):
                row = {
                    header.strip(): (value.strip() if isinstance(value, str) else value)
                    for header, value in raw.items()
                }
                row["__source_index__"] = index
                rows.append(row)
        dataset = {"path": path, "headers": headers, "rows": rows}
        self._cache[source] = dataset
        return dataset

    def number(self, row: dict[str, Any], column: str) -> int | float | None:
        if column not in row:
            raise ReportError(f"列不存在：{column}")
        return parse_number(row[column], self.missing_markers)


def select_total_row(dataset: dict[str, Any], marker: str) -> dict[str, Any]:
    first_column = dataset["headers"][0]
    matches = [
        row for row in dataset["rows"] if str(row.get(first_column, "")).strip() == marker
    ]
    if len(matches) != 1:
        raise ReportError(
            f"{dataset['path']} 中第一列等于 {marker!r} 的行应恰好有1行，实际为{len(matches)}行"
        )
    return matches[0]


def build_summary(
    contract: dict[str, Any], store: CsvStore, meta: dict[str, str]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for section_name, section in contract["summary"].items():
        section_result: dict[str, Any] = {}
        for field_name, field in section["fields"].items():
            source = field.get("source", section.get("source"))
            marker = field.get("row", section.get("row"))
            if not source or not marker:
                raise ReportError(f"汇总字段缺少 source 或 row：{section_name}.{field_name}")
            dataset = store.load(source)
            total = select_total_row(dataset, marker)
            column = resolve_name(field["column"], meta)
            value = store.number(total, column)
            if value is None:
                raise ReportError(f"汇总字段缺失：{section_name}.{field_name} ({source}/{column})")
            section_result[field_name] = value
        result[section_name] = section_result
    return result


def eligible_rows(
    dataset: dict[str, Any], organization_column: str
) -> list[dict[str, Any]]:
    return [
        row
        for row in dataset["rows"]
        if str(row.get(organization_column, "")).strip()
    ]


def ranked_rows(
    rows: list[dict[str, Any]], store: CsvStore, column: str, direction: str
) -> list[tuple[dict[str, Any], float]]:
    values: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        value = store.number(row, column)
        if value is not None:
            values.append((row, float(value)))
    reverse = direction == "descending"
    if direction not in {"ascending", "descending"}:
        raise ReportError(f"不支持的排名方向：{direction}")
    return sorted(values, key=lambda item: item[1], reverse=reverse)


def infer_threshold(
    selected: list[float], unselected: list[float], config: dict[str, Any], label: str
) -> int | float:
    """Infer a business-tick threshold that filters exactly the selected values."""
    if not selected:
        raise ReportError(f"Cannot infer a threshold from an empty selection: {label}")
    step = float(config["step"])
    operator = config["operator"]
    all_values = selected + unselected
    low = math.floor(min(all_values) / step) - 2
    high = math.ceil(max(all_values) / step) + 2
    candidates: list[float] = []
    for multiplier in range(low, high + 1):
        candidate = multiplier * step
        if all(compare(value, operator, candidate) for value in selected) and all(
            not compare(value, operator, candidate) for value in unselected
        ):
            candidates.append(candidate)
    if not candidates:
        raise ReportError(
            f"No business tick filters exactly the ranked fallback set: {label}; "
            f"selected={selected}, unselected={unselected}, step={step}, operator={operator}"
        )
    chosen = min(candidates)
    return int(chosen) if chosen.is_integer() else chosen


def same_ranked_set(
    left: list[tuple[dict[str, Any], float]],
    right: list[tuple[dict[str, Any], float]],
) -> bool:
    return {row["__source_index__"] for row, _ in left} == {
        row["__source_index__"] for row, _ in right
    }

def organizations_in_source_order(
    selected: list[tuple[dict[str, Any], float]], organization_column: str
) -> list[str]:
    return [
        str(row[organization_column]).strip()
        for row, _ in sorted(selected, key=lambda item: item[0]["__source_index__"])
    ]


def evaluate_conditions(
    row: dict[str, Any], conditions: list[dict[str, Any]], store: CsvStore, meta: dict[str, str]
) -> bool:
    for condition in conditions:
        column = resolve_name(condition["column"], meta)
        value = store.number(row, column)
        if value is None or not compare(float(value), condition["operator"], float(condition["value"])):
            return False
    return True


def build_derived(
    contract: dict[str, Any], store: CsvStore, meta: dict[str, str]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for group_name, group in contract["derived"].items():
        if group_name == "selection_runtime":
            continue
        organization_column = group["organization_column"]
        group_result: dict[str, Any] = {}
        for rule_name, rule in group["rules"].items():
            selection = rule["selection"]
            strategy = selection.get("strategy", "threshold_range_then_ranked_limit")
            source = rule.get("source", group.get("source"))
            if not source:
                raise ReportError(f"Derived rule has no source: {group_name}.{rule_name}")
            dataset = store.load(source)
            rows = eligible_rows(dataset, organization_column)
            label = f"{group_name}.{rule_name}"

            if strategy == "complement":
                base_name = selection["of"]
                if base_name not in group_result:
                    raise ReportError(f"Complement references an unfinished rule: {label} -> {base_name}")
                excluded = set(group_result[base_name]["organizations"])
                group_result[rule_name] = {
                    "organizations": [
                        str(row[organization_column]).strip()
                        for row in rows
                        if str(row[organization_column]).strip() not in excluded
                    ],
                    "selection_mode": "complement",
                }
                continue

            if strategy == "compound_condition":
                selected_rows = [
                    row for row in rows if evaluate_conditions(row, rule["all"], store, meta)
                ]
                group_result[rule_name] = {
                    "organizations": [str(row[organization_column]).strip() for row in selected_rows],
                    "selection_mode": "compound_condition",
                }
                continue

            column = resolve_name(rule["column"], meta)
            ranked = ranked_rows(rows, store, column, selection["direction"])
            offset = int(selection.get("offset", 0))
            limit = int(selection["limit"])
            minimum = int(selection["min"])
            maximum = int(selection["max"])
            if not minimum <= limit <= maximum:
                raise ReportError(
                    f"Invalid selection range: {label}; require min <= limit <= max, "
                    f"got min={minimum}, limit={limit}, max={maximum}"
                )

            if strategy == "ranked_window":
                lower_config = rule["lower_threshold"]
                upper_config = rule["upper_threshold"]
                reuse_rule, reuse_field = upper_config["reuse"].split(".", 1)
                try:
                    upper = group_result[reuse_rule][reuse_field]
                except KeyError as exc:
                    raise ReportError(f"Cannot reuse upper threshold: {label}") from exc
                lower_default = float(lower_config["default"])
                default_selected = [
                    item
                    for item in ranked
                    if compare(item[1], lower_config["operator"], lower_default)
                    and compare(item[1], upper_config["operator"], float(upper))
                ]
                if minimum <= len(default_selected) <= maximum:
                    selected = default_selected
                    lower = lower_config["default"]
                    selection_mode = "default_threshold"
                else:
                    selected = ranked[offset : offset + limit]
                    if len(selected) != limit:
                        raise ReportError(
                            f"Ranked fallback has too few rows: {label}; expected={limit}, actual={len(selected)}"
                        )
                    following = ranked[offset + limit :]
                    lower = infer_threshold(
                        [value for _, value in selected],
                        [value for _, value in following],
                        lower_config,
                        f"{label}.lower_threshold",
                    )
                    filtered = [
                        item
                        for item in ranked
                        if compare(item[1], lower_config["operator"], float(lower))
                        and compare(item[1], upper_config["operator"], float(upper))
                    ]
                    if len(filtered) != limit or not same_ranked_set(filtered, selected):
                        raise ReportError(f"Inferred window thresholds do not reproduce the fallback set: {label}")
                    selection_mode = "inferred_threshold"
                output: dict[str, Any] = {
                    "organizations": organizations_in_source_order(selected, organization_column),
                    "lower_threshold": lower,
                    "upper_threshold": upper,
                    "selection_mode": selection_mode,
                }
            else:
                threshold_config = rule["threshold"]
                default_threshold = float(threshold_config["default"])
                default_selected = [
                    item
                    for item in ranked
                    if compare(item[1], threshold_config["operator"], default_threshold)
                ]
                if minimum <= len(default_selected) <= maximum:
                    selected = default_selected
                    threshold = threshold_config["default"]
                    selection_mode = "default_threshold"
                else:
                    selected = ranked[offset : offset + limit]
                    if len(selected) != limit:
                        raise ReportError(
                            f"Ranked fallback has too few rows: {label}; expected={limit}, actual={len(selected)}"
                        )
                    unselected = ranked[:offset] + ranked[offset + limit :]
                    threshold = infer_threshold(
                        [value for _, value in selected],
                        [value for _, value in unselected],
                        threshold_config,
                        label,
                    )
                    filtered = [
                        item
                        for item in ranked
                        if compare(item[1], threshold_config["operator"], float(threshold))
                    ]
                    if len(filtered) != limit or not same_ranked_set(filtered, selected):
                        raise ReportError(f"Inferred threshold does not reproduce the fallback set: {label}")
                    selection_mode = "inferred_threshold"
                output = {
                    "organizations": organizations_in_source_order(selected, organization_column),
                    "threshold": threshold,
                    "selection_mode": selection_mode,
                }
                if "additional_output" in rule and "threshold_abs" in rule["additional_output"]:
                    output["threshold_abs"] = abs(output["threshold"])

            group_result[rule_name] = output
        result[group_name] = group_result
    return result

def make_environment(template_dir: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    environment.filters.update(
        {
            "amount_wan": lambda value: f"{format_number(value)}万",
            "pct": lambda value: f"{format_number(value)}%",
            "pt": lambda value: f"{format_number(value)}pt",
            "cn_join": lambda values: "、".join(values),
            "yoy_pct_phrase": lambda value: direction_phrase(value, "%"),
            "yoy_pt_phrase": lambda value: direction_phrase(value, "pt"),
            "gap_pt_phrase": gap_phrase,
        }
    )
    return environment


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据月度业绩 CSV 生成 Markdown 分析报告")
    parser.add_argument("--data-dir", type=Path, required=True, help="九个 CSV 所在目录")
    parser.add_argument("--report-month-name", required=True, help="封面月份，如：五月")
    parser.add_argument("--data-month-name", required=True, help="动态列月份，如：5月")
    parser.add_argument("--cutoff-date", required=True, help="截止日期，如：5月31日")
    parser.add_argument("--quarter-name", required=True, help="季度列名称，如：二季度")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--context-output", type=Path, help="可选：输出完整渲染上下文 JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    contract_path = args.contract.resolve()
    with contract_path.open("r", encoding="utf-8") as stream:
        contract = json.load(stream)
    meta = {
        "report_month_name": args.report_month_name,
        "data_month_name": args.data_month_name,
        "cutoff_date": args.cutoff_date,
        "quarter_name": args.quarter_name,
    }
    normalization = contract["normalization"]
    store = CsvStore(
        args.data_dir.resolve(),
        contract.get("source_encoding", "UTF-8"),
        normalization["missing_markers"],
    )
    context = {
        "meta": meta,
        "summary": build_summary(contract, store, meta),
        "derived": build_derived(contract, store, meta),
    }
    template_dir = contract_path.parent
    environment = make_environment(template_dir)
    template = environment.get_template(contract["template"])
    rendered = template.render(**context)

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"报告已生成：{output}")

    if args.context_output:
        context_output = args.context_output.resolve()
        context_output.parent.mkdir(parents=True, exist_ok=True)
        context_output.write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"上下文已生成：{context_output}")


if __name__ == "__main__":
    main()