"""Controlled query execution and source-specific data adapters."""

from __future__ import annotations

import csv
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from .config import SourceProfileRegistry, interpolate, parse_number
from .errors import QueryError
from .models import (
    CanonicalDataset,
    CanonicalRow,
    DataBinding,
    QueryExecutionRecord,
    QueryManifest,
    QueryRequest,
    QueryResult,
    Scalar,
)


class DataAdapter(Protocol):
    """Convert one bound source into a canonical dataset."""

    name: str

    def fingerprint(self, binding: DataBinding) -> str: ...

    def load(
        self,
        manifest: QueryManifest,
        binding: DataBinding,
        parameters: dict[str, Scalar],
    ) -> CanonicalDataset: ...


def _source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_field(value: Any, field_type: str, label: str) -> Scalar:
    if field_type == "number":
        return parse_number(value, label)
    if field_type == "string":
        return "" if value is None else str(value).strip()
    raise QueryError(f"不支持的字段类型：{label}/{field_type}")


class CsvDataAdapter:
    name = "csv"

    def __init__(self, profiles: SourceProfileRegistry):
        self.profiles = profiles

    def fingerprint(self, binding: DataBinding) -> str:
        path = Path(str(binding.source)).resolve()
        if not path.is_file():
            raise QueryError(f"找不到 CSV 数据源：{path}")
        return _source_digest(path)

    def load(
        self,
        manifest: QueryManifest,
        binding: DataBinding,
        parameters: dict[str, Scalar],
    ) -> CanonicalDataset:
        path = Path(str(binding.source)).resolve()
        if not path.is_file():
            raise QueryError(f"找不到 CSV 数据源：{path}")
        profile_id = binding.profile_id or manifest.profile_id
        if profile_id != manifest.profile_id:
            raise QueryError(
                f"查询 {manifest.id} 不允许使用 profile {profile_id}"
            )
        profile = self.profiles.get(profile_id)
        encoding = str(profile.get("profile", {}).get("encoding", "utf-8-sig"))
        dimensions = profile.get("dimensions", {})
        metrics = profile.get("metrics", {})
        definitions = {**dimensions, **metrics}
        resolved_columns = {
            field_id: interpolate(str(definition["column"]), parameters)
            for field_id, definition in definitions.items()
        }
        if len(resolved_columns.values()) != len(set(resolved_columns.values())):
            raise QueryError(f"profile {profile_id} 多个字段映射到同一列")

        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding=encoding, newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames:
                    raise QueryError(f"CSV 没有表头：{path}")
                headers = [str(header).strip() for header in reader.fieldnames]
                if len(headers) != len(set(headers)):
                    raise QueryError(f"CSV 清理后出现重复表头：{path}")
                missing = [
                    column
                    for column in resolved_columns.values()
                    if column not in headers
                ]
                if missing:
                    raise QueryError(f"CSV 缺少必需列：{', '.join(missing)}")
                for source_index, raw in enumerate(reader, start=2):
                    normalized = {
                        str(header).strip(): value.strip()
                        if isinstance(value, str)
                        else value
                        for header, value in raw.items()
                    }
                    canonical: dict[str, Any] = {"source_index": source_index}
                    for field_id, definition in definitions.items():
                        column = resolved_columns[field_id]
                        canonical[field_id] = _parse_field(
                            normalized.get(column),
                            str(definition["type"]),
                            f"第{source_index}行/{column}",
                        )
                    rows.append(canonical)
        except UnicodeError as exc:
            raise QueryError(f"CSV 编码错误：{path}/{encoding}") from exc

        row_sets = profile.get("row_sets", {})
        total_definition = row_sets.get("total", {})
        total_field = str(total_definition.get("field", ""))
        total_value = total_definition.get("value")
        if total_definition.get("operator") != "eq" or not total_field:
            raise QueryError(f"profile {profile_id} 的 total 选择器无效")
        total_matches = [row for row in rows if row.get(total_field) == total_value]
        if len(total_matches) != 1:
            raise QueryError(
                f"汇总行必须恰好1行，实际为{len(total_matches)}行：{profile_id}"
            )
        total = total_matches[0]
        organization_field = str(
            row_sets.get("details", {}).get("organization_field", "")
        )
        if organization_field not in dimensions:
            raise QueryError(f"profile {profile_id} 的机构字段无效")

        canonical_rows = [
            CanonicalRow(
                source_index=int(total["source_index"]),
                role="total",
                organization=None,
                values={metric_id: total.get(metric_id) for metric_id in metrics},
            )
        ]
        for row in rows:
            if row["source_index"] == total["source_index"]:
                continue
            organization = str(row.get(organization_field, "")).strip()
            if not organization:
                continue
            canonical_rows.append(
                CanonicalRow(
                    source_index=int(row["source_index"]),
                    role="detail",
                    organization=organization,
                    values={metric_id: row.get(metric_id) for metric_id in metrics},
                )
            )
        if not canonical_rows[1:]:
            raise QueryError(f"查询没有机构明细数据：{manifest.id}")
        return CanonicalDataset(
            query_id=manifest.id,
            query_version=manifest.version,
            profile_id=profile_id,
            source=str(path),
            source_hash=_source_digest(path),
            rows=canonical_rows,
        )


