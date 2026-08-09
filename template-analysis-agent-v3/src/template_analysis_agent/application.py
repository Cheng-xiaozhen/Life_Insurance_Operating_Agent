"""Application service orchestrating the complete local V3 workflow."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .config import (
    MetricRegistry,
    QueryRegistry,
    SourceProfileRegistry,
    TemplateRegistry,
)
from .errors import AnalysisAgentError, ExecutionError, ExpressionError, QueryError
from .executor import DeterministicAnalyzer
from .expression import (
    DeepSeekExpressionProvider,
    DeterministicExpressionProvider,
    ExpressionAssetLoader,
    ExpressionProvider,
)
from .models import (
    AnalysisRequest,
    AnalysisRunResult,
    ExpressionResult,
    FactBundle,
    NarrativeDraft,
    ValidationReport,
)
from .query import QueryExecutor
from .reporting import AuditRecorder, ReportAssembler
from .routing import (
    DeepSeekSemanticRouter,
    IntentRouter,
    PlanCompiler,
    SemanticRouter,
)
from .validation import NarrativeValidator


class AnalysisApplication:
    """Facade for routing, compiling, querying, analyzing, expressing, and auditing."""

    def __init__(
        self,
        project_root: Path,
        *,
        expression_provider: ExpressionProvider | None = None,
        semantic_router: SemanticRouter | None = None,
    ):
        self.project_root = project_root.resolve()
        skill_assets = (
            self.project_root
            / "skills"
            / "monthly-performance-analysis"
            / "assets"
        )
        self.query_registry = QueryRegistry(
            self.project_root / "configs" / "data_queries"
        )
        self.profile_registry = SourceProfileRegistry(
            self.project_root / "configs" / "data_source_profiles"
        )
        self.metric_registry = MetricRegistry(skill_assets / "metrics")
        self.template_registry = TemplateRegistry(
            skill_assets,
            self.query_registry,
            self.metric_registry,
            self.profile_registry,
        )
        self.expression_assets = ExpressionAssetLoader(skill_assets)
        self.expression_provider_override = expression_provider
        self.semantic_router_override = semantic_router
        self.query_executor = QueryExecutor(self.profile_registry)
        self.analyzer = DeterministicAnalyzer()
        self.validator = NarrativeValidator()
        self.assembler = ReportAssembler()

    @classmethod
    def from_project_root(
        cls,
        project_root: str | Path,
        **kwargs: object,
    ) -> "AnalysisApplication":
        return cls(Path(project_root), **kwargs)

    def validate_configuration(self) -> dict[str, int]:
        return {
            "queries": len(self.query_registry.all()),
            "scenes": len(self.template_registry.scene_manifests()),
            "reports": len(self.template_registry.reports()),
        }

    def _route(self, request: AnalysisRequest):
        router = IntentRouter(
            self.template_registry,
            semantic_router=self.semantic_router_override,
        )
        decision = router.route(request)
        if (
            decision.confidence < 0.7
            and not self.semantic_router_override
            and request.expression_provider == "deepseek"
        ):
            router = IntentRouter(
                self.template_registry,
                semantic_router=DeepSeekSemanticRouter(),
            )
            decision = router.route(request)
        return decision

    def _provider_for(self, request: AnalysisRequest) -> ExpressionProvider:
        if self.expression_provider_override:
            return self.expression_provider_override
        if request.expression_provider == "deepseek":
            return DeepSeekExpressionProvider(self.expression_assets)
        return DeterministicExpressionProvider()

    def run(self, request: AnalysisRequest) -> AnalysisRunResult:
        run_id = uuid4().hex
        states: list[str] = []
        routing = self._route(request)
        states.append("ROUTED")
        plan = PlanCompiler(self.template_registry).compile(
            routing,
            output_style=request.output_style,
        )
        states.append("PLAN_COMPILED")

        self.query_executor.clear_cache()
        bundles: list[FactBundle] = []
        used_manifests = []
        for scene in plan.scenes:
            manifest = self.query_registry.get(scene.query_ref)
            used_manifests.append(manifest)
            binding = request.data_bindings.get(manifest.id) or request.data_bindings.get(
                manifest.binding_id
            )
            if binding is None:
                bundles.append(
                    FactBundle(
                        scene_id=scene.scene_id,
                        scene_title=scene.title,
                        status="insufficient_data",
                        parameters=scene.parameters,
                        errors=[
                            f"缺少数据绑定：{manifest.binding_id} ({manifest.id})"
                        ],
                    )
                )
                continue
            try:
                query_result = self.query_executor.execute(
                    manifest,
                    binding,
                    scene.parameters,
                )
                metrics = self.metric_registry.get(scene.scene_id)
                bundle = self.analyzer.execute_scene(
                    scene,
                    query_result.dataset,
                    metrics,
                )
            except (QueryError, ExecutionError, AnalysisAgentError) as exc:
                bundle = FactBundle(
                    scene_id=scene.scene_id,
                    scene_title=scene.title,
                    status="insufficient_data",
                    parameters=scene.parameters,
                    errors=[str(exc)],
                )
            bundles.append(bundle)
        states.extend(["QUERY_EXECUTED", "DATA_NORMALIZED", "FACTS_COMPUTED"])

        provider = self._provider_for(request)
        final_drafts: list[NarrativeDraft] = []
        expression_attempts: list[ExpressionResult] = []
        validations: list[ValidationReport] = []
        plan_index = {scene.scene_id: scene for scene in plan.scenes}
        for bundle in bundles:
            if bundle.status == "insufficient_data":
                validations.append(
                    ValidationReport(
                        scene_id=bundle.scene_id,
                        valid=True,
                        errors=["表达已跳过：必需数据不可用"],
                    )
                )
                continue
            repair_errors: list[str] = []
            final_result: ExpressionResult | None = None
            final_validation: ValidationReport | None = None
            for _ in range(3):
                try:
                    result = provider.express(
                        bundle,
                        plan_index[bundle.scene_id].expression,
                        repair_errors,
                    )
                except ExpressionError as exc:
                    repair_errors = [str(exc)]
                    continue
                expression_attempts.append(result)
                validation = self.validator.validate(bundle, result.draft)
                if validation.valid:
                    final_result = result
                    final_validation = validation
                    break
                repair_errors = validation.errors
            if final_result is None:
                fallback = DeterministicExpressionProvider().express(
                    bundle,
                    plan_index[bundle.scene_id].expression,
                    repair_errors,
                )
                expression_attempts.append(fallback)
                fallback_validation = self.validator.validate(bundle, fallback.draft)
                if not fallback_validation.valid:
                    raise ExpressionError(
                        "安全降级表达未通过校验："
                        + "；".join(fallback_validation.errors)
                    )
                final_result = fallback
                final_validation = fallback_validation
            final_drafts.append(final_result.draft)
            validations.append(final_validation)
        states.extend(["NARRATIVE_GENERATED", "NARRATIVE_VALIDATED"])

        report_markdown = self.assembler.assemble(plan, bundles, final_drafts)
        states.append("REPORT_ASSEMBLED")
        states.append("COMPLETED")
        output_root = (
            Path(request.output_dir).resolve()
            if request.output_dir
            else self.project_root / "runs"
        )
        audit_path = AuditRecorder(output_root).record(
            run_id=run_id,
            request=request,
            routing=routing,
            plan=plan,
            query_manifests=used_manifests,
            query_records=self.query_executor.execution_records,
            bundles=bundles,
            expressions=expression_attempts,
            validations=validations,
            report_markdown=report_markdown,
            state_history=states,
        )
        return AnalysisRunResult(
            run_id=run_id,
            state_history=states,
            routing=routing,
            plan=plan,
            fact_bundles=bundles,
            narratives=final_drafts,
            validations=validations,
            report_markdown=report_markdown,
            audit_path=str(audit_path),
        )
