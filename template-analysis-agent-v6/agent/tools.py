from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .catalog import ReportCatalog
from .models import AgentContractError


class DataContextRegistry:
    """Resolves application-authorized data IDs to directories."""

    def __init__(
        self,
        contexts: Mapping[str, str | Path] | None = None,
        *,
        allowed_roots: list[str | Path] | None = None,
    ):
        self._allowed_roots = [Path(root).resolve() for root in allowed_roots or []]
        self._contexts: dict[str, Path] = {}
        for context_id, path in (contexts or {}).items():
            self.register(context_id, path)

    def register(self, context_id: str, path: str | Path) -> None:
        if not context_id or not context_id.strip():
            raise AgentContractError("data context id must not be empty")
        resolved = Path(path).resolve()
        if not resolved.is_dir():
            raise AgentContractError(f"data directory not found: {resolved}")
        if self._allowed_roots and not any(
            resolved == root or root in resolved.parents for root in self._allowed_roots
        ):
            raise AgentContractError(f"data directory is outside allowed roots: {resolved}")
        self._contexts[context_id] = resolved

    def resolve(self, context_id: str) -> Path:
        try:
            return self._contexts[context_id]
        except KeyError as exc:
            raise AgentContractError(f"unknown data context id: {context_id}") from exc


class ReportExecutionTool:
    """The only business execution capability available to the Agent."""

    def __init__(
        self,
        catalog: ReportCatalog,
        data_contexts: DataContextRegistry,
        execute_report: Any,
    ):
        self.catalog = catalog
        self.data_contexts = data_contexts
        self._execute_report = execute_report

    def execute(
        self,
        report_id: str,
        parameters: Mapping[str, Any],
        data_context_id: str,
    ) -> dict[str, Any]:
        report = self.catalog.get(report_id)
        unknown = sorted(set(parameters) - set(report.params_schema))
        if unknown:
            raise AgentContractError(f"unknown report parameters: {', '.join(unknown)}")
        data_dir = self.data_contexts.resolve(data_context_id)
        return self._execute_report(report.path, data_dir, parameters)
