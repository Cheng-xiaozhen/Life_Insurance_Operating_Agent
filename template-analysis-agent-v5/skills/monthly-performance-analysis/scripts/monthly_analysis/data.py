"""CSV source profiles and normalization for the monthly report."""

from __future__ import annotations

import csv
import hashlib
import math
import re
from pathlib import Path
from typing import Any

import yaml

from .models import (
    AnalysisDataset,
    DataError,
    OrganizationRow,
    Scalar,
    SourceRecord,
)


INTERPOLATION_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate(value: str, context: dict[str, Scalar]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = context.get(name)
        if resolved in {None, ""}:
            raise DataError(f"CSV 字段映射缺少参数：{name}")
        return str(resolved)

    return INTERPOLATION_PATTERN.sub(replace, value)


def _parse_number(value: Any, label: str) -> int | float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "-"}:
        return None
    text = text.removesuffix("%").removesuffix("pt").strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise DataError(f"{label} 不是合法数值：{value!r}") from exc
    if not math.isfinite(number):
        raise DataError(f"{label} 必须是有限数值：{value!r}")
    return int(number) if number.is_integer() else number


def load_csv_profiles(path: str | Path) -> dict[str, dict[str, Any]]:
    profile_path = Path(path).resolve()
    if not profile_path.is_file():
        raise DataError(f"CSV Profile 不存在：{profile_path}")
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise DataError(f"CSV Profile 没有 profiles：{profile_path}")
    return {str(name): dict(value) for name, value in profiles.items()}


class CsvDatasetLoader:
    """Normalize one configured CSV into the small V4 dataset contract."""

    def __init__(self, profiles: dict[str, dict[str, Any]]):
        self.profiles = profiles

    def load(
        self,
        scene_id: str,
        data_dir: str | Path,
        context: dict[str, Scalar],
    ) -> tuple[AnalysisDataset, SourceRecord]:
        try:
            profile = self.profiles[scene_id]
        except KeyError as exc:
            raise DataError(f"没有 CSV Profile：{scene_id}") from exc
        path = (Path(data_dir).resolve() / str(profile["filename"])).resolve()
        if not path.is_file():
            raise DataError(f"找不到 CSV 数据源：{path}")
        encoding = str(profile.get("encoding", "utf-8-sig"))
        row_label_column = str(profile["row_label_column"])
        organization_column = str(profile["organization_column"])
        total_value = str(profile.get("total_value", "全系统"))
        metric_columns = {
            str(metric_id): _interpolate(str(column), context)
            for metric_id, column in profile["metrics"].items()
        }
        required_columns = [
            row_label_column,
            organization_column,
            *metric_columns.values(),
        ]
        if len(required_columns) != len(set(required_columns)):
            raise DataError(f"CSV Profile 多个字段映射到同一列：{scene_id}")

        rows: list[dict[str, str]] = []
        try:
            with path.open("r", encoding=encoding, newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames:
                    raise DataError(f"CSV 没有表头：{path}")
                headers = [str(header).strip() for header in reader.fieldnames]
                if len(headers) != len(set(headers)):
                    raise DataError(f"CSV 清理后出现重复表头：{path}")
                missing = [
                    column for column in required_columns if column not in headers
                ]
                if missing:
                    raise DataError(f"CSV 缺少必需列：{', '.join(missing)}")
                for raw in reader:
                    rows.append(
                        {
                            str(header).strip(): (
                                value.strip() if isinstance(value, str) else ""
                            )
                            for header, value in raw.items()
                        }
                    )
        except UnicodeError as exc:
            raise DataError(f"CSV 编码错误：{path}/{encoding}") from exc

        total_rows = [
            row for row in rows if row.get(row_label_column) == total_value
        ]
        if len(total_rows) != 1:
            raise DataError(
                f"汇总行必须恰好1行，实际为{len(total_rows)}行：{scene_id}"
            )
        total = total_rows[0]
        summary = {
            metric_id: _parse_number(
                total.get(column),
                f"{scene_id}/全系统/{column}",
            )
            for metric_id, column in metric_columns.items()
        }
        detail_rows: list[OrganizationRow] = []
        for index, row in enumerate(rows, start=2):
            if row is total:
                continue
            organization = row.get(organization_column, "").strip()
            if not organization:
                continue
            detail_rows.append(
                OrganizationRow(
                    organization=organization,
                    metrics={
                        metric_id: _parse_number(
                            row.get(column),
                            f"{scene_id}/第{index}行/{column}",
                        )
                        for metric_id, column in metric_columns.items()
                    },
                )
            )
        if not detail_rows:
            raise DataError(f"CSV 没有机构明细：{scene_id}")
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        return (
            AnalysisDataset(summary=summary, rows=detail_rows),
            SourceRecord(
                scene_id=scene_id,
                path=str(path),
                source_hash=source_hash,
                detail_rows=len(detail_rows),
            ),
        )
