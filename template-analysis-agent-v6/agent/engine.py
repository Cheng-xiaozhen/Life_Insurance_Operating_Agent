from __future__ import annotations

import asyncio
from pathlib import Path
from time import perf_counter
from typing import Any, AsyncIterator, Iterator

from .analyzer import ReportAssembler, SceneAnalyzer, build_scene_context
from .catalog import ReportCatalog
from .models import (
    AgentEvent,
    AgentRequest,
    AgentResult,
    RouteDecision,
    SessionState,
)
from .router import IntentRouter, ParameterBinder
from .store import InMemorySessionStore, RunStore
from .tools import ReportExecutionTool


class TemplateAnalysisAgent:
    """Explicit orchestration state machine for template-driven reports."""

    def __init__(
        self,
        *,
        catalog: ReportCatalog,
        router: IntentRouter,
        parameter_binder: ParameterBinder,
        report_tool: ReportExecutionTool,
        scene_analyzer: SceneAnalyzer,
        report_assembler: ReportAssembler,
        run_store: RunStore,
        session_store: InMemorySessionStore | None = None,
        model_name: str = "unknown",
    ):
        self.catalog = catalog
        self.router = router
        self.parameter_binder = parameter_binder
        self.report_tool = report_tool
        self.scene_analyzer = scene_analyzer
        self.report_assembler = report_assembler
        self.run_store = run_store
        self.session_store = session_store or InMemorySessionStore()
        self.model_name = model_name

    @staticmethod
    def _coerce_request(
        request: AgentRequest | str,
        *,
        session_id: str | None = None,
        data_context_id: str | None = None,
    ) -> AgentRequest:
        if isinstance(request, AgentRequest):
            return request
        return AgentRequest(
            message=str(request),
            session_id=session_id,
            data_context_id=data_context_id,
        )

    def _event(self, run_dir: Path, event_type: str, run_id: str, **data: Any) -> AgentEvent:
        event = AgentEvent(type=event_type, run_id=run_id, data=data)
        self.run_store.append_event(run_dir, event)
        return event

    @staticmethod
    def _failure_message(exc: Exception) -> str:
        detail = str(exc)
        if "is missing columns" in detail:
            return f"当前数据源不包含所请求月份对应的字段：{detail}"
        if "CSV file not found" in detail:
            return f"当前数据源缺少模板所需的 CSV 文件：{detail}"
        return f"报告执行失败：{detail}"

    def _route_with_retry(
        self,
        request: AgentRequest,
        session: SessionState,
    ) -> RouteDecision:
        errors: list[str] = []
        pending_report_id = session.report_id if session.pending_parameters else None
        for _ in range(2):
            try:
                return self.router.route(
                    request.message,
                    pending_report_id=pending_report_id,
                    pending_parameters=session.pending_parameters,
                )
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError(f"模板路由失败：{errors[-1]}")

    def events(
        self,
        request: AgentRequest | str,
        *,
        session_id: str | None = None,
        data_context_id: str | None = None,
    ) -> Iterator[AgentEvent]:
        request = self._coerce_request(
            request,
            session_id=session_id,
            data_context_id=data_context_id,
        )
        run_id, run_dir = self.run_store.create_run()
        self.run_store.write_json(
            run_dir,
            "request.json",
            {
                "message": request.message,
                "session_id": request.session_id,
                "data_context_id": request.data_context_id,
            },
        )
        yield self._event(run_dir, "request_received", run_id)
        session = self.session_store.get(request.session_id)
        current_report_id: str | None = None
        current_parameters: dict[str, Any] = {}

        try:
            yield self._event(run_dir, "route_started", run_id)
            started = perf_counter()
            route = self._route_with_retry(request, session)
            route_record: dict[str, Any] = route.to_dict()
            route_record["duration_ms"] = round((perf_counter() - started) * 1000, 2)
            route_record["model"] = self.model_name
            self.run_store.write_json(run_dir, "route.json", route_record)

            if route.action == "unsupported":
                message = route.clarification or (
                    "当前没有匹配的完整报告模板。Phase 1 不会临时裁剪或拼接场景。"
                )
                result = AgentResult(
                    status="unsupported",
                    message=message,
                    run_id=run_id,
                    report_id=route.report_id,
                )
                yield self._event(run_dir, "unsupported", run_id, reason=route.reason)
                yield self._event(run_dir, "done", run_id, result=result.to_dict())
                return

            if route.action == "clarify":
                if route.report_id:
                    session.report_id = route.report_id
                    session.parameters.update(route.extracted_params)
                    session.pending_parameters = route.missing_params or ["route_confirmation"]
                    self.session_store.save(request.session_id, session)
                message = route.clarification or "请补充报告类型或必要参数。"
                result = AgentResult(
                    status="needs_input",
                    message=message,
                    run_id=run_id,
                    report_id=route.report_id,
                    parameters=route.extracted_params,
                )
                yield self._event(
                    run_dir,
                    "clarification_required",
                    run_id,
                    missing_params=route.missing_params,
                    message=message,
                )
                yield self._event(run_dir, "done", run_id, result=result.to_dict())
                return

            report = self.catalog.get(str(route.report_id))
            current_report_id = report.report_id
            yield self._event(
                run_dir,
                "route_selected",
                run_id,
                report_id=report.report_id,
                confidence=route.confidence,
            )
            bound = self.parameter_binder.bind(route, report, session)
            if bound.clarification or bound.missing:
                session.report_id = report.report_id
                session.parameters.update(bound.values)
                session.pending_parameters = bound.missing or ["month_conflict"]
                if request.data_context_id:
                    session.data_context_id = request.data_context_id
                self.session_store.save(request.session_id, session)
                message = bound.clarification or "请补充报告所需参数。"
                result = AgentResult(
                    status="needs_input",
                    message=message,
                    run_id=run_id,
                    report_id=report.report_id,
                    parameters=bound.values,
                )
                yield self._event(
                    run_dir,
                    "clarification_required",
                    run_id,
                    missing_params=bound.missing,
                    message=message,
                )
                yield self._event(run_dir, "done", run_id, result=result.to_dict())
                return

            route_record["bound_parameters"] = bound.values
            current_parameters = dict(bound.values)
            self.run_store.write_json(run_dir, "route.json", route_record)
            yield self._event(
                run_dir,
                "parameters_bound",
                run_id,
                parameters=bound.values,
            )

            active_data_context = request.data_context_id or session.data_context_id
            if not active_data_context:
                session.report_id = report.report_id
                session.parameters = dict(bound.values)
                session.pending_parameters = ["data_context_id"]
                self.session_store.save(request.session_id, session)
                message = "请先选择或上传本次报告使用的数据集。"
                result = AgentResult(
                    status="needs_input",
                    message=message,
                    run_id=run_id,
                    report_id=report.report_id,
                    parameters=bound.values,
                )
                yield self._event(
                    run_dir,
                    "clarification_required",
                    run_id,
                    missing_params=["data_context_id"],
                    message=message,
                )
                yield self._event(run_dir, "done", run_id, result=result.to_dict())
                return

            yield self._event(
                run_dir,
                "report_execution_started",
                run_id,
                report_id=report.report_id,
                data_context_id=active_data_context,
            )
            started = perf_counter()
            deterministic_result = self.report_tool.execute(
                report.report_id,
                bound.values,
                active_data_context,
            )
            facts_path = self.run_store.write_json(
                run_dir, "facts.json", deterministic_result
            )
            facts_duration_ms = round((perf_counter() - started) * 1000, 2)
            for scene in deterministic_result["scenes"]:
                yield self._event(
                    run_dir,
                    "scene_facts_ready",
                    run_id,
                    scene_id=scene["scene_id"],
                    duration_ms=facts_duration_ms,
                )

            narratives: dict[str, Any] = {}
            narrative_record: dict[str, Any] = {
                "model": self.model_name,
                "scenes": [],
            }
            warnings: list[str] = []
            for scene_result in deterministic_result["scenes"]:
                scene_id = str(scene_result["scene_id"])
                scene_definition = self.catalog.load_scene(report.report_id, scene_id)
                context = build_scene_context(
                    scene_result,
                    scene_definition,
                    deterministic_result["parameters"],
                )
                yield self._event(
                    run_dir, "scene_analysis_started", run_id, scene_id=scene_id
                )
                started = perf_counter()
                outcome = self.scene_analyzer.analyze(context)
                narratives[scene_id] = outcome.result
                if outcome.warning:
                    warnings.append(outcome.warning)
                narrative_record["scenes"].append(
                    {
                        **outcome.result.to_dict(),
                        "used_fallback": outcome.used_fallback,
                        "duration_ms": round((perf_counter() - started) * 1000, 2),
                    }
                )
                event_type = (
                    "scene_analysis_fallback"
                    if outcome.used_fallback
                    else "scene_analysis_ready"
                )
                yield self._event(
                    run_dir,
                    event_type,
                    run_id,
                    scene_id=scene_id,
                    warning=outcome.warning,
                )

            self.run_store.write_json(run_dir, "narratives.json", narrative_record)
            markdown = self.report_assembler.render(deterministic_result, narratives)
            report_path = self.run_store.write_text(run_dir, "report.md", markdown)
            status = "completed_with_warnings" if warnings else "completed"
            result = AgentResult(
                status=status,
                message="报告已生成。" if not warnings else "报告已生成，部分场景使用了确定性文案。",
                run_id=run_id,
                report_id=report.report_id,
                parameters=bound.values,
                facts_path=str(facts_path),
                report_path=str(report_path),
                warnings=warnings,
            )
            session.report_id = report.report_id
            session.parameters = dict(bound.values)
            session.pending_parameters = []
            session.data_context_id = active_data_context
            self.session_store.save(request.session_id, session)
            yield self._event(
                run_dir,
                "report_ready",
                run_id,
                report_path=str(report_path),
                status=status,
            )
            yield self._event(run_dir, "done", run_id, result=result.to_dict())
        except Exception as exc:
            result = AgentResult(
                status="failed",
                message=self._failure_message(exc),
                run_id=run_id,
                report_id=current_report_id,
                parameters=current_parameters,
            )
            yield self._event(run_dir, "failed", run_id, error=str(exc))
            yield self._event(run_dir, "done", run_id, result=result.to_dict())

    def chat(
        self,
        request: AgentRequest | str,
        *,
        session_id: str | None = None,
        data_context_id: str | None = None,
    ) -> AgentResult:
        result: AgentResult | None = None
        for event in self.events(
            request,
            session_id=session_id,
            data_context_id=data_context_id,
        ):
            if event.type == "done":
                result = AgentResult.from_mapping(event.data["result"])
        if result is None:
            raise RuntimeError("Agent state machine ended without a result")
        return result

    async def chat_stream(
        self,
        request: AgentRequest | str,
        *,
        session_id: str | None = None,
        data_context_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        for event in self.events(
            request,
            session_id=session_id,
            data_context_id=data_context_id,
        ):
            yield event
            await asyncio.sleep(0)
