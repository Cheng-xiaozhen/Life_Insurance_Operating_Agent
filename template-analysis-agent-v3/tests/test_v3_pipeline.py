from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from pydantic import ValidationError

from template_analysis_agent.application import AnalysisApplication
from template_analysis_agent.config import (
    FORBIDDEN_SCENE_KEYS,
    iter_keys,
    load_yaml,
)
from template_analysis_agent.errors import ConfigurationError, PlanError, QueryError
from template_analysis_agent.expression import (
    DeterministicExpressionProvider,
    ExpressionAssetLoader,
    ReplayExpressionProvider,
)
from template_analysis_agent.models import (
    AnalysisRequest,
    DataBinding,
    ExpressionResult,
    NarrativeBlock,
    NarrativeDraft,
    RoutingDecision,
)
from template_analysis_agent.routing import IntentRouter, PlanCompiler


V3_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = V3_ROOT.parent
ASSETS_ROOT = (
    V3_ROOT / "skills" / "monthly-performance-analysis" / "assets"
)
SCENE_ROOT = ASSETS_ROOT / "scenes"
PROFILE_ROOT = V3_ROOT / "configs" / "data_source_profiles"

DATASET_PATHS = {
    "standard_premium": REPOSITORY_ROOT / "docs" / "标保.csv",
    "value": REPOSITORY_ROOT / "docs" / "价值.csv",
    "active_manpower": REPOSITORY_ROOT / "docs" / "活动人力.csv",
    "sunshine_manpower": REPOSITORY_ROOT / "docs" / "阳光人力.csv",
    "supervisor_activity": REPOSITORY_ROOT / "docs" / "主管活动.csv",
    "supervisor_double_star": REPOSITORY_ROOT / "docs" / "主管双星.csv",
    "standard_team": REPOSITORY_ROOT / "docs" / "标准组.csv",
    "recruitment": REPOSITORY_ROOT / "docs" / "新增.csv",
    "co_recruitment": REPOSITORY_ROOT / "docs" / "同引.csv",
}
REPORT_PARAMETERS = {
    "report_month_name": "五月",
    "data_month_name": "5月",
    "quarter_name": "二季度",
    "cutoff_date": "5月31日",
}


def bindings() -> dict[str, DataBinding]:
    return {
        binding_id: DataBinding(source=str(path))
        for binding_id, path in DATASET_PATHS.items()
    }


def full_request(output_dir: str) -> AnalysisRequest:
    return AnalysisRequest(
        question="生成五月业绩分析报告",
        report_id="monthly-performance",
        parameters=REPORT_PARAMETERS,
        data_bindings=bindings(),
        output_dir=output_dir,
    )


def scene_request(
    scene_id: str,
    binding_id: str,
    output_dir: str,
) -> AnalysisRequest:
    scene_config = load_yaml(SCENE_ROOT / scene_id / "scene.yaml")
    scene_parameters = {
        name: value
        for name, value in REPORT_PARAMETERS.items()
        if name in scene_config.get("parameters", {})
    }
    return AnalysisRequest(
        question=f"分析{scene_id}",
        scene_ids=[scene_id],
        parameters=scene_parameters,
        data_bindings={
            binding_id: DataBinding(source=str(DATASET_PATHS[binding_id]))
        },
        output_dir=output_dir,
    )


class _InvalidExpressionProvider:
    name = "invalid"

    def __init__(self) -> None:
        self.calls = 0

    def express(self, bundle, expression, repair_errors=None):
        del expression, repair_errors
        self.calls += 1
        fact = bundle.facts[0]
        return ExpressionResult(
            draft=NarrativeDraft(
                scene_id=bundle.scene_id,
                blocks=[
                    NarrativeBlock(
                        fact_refs=[fact.fact_id],
                        markdown=f"- {fact.label}：999999。",
                    )
                ],
            ),
            provider=self.name,
        )


class _NeverExpressionProvider:
    name = "never"

    def __init__(self) -> None:
        self.calls = 0

    def express(self, bundle, expression, repair_errors=None):
        del bundle, expression, repair_errors
        self.calls += 1
        raise AssertionError("insufficient_data 场景不得调用表达器")


class _TemplateOnlySemanticRouter:
    def choose(self, question, candidates, supplied_parameters):
        del question, candidates
        return RoutingDecision(
            template_id="standard-premium",
            parameters=supplied_parameters,
            confidence=0.91,
        )


