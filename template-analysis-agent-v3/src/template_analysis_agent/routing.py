"""Intent routing and deterministic plan compilation."""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Protocol

from .config import TemplateRegistry, interpolate, resolve_parameters
from .errors import ConfigurationError, PlanError, RoutingError
from .models import (
    AnalysisRequest,
    CompiledAnalysisPlan,
    CompiledScenePlan,
    ParameterDefinition,
    RoutingDecision,
    Scalar,
)


class SemanticRouter(Protocol):
    """Optional LLM semantic router. It may not return executable steps."""

    def choose(
        self,
        question: str,
        candidates: list[dict[str, Any]],
        supplied_parameters: dict[str, Scalar],
    ) -> RoutingDecision: ...


def _score_text(question: str, keywords: list[str], examples: list[str]) -> int:
    normalized = question.casefold()
    score = 0
    for keyword in keywords:
        if keyword.casefold() in normalized:
            score += max(2, len(keyword))
    for example in examples:
        common = [word for word in keywords if word in example and word in question]
        score += len(common)
    return score


class IntentRouter:
    """Select a registered report/scene and extract no executable logic."""

    def __init__(
        self,
        templates: TemplateRegistry,
        semantic_router: SemanticRouter | None = None,
    ):
        self.templates = templates
        self.semantic_router = semantic_router

    def route(self, request: AnalysisRequest) -> RoutingDecision:
        if request.report_id:
            decision = RoutingDecision(
                report_id=request.report_id,
                scene_ids=self.templates.report_scene_ids(request.report_id),
                parameters=request.parameters,
                confidence=1,
            )
            return self._with_missing_parameters(decision)
        if request.scene_ids:
            for scene_id in request.scene_ids:
                self.templates.scene_manifest(scene_id)
            decision = RoutingDecision(
                template_id=request.scene_ids[0]
                if len(request.scene_ids) == 1
                else None,
                scene_ids=request.scene_ids,
                parameters=request.parameters,
                confidence=1,
            )
            return self._with_missing_parameters(decision)

        scored: list[tuple[int, str, str, list[str]]] = []
        candidates: list[dict[str, Any]] = []
        for report in self.templates.reports():
            meta = report["report"]
            report_scene_ids = self.templates.report_scene_ids(str(meta["id"]))
            score = _score_text(
                request.question,
                list(meta.get("keywords", [])),
                list(meta.get("questions", [])),
            )
            availability = self._data_availability(
                report_scene_ids,
                request,
            )
            score += availability["score"]
            scored.append((score, "report", str(meta["id"]), report_scene_ids))
            candidates.append(
                {
                    "kind": "report",
                    "id": meta["id"],
                    "title": meta["title"],
                    "description": meta.get("description", ""),
                    "keywords": meta.get("keywords", []),
                    "questions": meta.get("questions", []),
                    "data_availability": availability,
                }
            )
        for manifest in self.templates.scene_manifests():
            score = _score_text(
                request.question, manifest.keywords, manifest.questions
            )
            availability = self._data_availability([manifest.id], request)
            score += availability["score"]
            scored.append((score, "scene", manifest.id, [manifest.id]))
            candidates.append(
                {
                    "kind": "scene",
                    "id": manifest.id,
                    "title": manifest.title,
                    "description": manifest.description,
                    "keywords": manifest.keywords,
                    "questions": manifest.questions,
                    "data_availability": availability,
                }
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        positive = [item for item in scored if item[0] > 0]
        if positive and (len(positive) == 1 or positive[0][0] > positive[1][0]):
            score, kind, selected_id, scene_ids = positive[0]
            decision = RoutingDecision(
                report_id=selected_id if kind == "report" else None,
                template_id=selected_id if kind == "scene" else None,
                scene_ids=scene_ids,
                parameters=request.parameters,
                confidence=min(0.99, 0.7 + score / 100),
            )
            return self._with_missing_parameters(decision)

        if self.semantic_router:
            decision = self.semantic_router.choose(
                request.question, candidates, request.parameters
            )
            decision.parameters = {**decision.parameters, **request.parameters}
            self._validate_selection(decision)
            return self._with_missing_parameters(decision)

        candidate_names = "、".join(
            candidate["title"] for candidate in candidates[:5]
        )
        return RoutingDecision(
            parameters=request.parameters,
            confidence=0,
            clarification=f"无法唯一匹配分析模板，请从以下候选中明确选择：{candidate_names}",
        )

    def _data_availability(
        self,
        scene_ids: list[str],
        request: AnalysisRequest,
    ) -> dict[str, Any]:
        if not request.data_bindings:
            return {
                "known": False,
                "available": 0,
                "required": len(scene_ids),
                "score": 0,
            }
        available = 0
        for scene_id in scene_ids:
            spec = self.templates.scene_spec(scene_id)
            query = self.templates.queries.get(spec.query_ref)
            if (
                query.id in request.data_bindings
                or query.binding_id in request.data_bindings
            ):
                available += 1
        required = len(scene_ids)
        return {
            "known": True,
            "available": available,
            "required": required,
            "score": int(20 * available / required) if required else 0,
        }

    def _validate_selection(self, decision: RoutingDecision) -> None:
        if decision.report_id and decision.template_id:
            raise RoutingError("语义路由不能同时选择报告和场景模板")
        if decision.report_id:
            report = self.templates.report(decision.report_id)
            expected = self.templates.report_scene_ids(decision.report_id)
            if decision.scene_ids and decision.scene_ids != expected:
                raise RoutingError("语义路由不能修改报告配方的场景列表")
            decision.scene_ids = expected
        elif decision.template_id:
            self.templates.scene_manifest(decision.template_id)
            if decision.scene_ids and decision.scene_ids != [decision.template_id]:
                raise RoutingError("语义路由不能修改场景模板对应的场景列表")
            decision.scene_ids = [decision.template_id]
        elif decision.scene_ids:
            for scene_id in decision.scene_ids:
                self.templates.scene_manifest(scene_id)
        else:
            raise RoutingError("语义路由没有选择报告或场景")

    def _with_missing_parameters(
        self, decision: RoutingDecision
    ) -> RoutingDecision:
        required: set[str] = set()
        if decision.report_id:
            report = self.templates.report(decision.report_id)
            for name, raw in report.get("parameters", {}).items():
                definition = ParameterDefinition.model_validate(raw)
                if definition.required and definition.default is None:
                    required.add(name)
        for scene_id in decision.scene_ids:
            spec = self.templates.scene_spec(scene_id)
            for name, definition in spec.parameters.items():
                if definition.required and definition.default is None:
                    required.add(name)
        decision.missing_parameters = sorted(
            name
            for name in required
            if decision.parameters.get(name) in {None, ""}
        )
        if decision.missing_parameters:
            decision.clarification = (
                "请补充必需参数：" + "、".join(decision.missing_parameters)
            )
        return decision


class DeepSeekSemanticRouter:
    """LangChain-backed router constrained to the RoutingDecision schema."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com",
    ):
        try:
            from langchain_deepseek import ChatDeepSeek
        except ImportError as exc:
            raise RoutingError("未安装 langchain-deepseek") from exc
        key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise RoutingError("使用 DeepSeek 路由需要 DEEPSEEK_API_KEY")
        model_name = model or os.getenv(
            "DEEPSEEK_ROUTER_MODEL",
            os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        )
        chat = ChatDeepSeek(
            model=model_name,
            api_key=key,
            base_url=base_url,
            temperature=0,
            max_retries=2,
            timeout=60,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self.model = chat.with_structured_output(
            RoutingDecision,
            method="json_mode",
        )

    def choose(
        self,
        question: str,
        candidates: list[dict[str, Any]],
        supplied_parameters: dict[str, Scalar],
    ) -> RoutingDecision:
        prompt = (
            "你是经营分析模板路由器。只能选择候选清单中的 report 或 scene，"
            "并抽取用户明确给出的参数。禁止生成查询、指标、分析步骤或阈值。"
            "以符合 RoutingDecision schema 的 JSON 返回；无法判断时 confidence 低于0.7"
            "并填写 clarification。\n\n"
            f"用户问题：{question}\n"
            f"已提供参数：{json.dumps(supplied_parameters, ensure_ascii=False)}\n"
            f"候选模板：{json.dumps(candidates, ensure_ascii=False)}"
        )
        try:
            return self.model.invoke(prompt)
        except Exception as exc:
            raise RoutingError(f"DeepSeek 路由失败：{exc}") from exc


class PlanCompiler:
    """Compile a validated routing decision into an immutable template plan."""

    def __init__(self, templates: TemplateRegistry):
        self.templates = templates

    def compile(
        self,
        decision: RoutingDecision,
        *,
        output_style: str | None = None,
    ) -> CompiledAnalysisPlan:
        if decision.confidence < 0.7:
            raise RoutingError(decision.clarification or "模板路由置信度不足")
        if decision.missing_parameters:
            raise RoutingError(decision.clarification or "缺少必需参数")
        if not decision.scene_ids:
            raise PlanError("路由结果没有场景")
        if output_style:
            try:
                self.templates.validate_expression_style(output_style)
            except ConfigurationError as exc:
                raise PlanError(str(exc)) from exc

        report: dict[str, Any] | None = None
        report_parameters: dict[str, Scalar] = {}
        callouts: list[dict[str, Any]] = []
        scene_requirements: dict[str, bool] = {}
        if decision.report_id:
            report = self.templates.report(decision.report_id)
            scene_requirements = {
                item["id"]: item["required"]
                for item in self.templates.report_scene_entries(
                    decision.report_id
                )
            }
            report_definitions = {
                name: ParameterDefinition.model_validate(raw)
                for name, raw in report.get("parameters", {}).items()
            }
            report_parameters = resolve_parameters(
                report_definitions,
                decision.parameters,
                label=f"报告 {decision.report_id}",
            )
            title = interpolate(str(report["report"]["title"]), report_parameters)
            callouts = copy.deepcopy(report.get("callouts", []))
        else:
            manifests = [
                self.templates.scene_manifest(scene_id)
                for scene_id in decision.scene_ids
            ]
            title = manifests[0].title if len(manifests) == 1 else "组合经营分析"

        declared_parameters: set[str] = set(report_parameters)
        scene_plans: list[CompiledScenePlan] = []
        for scene_id in decision.scene_ids:
            manifest = self.templates.scene_manifest(scene_id)
            spec = self.templates.scene_spec(scene_id)
            declared_parameters.update(spec.parameters)
            parameters = resolve_parameters(
                spec.parameters,
                decision.parameters,
                label=f"场景 {scene_id}",
            )
            expression = copy.deepcopy(spec.expression)
            if output_style:
                expression["style_profile"] = output_style
            scene_plans.append(
                CompiledScenePlan(
                    scene_id=scene_id,
                    version=manifest.version,
                    title=manifest.title,
                    required=scene_requirements.get(scene_id, True),
                    query_ref=spec.query_ref,
                    parameters=parameters,
                    steps=copy.deepcopy(spec.steps),
                    signals=copy.deepcopy(spec.signals),
                    expression=expression,
                )
            )
        if report:
            declared_parameters.update(report.get("parameters", {}))
        unknown = sorted(set(decision.parameters) - declared_parameters)
        if unknown:
            raise PlanError(f"参数未在模板中声明：{', '.join(unknown)}")
        return CompiledAnalysisPlan(
            report_id=decision.report_id,
            report_version=str(report["report"]["version"]) if report else None,
            title=title,
            parameters=report_parameters,
            scenes=scene_plans,
            callouts=callouts,
        )