class MemoryDataAdapter:
    name = "memory"

    def fingerprint(self, binding: DataBinding) -> str:
        payload = json.dumps(
            binding.records, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def load(
        self,
        manifest: QueryManifest,
        binding: DataBinding,
        parameters: dict[str, Scalar],
    ) -> CanonicalDataset:
        del parameters
        try:
            rows = [
                CanonicalRow.model_validate(record)
                for record in (binding.records or [])
            ]
        except (TypeError, ValueError) as exc:
            raise QueryError(f"内存数据不符合 CanonicalRow：{manifest.id}") from exc
        totals = [row for row in rows if row.role == "total"]
        details = [row for row in rows if row.role == "detail"]
        if len(totals) != 1 or not details:
            raise QueryError("内存数据必须包含1个 total 和至少1个 detail")
        return CanonicalDataset(
            query_id=manifest.id,
            query_version=manifest.version,
            profile_id=binding.profile_id or manifest.profile_id,
            source="memory",
            source_hash=self.fingerprint(binding),
            rows=rows,
        )


class QueryExecutor:
    """Validate query parameters, execute controlled handlers, and cache per run."""

    def __init__(self, profiles: SourceProfileRegistry):
        self.adapters: dict[str, DataAdapter] = {
            "csv": CsvDataAdapter(profiles),
            "memory": MemoryDataAdapter(),
        }
        self._cache: dict[str, CanonicalDataset] = {}
        self.execution_records: list[QueryExecutionRecord] = []

    def clear_cache(self) -> None:
        self._cache.clear()
        self.execution_records.clear()

    def execute(
        self,
        manifest: QueryManifest,
        binding: DataBinding,
        supplied_parameters: dict[str, Scalar],
    ) -> QueryResult:
        started = perf_counter()
        parameters: dict[str, Scalar] = {}
        source_hash: str | None = None
        cache_hit = False
        expected_handler_refs = {
            "csv": "template_analysis_agent.query.CsvDataAdapter",
            "memory": "template_analysis_agent.query.MemoryDataAdapter",
        }
        try:
            for name, definition in manifest.parameters.items():
                value = supplied_parameters.get(name)
                if definition.required and value in {None, ""}:
                    raise QueryError(f"查询 {manifest.id} 缺少参数：{name}")
                if value not in {None, ""}:
                    parameters[name] = value
            if manifest.handler_ref != expected_handler_refs[manifest.handler]:
                raise QueryError(
                    f"查询 {manifest.id} 的 handler_ref 未注册："
                    f"{manifest.handler_ref}"
                )
            if binding.adapter not in self.adapters:
                raise QueryError(f"未知数据适配器：{binding.adapter}")
            if manifest.handler != binding.adapter and binding.adapter != "memory":
                raise QueryError(
                    f"查询 {manifest.id} 要求 {manifest.handler}，"
                    f"实际为 {binding.adapter}"
                )
            adapter = self.adapters[binding.adapter]
            source_hash = adapter.fingerprint(binding)
            cache_payload = {
                "query": manifest.id,
                "version": manifest.version,
                "parameters": parameters,
                "adapter": binding.adapter,
                "profile": binding.profile_id or manifest.profile_id,
                "source_hash": source_hash,
            }
            cache_key = hashlib.sha256(
                json.dumps(
                    cache_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            request = QueryRequest(
                query_id=manifest.id,
                query_version=manifest.version,
                parameters=parameters,
                binding=binding,
            )
            if cache_key in self._cache:
                dataset = self._cache[cache_key]
                cache_hit = True
            else:
                pool = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="query-adapter",
                )
                future = pool.submit(adapter.load, manifest, binding, parameters)
                try:
                    dataset = future.result(timeout=manifest.timeout_ms / 1000)
                except FutureTimeoutError as exc:
                    future.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise QueryError(
                        f"查询 {manifest.id} 超时（{manifest.timeout_ms}ms）"
                    ) from exc
                except Exception:
                    pool.shutdown(wait=True, cancel_futures=True)
                    raise
                else:
                    pool.shutdown(wait=True)
                self._cache[cache_key] = dataset
            result = QueryResult(
                request=request,
                dataset=dataset,
                cache_hit=cache_hit,
            )
        except Exception as exc:
            self.execution_records.append(
                QueryExecutionRecord(
                    query_id=manifest.id,
                    query_version=manifest.version,
                    binding_id=manifest.binding_id,
                    adapter=binding.adapter,
                    profile_id=binding.profile_id or manifest.profile_id,
                    parameters=parameters,
                    source=str(binding.source or "memory"),
                    source_hash=source_hash,
                    cache_hit=cache_hit,
                    status="timeout"
                    if isinstance(exc, QueryError) and "超时" in str(exc)
                    else "failed",
                    duration_ms=(perf_counter() - started) * 1000,
                    error=str(exc),
                )
            )
            raise
        self.execution_records.append(
            QueryExecutionRecord(
                query_id=manifest.id,
                query_version=manifest.version,
                binding_id=manifest.binding_id,
                adapter=binding.adapter,
                profile_id=binding.profile_id or manifest.profile_id,
                parameters=parameters,
                source=str(binding.source or "memory"),
                source_hash=source_hash,
                cache_hit=cache_hit,
                status="success",
                duration_ms=(perf_counter() - started) * 1000,
            )
        )
        return result