class _SlowMemoryAdapter:
    name = "memory"

    def fingerprint(self, binding):
        del binding
        return "slow-memory"

    def load(self, manifest, binding, parameters):
        del manifest, binding, parameters
        time.sleep(0.03)
        raise AssertionError("超时查询的结果不应被采用")


class V3BaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.application = AnalysisApplication(V3_ROOT)
        cls.result = cls.application.run(full_request(cls.temporary.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def bundle(self, scene_id: str):
        return next(
            bundle
            for bundle in self.result.fact_bundles
            if bundle.scene_id == scene_id
        )


class ConfigurationAndRoutingTests(V3BaseTest):
    def test_all_registered_assets_load(self) -> None:
        self.assertEqual(
            self.application.validate_configuration(),
            {"queries": 9, "scenes": 9, "reports": 1},
        )
        for manifest in self.application.query_registry.all():
            self.assertEqual(
                manifest.handler_ref,
                "template_analysis_agent.query.CsvDataAdapter",
            )
            self.assertEqual(manifest.source.kind, "local_binding")
            self.assertEqual(
                manifest.permissions.required_scopes,
                ["monthly-performance:read"],
            )

    def test_public_contracts_generate_strict_json_schema(self) -> None:
        schema = AnalysisRequest.model_json_schema()
        self.assertEqual(schema["additionalProperties"], False)
        with self.assertRaises(ValidationError):
            AnalysisRequest.model_validate(
                {"question": "测试", "unregistered_field": True}
            )

    def test_scene_files_have_no_physical_or_presentation_fields(self) -> None:
        for path in SCENE_ROOT.glob("*/scene.yaml"):
            raw = load_yaml(path)
            forbidden = set(iter_keys(raw)) & FORBIDDEN_SCENE_KEYS
            self.assertEqual(forbidden, set(), str(path))
            serialized = path.read_text(encoding="utf-8").casefold()
            self.assertNotIn("csv", serialized)
            self.assertNotIn("jinja", serialized)
            self.assertNotIn("sql", serialized)

    def test_physical_mappings_live_in_source_profiles(self) -> None:
        profiles = list(PROFILE_ROOT.glob("*.yaml"))
        self.assertEqual(len(profiles), 9)
        for path in profiles:
            raw = load_yaml(path)
            self.assertIn("row_sets", raw)
            definitions = {
                **raw.get("dimensions", {}),
                **raw.get("metrics", {}),
            }
            self.assertTrue(definitions)
            self.assertTrue(
                all("column" in definition for definition in definitions.values())
            )

    def test_unknown_query_and_step_are_rejected(self) -> None:
        manifest = self.application.template_registry.scene_manifest(
            "standard-premium"
        )
        original = self.application.template_registry.scene_spec(
            "standard-premium"
        )
        unknown_query = original.model_copy(
            update={"query_ref": "unregistered.query"},
            deep=True,
        )
        with self.assertRaises(ConfigurationError):
            self.application.template_registry._validate_scene(
                manifest, unknown_query
            )
        unknown_step = original.model_copy(deep=True)
        unknown_step.steps[0]["type"] = "run_python"
        with self.assertRaises(ConfigurationError):
            self.application.template_registry._validate_scene(
                manifest, unknown_step
            )

    def test_expression_assets_include_style_glossary_and_example(self) -> None:
        loader = ExpressionAssetLoader(ASSETS_ROOT)
        self.assertEqual(
            loader.load_style("monthly-operation-report")["style"]["language"],
            "zh-CN",
        )
        self.assertIn("relations", loader.load_glossary())
        examples = loader.load_examples(
            ["summary", "threshold-classification"]
        )
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["facts"]["scene_id"], "standard-premium")
        self.assertIn("12444万", examples[0]["markdown"])

    def test_router_selects_one_scene_without_generating_a_plan(self) -> None:
        request = AnalysisRequest(
            question="哪些机构的标保达成率偏低？",
            parameters={"data_month_name": "5月", "cutoff_date": "5月31日"},
        )
        decision = self.application._route(request)
        self.assertEqual(decision.template_id, "standard-premium")
        self.assertEqual(decision.scene_ids, ["standard-premium"])
        self.assertFalse(hasattr(decision, "steps"))

    def test_router_selects_full_report_and_detects_missing_parameters(self) -> None:
        decision = self.application._route(
            AnalysisRequest(
                question="生成月度业绩分析报告",
                report_id="monthly-performance",
            )
        )
        self.assertEqual(decision.report_id, "monthly-performance")
        self.assertEqual(len(decision.scene_ids), 9)
        self.assertEqual(
            decision.missing_parameters,
            ["cutoff_date", "data_month_name", "quarter_name", "report_month_name"],
        )

    def test_router_does_not_fall_back_when_no_candidate_is_clear(self) -> None:
        decision = self.application._route(
            AnalysisRequest(question="请帮我看一下数据")
        )
        self.assertEqual(decision.confidence, 0)
        self.assertIsNotNone(decision.clarification)
        self.assertEqual(decision.scene_ids, [])

    def test_router_uses_available_data_to_disambiguate_candidates(self) -> None:
        decision = self.application._route(
            AnalysisRequest(
                question="分析本月经营情况",
                data_bindings={
                    "value": DataBinding(source=str(DATASET_PATHS["value"]))
                },
            )
        )
        self.assertEqual(decision.template_id, "value")
        self.assertEqual(decision.scene_ids, ["value"])

    def test_semantic_router_template_id_is_registry_checked_and_expanded(self) -> None:
        router = IntentRouter(
            self.application.template_registry,
            semantic_router=_TemplateOnlySemanticRouter(),
        )
        decision = router.route(
            AnalysisRequest(
                question="请帮我看看",
                parameters={"data_month_name": "5月", "cutoff_date": "5月31日"},
            )
        )
        self.assertEqual(decision.scene_ids, ["standard-premium"])
        plan = PlanCompiler(self.application.template_registry).compile(decision)
        self.assertEqual(plan.scenes[0].scene_id, "standard-premium")

    def test_plan_compiler_applies_only_declared_overrides(self) -> None:
        decision = RoutingDecision(
            template_id="standard-premium",
            scene_ids=["standard-premium"],
            parameters={
                "data_month_name": "5月",
                "cutoff_date": "5月31日",
                "monthly_high_threshold": 80,
            },
            confidence=1,
        )
        plan = PlanCompiler(self.application.template_registry).compile(decision)
        self.assertEqual(plan.scenes[0].parameters["monthly_high_threshold"], 80)
        styled_plan = PlanCompiler(self.application.template_registry).compile(
            decision,
            output_style="monthly-operation-report",
        )
        self.assertEqual(
            styled_plan.scenes[0].expression["style_profile"],
            "monthly-operation-report",
        )
        with self.assertRaises(PlanError):
            PlanCompiler(self.application.template_registry).compile(
                decision,
                output_style="../unknown",
            )
        decision.parameters["free_sql"] = "select *"
        with self.assertRaises(PlanError):
            PlanCompiler(self.application.template_registry).compile(decision)


class QueryAndExecutionTests(V3BaseTest):
    def test_query_cache_is_scoped_by_query_parameters_and_source_hash(self) -> None:
        executor = self.application.query_executor
        executor.clear_cache()
        manifest = self.application.query_registry.get(
            "monthly-performance.standard-premium"
        )
        binding = DataBinding(source=str(DATASET_PATHS["standard_premium"]))
        parameters = {"data_month_name": "5月"}
        first = executor.execute(manifest, binding, parameters)
        second = executor.execute(manifest, binding, parameters)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first.dataset.source_hash, second.dataset.source_hash)
        records = executor.execution_records
        self.assertEqual(len(records), 2)
        self.assertFalse(records[0].cache_hit)
        self.assertTrue(records[1].cache_hit)
        self.assertTrue(all(record.status == "success" for record in records))

    def test_memory_adapter_accepts_the_same_canonical_rows(self) -> None:
        executor = self.application.query_executor
        executor.clear_cache()
        manifest = self.application.query_registry.get(
            "monthly-performance.standard-premium"
        )
        csv_result = executor.execute(
            manifest,
            DataBinding(source=str(DATASET_PATHS["standard_premium"])),
            {"data_month_name": "5月"},
        )
        memory_binding = DataBinding(
            adapter="memory",
            profile_id="standard-premium",
            records=[
                row.model_dump(mode="json")
                for row in csv_result.dataset.rows
            ],
        )
        memory_result = executor.execute(
            manifest,
            memory_binding,
            {"data_month_name": "5月"},
        )
        self.assertEqual(
            [row.model_dump() for row in csv_result.dataset.rows],
            [row.model_dump() for row in memory_result.dataset.rows],
        )

    def test_dynamic_month_column_must_exist(self) -> None:
        self.application.query_executor.clear_cache()
        manifest = self.application.query_registry.get(
            "monthly-performance.standard-premium"
        )
        with self.assertRaises(QueryError):
            self.application.query_executor.execute(
                manifest,
                DataBinding(source=str(DATASET_PATHS["standard_premium"])),
                {"data_month_name": "不存在月份"},
            )

    def test_memory_dataset_requires_exactly_one_total_and_details(self) -> None:
        self.application.query_executor.clear_cache()
        manifest = self.application.query_registry.get(
            "monthly-performance.standard-team"
        )
        binding = DataBinding(
            adapter="memory",
            records=[
                {
                    "source_index": 1,
                    "role": "total",
                    "organization": None,
                    "values": {},
                },
                {
                    "source_index": 2,
                    "role": "total",
                    "organization": None,
                    "values": {},
                },
                {
                    "source_index": 3,
                    "role": "detail",
                    "organization": "测试机构",
                    "values": {},
                },
            ],
        )
        with self.assertRaises(QueryError):
            self.application.query_executor.execute(manifest, binding, {})

    def test_query_timeout_is_enforced(self) -> None:
        executor = self.application.query_executor
        executor.clear_cache()
        original_adapter = executor.adapters["memory"]
        executor.adapters["memory"] = _SlowMemoryAdapter()
        manifest = self.application.query_registry.get(
            "monthly-performance.standard-team"
        ).model_copy(update={"timeout_ms": 1})
        binding = DataBinding(adapter="memory", records=[])
        try:
            with self.assertRaisesRegex(QueryError, "超时"):
                executor.execute(manifest, binding, {})
            self.assertEqual(executor.execution_records[-1].status, "timeout")
        finally:
            executor.adapters["memory"] = original_adapter

    def test_all_scenes_produce_source_backed_facts_and_signals(self) -> None:
        self.assertEqual(len(self.result.fact_bundles), 9)
        for bundle in self.result.fact_bundles:
            self.assertEqual(bundle.status, "ready")
            self.assertTrue(bundle.facts)
            for fact in bundle.facts:
                self.assertTrue(fact.query_id.startswith("monthly-performance."))
                self.assertEqual(fact.query_version, "1.0.0")
                if fact.fact_type == "summary" or fact.organizations:
                    self.assertTrue(fact.source_rows)
        premium = self.bundle("standard-premium")
        self.assertEqual(
            premium.signals["target_met"],
            "standard-premium.monthly_rate_classification.target_met",
        )
        self.assertEqual(
            premium.signals["severe_low"],
            "standard-premium.monthly_rate_classification.very_low",
        )

    def test_threshold_sorting_and_compound_conditions_match_expected_results(
        self,
    ) -> None:
        premium = {
            fact.fact_id: fact for fact in self.bundle("standard-premium").facts
        }
        self.assertEqual(
            premium[
                "standard-premium.monthly_rate_classification.high"
            ].organizations,
            ["河南", "天津", "潍坊", "云南", "新疆", "海南", "内蒙古", "宁波", "甘肃"],
        )
        recruitment = {
            fact.fact_id: fact for fact in self.bundle("recruitment").facts
        }
        middle = recruitment[
            "recruitment.recruitment_rate_classification.middle"
        ]
        self.assertEqual(middle.condition["match"], "all")
        self.assertEqual(middle.organizations, ["河北", "河南", "山西", "青岛"])
        co_recruitment = {
            fact.fact_id: fact for fact in self.bundle("co-recruitment").facts
        }
        zero = co_recruitment[
            "co-recruitment.zero_classification.both_zero"
        ]
        self.assertEqual(zero.condition["match"], "all")
        self.assertEqual(
            zero.organizations,
            ["重庆", "福建", "天津", "贵州", "宁波", "海南", "深圳"],
        )


class NarrativeSafetyTests(V3BaseTest):
    def setUp(self) -> None:
        self.bundle_under_test = self.bundle("standard-premium")
        self.provider = DeterministicExpressionProvider()
        self.draft = self.provider.express(
            self.bundle_under_test, {}
        ).draft

    def validate(self, draft: NarrativeDraft):
        return self.application.validator.validate(
            self.bundle_under_test, draft
        )

    def test_deterministic_expression_is_valid(self) -> None:
        report = self.validate(self.draft)
        self.assertTrue(report.valid, report.errors)

    def test_replay_provider_supports_offline_expression_tests(self) -> None:
        replay = ReplayExpressionProvider(
            {self.bundle_under_test.scene_id: self.draft}
        )
        result = replay.express(self.bundle_under_test, {})
        self.assertEqual(result.provider, "replay")
        self.assertTrue(self.validate(result.draft).valid)

    def test_unknown_number_is_rejected(self) -> None:
        draft = self.draft.model_copy(deep=True)
        draft.blocks[0].markdown += " 另有999999万。"
        report = self.validate(draft)
        self.assertFalse(report.valid)
        self.assertTrue(any("未授权数字" in error for error in report.errors))

    def test_extra_organization_is_rejected(self) -> None:
        draft = self.draft.model_copy(deep=True)
        high = next(
            block
            for block in draft.blocks
            if block.fact_refs
            == ["standard-premium.monthly_rate_classification.high"]
        )
        high.markdown = high.markdown.removesuffix("。") + "、火星。"
        report = self.validate(draft)
        self.assertFalse(report.valid)
        self.assertTrue(any("事实外名单项" in error for error in report.errors))

    def test_wrong_organization_count_is_rejected(self) -> None:
        draft = self.draft.model_copy(deep=True)
        high = next(
            block
            for block in draft.blocks
            if block.fact_refs
            == ["standard-premium.monthly_rate_classification.high"]
        )
        high.markdown = high.markdown.replace("共9家", "共5家")
        report = self.validate(draft)
        self.assertFalse(report.valid)
        self.assertTrue(any("机构数量" in error for error in report.errors))

    def test_wrong_comparison_relation_is_rejected(self) -> None:
        draft = self.draft.model_copy(deep=True)
        high = next(
            block
            for block in draft.blocks
            if block.fact_refs
            == ["standard-premium.monthly_rate_classification.high"]
        )
        high.markdown = high.markdown.replace("高于", "低于")
        report = self.validate(draft)
        self.assertFalse(report.valid)
        self.assertTrue(any("运算符 gt" in error for error in report.errors))

    def test_wrong_yoy_direction_is_rejected(self) -> None:
        draft = self.draft.model_copy(deep=True)
        yoy = next(
            block
            for block in draft.blocks
            if block.fact_refs
            == ["standard-premium.overall_summary.month_yoy"]
        )
        yoy.markdown += " 同比增长。"
        report = self.validate(draft)
        self.assertFalse(report.valid)
        self.assertTrue(any("方向" in error for error in report.errors))

    def test_missing_required_fact_is_rejected(self) -> None:
        draft = self.draft.model_copy(deep=True)
        draft.blocks.pop()
        report = self.validate(draft)
        self.assertFalse(report.valid)
        self.assertTrue(any("缺少必需事实" in error for error in report.errors))

    def test_advice_is_rejected(self) -> None:
        draft = self.draft.model_copy(deep=True)
        draft.blocks[0].markdown += " 建议加强推动。"
        report = self.validate(draft)
        self.assertFalse(report.valid)
        self.assertTrue(any("禁止表达" in error for error in report.errors))

    def test_three_failed_repairs_use_valid_deterministic_fallback(self) -> None:
        provider = _InvalidExpressionProvider()
        with tempfile.TemporaryDirectory() as output_dir:
            application = AnalysisApplication(
                V3_ROOT,
                expression_provider=provider,
            )
            result = application.run(
                scene_request(
                    "standard-premium",
                    "standard_premium",
                    output_dir,
                )
            )
            self.assertEqual(provider.calls, 3)
            self.assertEqual(len(result.narratives), 1)
            self.assertTrue(result.validations[0].valid)
            attempts = json.loads(
                (Path(result.audit_path) / "model-response.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                [attempt["provider"] for attempt in attempts],
                ["invalid", "invalid", "invalid", "deterministic"],
            )

    def test_insufficient_data_skips_expression(self) -> None:
        provider = _NeverExpressionProvider()
        with tempfile.TemporaryDirectory() as output_dir:
            application = AnalysisApplication(
                V3_ROOT,
                expression_provider=provider,
            )
            result = application.run(
                AnalysisRequest(
                    question="分析价值",
                    scene_ids=["value"],
                    parameters={
                        "data_month_name": "5月",
                        "cutoff_date": "5月31日",
                    },
                    output_dir=output_dir,
                )
            )
            self.assertEqual(provider.calls, 0)
            self.assertEqual(result.fact_bundles[0].status, "insufficient_data")
            self.assertEqual(result.narratives, [])
            self.assertIn("数据不足，未生成分析", result.report_markdown)


class EndToEndAndRegressionTests(V3BaseTest):
    def test_complete_state_machine_and_report_assembly(self) -> None:
        self.assertEqual(
            self.result.state_history,
            [
                "ROUTED",
                "PLAN_COMPILED",
                "QUERY_EXECUTED",
                "DATA_NORMALIZED",
                "FACTS_COMPUTED",
                "NARRATIVE_GENERATED",
                "NARRATIVE_VALIDATED",
                "REPORT_ASSEMBLED",
                "COMPLETED",
            ],
        )
        self.assertIn("# 五月业绩分析报告", self.result.report_markdown)
        self.assertIn("## 贺报", self.result.report_markdown)
        self.assertIn("天津、新疆", self.result.report_markdown)
        self.assertEqual(len(self.result.validations), 9)
        self.assertTrue(all(item.valid for item in self.result.validations))

    def test_all_required_audit_artifacts_are_written(self) -> None:
        audit_path = Path(self.result.audit_path)
        expected = {
            "request.json",
            "routing.json",
            "plan.json",
            "query-manifest.json",
            "facts.json",
            "model-response.json",
            "validation.json",
            "report.md",
        }
        self.assertEqual(
            {path.name for path in audit_path.iterdir()},
            expected,
        )
        validation = json.loads(
            (audit_path / "validation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(validation["state_history"][-1], "COMPLETED")
        query_audit = json.loads(
            (audit_path / "query-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(query_audit["contracts"]), 9)
        self.assertEqual(len(query_audit["calls"]), 9)
        self.assertTrue(
            all(call["status"] == "success" for call in query_audit["calls"])
        )

    def test_v3_facts_are_exactly_equivalent_to_v2_context(self) -> None:
        context_files = list(
            (
                REPOSITORY_ROOT
                / "monthly-performance-analysis-template-v2"
                / "output"
            ).glob("*.context.json")
        )
        full_contexts = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in context_files
            if len(
                json.loads(path.read_text(encoding="utf-8")).get("scenes", {})
            )
            == 9
        ]
        self.assertEqual(len(full_contexts), 1)
        v2_scenes = full_contexts[0]["scenes"]
        v3_bundles = {
            bundle.scene_id: bundle
            for bundle in self.result.fact_bundles
        }
        compared = 0
        for scene_id, v2_scene in v2_scenes.items():
            v3_facts = {
                fact.fact_id: fact
                for fact in v3_bundles[scene_id].facts
            }
            for step in v2_scene["steps"]:
                if step["type"] == "summarize":
                    for metric in step["metrics"]:
                        fact_id = (
                            f"{scene_id}.{step['id']}.{metric['id']}"
                        )
                        self.assertIn(fact_id, v3_facts)
                        self.assertEqual(
                            v3_facts[fact_id].raw_value,
                            metric["value"],
                            fact_id,
                        )
                        compared += 1
                    continue
                for band in step["bands"]:
                    fact_id = f"{scene_id}.{step['id']}.{band['id']}"
                    self.assertIn(fact_id, v3_facts)
                    fact = v3_facts[fact_id]
                    self.assertEqual(
                        fact.organizations,
                        band["organizations"],
                        fact_id,
                    )
                    self.assertEqual(fact.count, band["count"], fact_id)
                    v2_rules = band.get("conditions", {}).get("rules", [])
                    v3_rules = (fact.condition or {}).get("rules", [])
                    self.assertEqual(len(v2_rules), len(v3_rules), fact_id)
                    for v2_rule, v3_rule in zip(v2_rules, v3_rules):
                        self.assertEqual(
                            v3_rule["metric_id"].rsplit(".", 1)[-1],
                            v2_rule["metric"],
                            fact_id,
                        )
                        self.assertEqual(
                            v3_rule["operator"],
                            v2_rule["operator"],
                            fact_id,
                        )
                        self.assertEqual(
                            v3_rule["threshold"],
                            v2_rule["threshold"],
                            fact_id,
                        )
                    compared += 1
        self.assertEqual(compared, 73)

    def test_reference_values_remain_stable(self) -> None:
        facts = {
            fact.fact_id: fact
            for fact in self.bundle("standard-premium").facts
        }
        self.assertEqual(
            facts[
                "standard-premium.overall_summary.month_rate"
            ].display_value,
            "61.8%",
        )
        self.assertEqual(
            facts[
                "standard-premium.overall_summary.month_yoy"
            ].direction,
            "decrease",
        )
        recruitment = {
            fact.fact_id: fact for fact in self.bundle("recruitment").facts
        }
        self.assertEqual(
            recruitment[
                "recruitment.overall_summary.added_count"
            ].display_value,
            "739人",
        )


if __name__ == "__main__":
    unittest.main()
