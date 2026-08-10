from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from template_analysis_agent_v4 import (  # noqa: E402
    AnalysisAgent,
    AnalysisDataset,
    AnalysisRequest,
)
from template_analysis_agent_v4.cli import run as cli_run  # noqa: E402
from template_analysis_agent_v4.expression import (  # noqa: E402
    DeterministicExpressionProvider,
    NarrativeValidator,
    _dotenv_value,
)
from template_analysis_agent_v4.models import (  # noqa: E402
    ExpressionError,
    NarrativeBlock,
    TemplateError,
)
from template_analysis_agent_v4.templates import load_templates  # noqa: E402


def load_example(name: str) -> AnalysisDataset:
    return AnalysisDataset.model_validate_json(
        (ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8")
    )


class ValidFakeProvider:
    name = "deepseek"

    def express(self, title, facts):
        return DeterministicExpressionProvider().express(title, facts)


class InvalidFakeProvider:
    name = "deepseek"

    def express(self, title, facts):
        blocks = DeterministicExpressionProvider().express(title, facts)
        blocks[0] = NarrativeBlock(
            fact_id=blocks[0].fact_id,
            text=blocks[0].text + "预计增长999%。",
        )
        return blocks


class FailingFakeProvider:
    name = "deepseek"

    def express(self, title, facts):
        raise ExpressionError("模拟模型故障")


class TemplateTests(unittest.TestCase):
    def test_two_templates_load(self):
        templates = load_templates(ROOT / "templates")
        self.assertEqual(set(templates), {"standard-premium", "recruitment"})

    def test_unknown_metric_is_rejected(self):
        source = (ROOT / "templates" / "standard-premium.yaml").read_text(
            encoding="utf-8"
        )
        source = source.replace(
            "metrics: [month_amount, month_rate, month_yoy, year_amount, year_progress, year_yoy]",
            "metrics: [unknown_metric]",
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "bad.yaml").write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(TemplateError, "未声明指标"):
                load_templates(directory)

    def test_unknown_operator_and_formatter_are_rejected(self):
        source = (ROOT / "templates" / "standard-premium.yaml").read_text(
            encoding="utf-8"
        )
        for old, new in [("operator: gte", "operator: between"), ("formatter: wan", "formatter: money")]:
            with self.subTest(new=new), tempfile.TemporaryDirectory() as directory:
                Path(directory, "bad.yaml").write_text(
                    source.replace(old, new, 1), encoding="utf-8"
                )
                with self.assertRaises(TemplateError):
                    load_templates(directory)

    def test_unknown_step_is_rejected(self):
        source = (ROOT / "templates" / "standard-premium.yaml").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "bad.yaml").write_text(
                source.replace("op: rank", "op: calculate", 1),
                encoding="utf-8",
            )
            with self.assertRaises(TemplateError):
                load_templates(directory)

    def test_required_parameter_requests_clarification(self):
        source = (ROOT / "templates" / "standard-premium.yaml").read_text(
            encoding="utf-8"
        )
        source = source.replace(
            "  monthly_target:\n    type: number\n    default: 100",
            "  monthly_target:\n    type: number\n    required: true",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "required.yaml").write_text(source, encoding="utf-8")
            agent = AnalysisAgent(directory)
            result = agent.analyze(
                AnalysisRequest(
                    template_id="standard-premium",
                    dataset=load_example("standard-premium"),
                )
            )
            self.assertEqual(result.status, "needs_clarification")
            self.assertIn("monthly_target", result.routing.clarification)


class RoutingAndExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = AnalysisAgent(ROOT / "templates")
        cls.premium = load_example("standard-premium")
        cls.recruitment = load_example("recruitment")

    def test_unique_tie_and_no_match_routing(self):
        selected = self.agent.analyze(
            AnalysisRequest(question="看看标保排名", dataset=self.premium)
        )
        self.assertEqual(selected.template_id, "standard-premium")

        tied = self.agent.analyze(
            AnalysisRequest(question="标保新增", dataset=self.premium)
        )
        self.assertEqual(tied.status, "needs_clarification")
        self.assertIn("多个同分模板", tied.routing.clarification)

        missing = self.agent.analyze(
            AnalysisRequest(question="看看库存", dataset=self.premium)
        )
        self.assertEqual(missing.status, "needs_clarification")
        self.assertIn("没有匹配到模板", missing.routing.clarification)

    def test_explicit_template_and_unknown_parameter(self):
        result = self.agent.analyze(
            AnalysisRequest(
                template_id="standard-premium",
                dataset=self.premium,
                parameters={"unknown": 1},
            )
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("未声明参数", result.errors[0])

    def test_standard_premium_rank_and_exclusive_bands(self):
        result = self.agent.analyze(
            AnalysisRequest(template_id="standard-premium", dataset=self.premium)
        )
        self.assertEqual(result.status, "completed")
        facts = {fact.fact_id: fact for fact in result.facts}
        top = facts["standard-premium.top5"]
        self.assertEqual(
            [item.organization for item in top.items],
            ["新疆", "天津", "河南", "宁波", "安徽"],
        )
        target = facts["standard-premium.monthly_bands.target"]
        high = facts["standard-premium.monthly_bands.high"]
        severe = facts["standard-premium.monthly_bands.severe_low"]
        low = facts["standard-premium.monthly_bands.low"]
        groups = [
            {item.organization for item in fact.items}
            for fact in [target, high, severe, low]
        ]
        self.assertEqual(groups[0], {"天津", "新疆"})
        self.assertEqual(groups[1], {"河南", "宁波"})
        self.assertEqual(groups[2], {"深圳"})
        self.assertEqual(groups[3], {"上海", "安徽", "北京"})
        self.assertEqual(sum(map(len, groups)), len(set().union(*groups)))

    def test_recruitment_compound_and_missing_rules(self):
        result = self.agent.analyze(
            AnalysisRequest(template_id="recruitment", dataset=self.recruitment)
        )
        self.assertEqual(result.status, "completed")
        facts = {fact.fact_id: fact for fact in result.facts}
        middle = facts["recruitment.recruitment_bands.middle"]
        not_met = facts["recruitment.bachelor_bands.not_met"]
        self.assertEqual(
            {item.organization for item in middle.items}, {"河北", "河南"}
        )
        self.assertEqual(
            {item.organization for item in not_met.items},
            {"河南", "广东", "天津", "宁波", "海南"},
        )
        hainan = next(item for item in not_met.items if item.organization == "海南")
        self.assertIsNone(hainan.raw_value)

    def test_same_input_is_reproducible(self):
        request = AnalysisRequest(
            template_id="standard-premium", dataset=self.premium
        )
        first = self.agent.analyze(request)
        second = self.agent.analyze(request)
        self.assertEqual(first.model_dump(), second.model_dump())


class ExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_example("standard-premium")
        cls.base = AnalysisAgent(ROOT / "templates").analyze(
            AnalysisRequest(template_id="standard-premium", dataset=cls.dataset)
        )

    def test_validator_rejects_unknown_number_organization_duplicate_and_advice(self):
        facts = self.base.facts
        universe = [row.organization for row in self.dataset.rows]
        validator = NarrativeValidator()
        base_blocks = self.base.blocks
        cases = [
            NarrativeBlock(fact_id=base_blocks[0].fact_id, text="- 月度标保达成：999万。"),
            NarrativeBlock(fact_id=base_blocks[0].fact_id, text="- 月度标保达成：12444万，河南表现。"),
            NarrativeBlock(fact_id=base_blocks[0].fact_id, text="- 月度标保达成：12444万，建议优化。"),
        ]
        for block in cases:
            with self.subTest(text=block.text):
                candidate = [block, *base_blocks[1:]]
                self.assertFalse(validator.validate(facts, candidate, universe).valid)
        duplicate = [*base_blocks, base_blocks[0]]
        self.assertFalse(validator.validate(facts, duplicate, universe).valid)
        ranking_index = next(
            index
            for index, block in enumerate(base_blocks)
            if block.fact_id == "standard-premium.top5"
        )
        omitted = list(base_blocks)
        omitted[ranking_index] = NarrativeBlock(
            fact_id="standard-premium.top5",
            text="- 月度标保达成率 Top 5共5家：新疆（100.5%）、天津（100.1%）、河南（85.2%）、宁波（78.5%）。",
        )
        self.assertFalse(validator.validate(facts, omitted, universe).valid)

    def test_valid_fake_provider_is_used(self):
        agent = AnalysisAgent(ROOT / "templates", deepseek_provider=ValidFakeProvider())
        result = agent.analyze(
            AnalysisRequest(
                template_id="standard-premium",
                dataset=self.dataset,
                provider="deepseek",
            )
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.expression.used_provider, "deepseek")

    def test_invalid_or_failing_provider_falls_back_once(self):
        for provider in [InvalidFakeProvider(), FailingFakeProvider()]:
            with self.subTest(provider=type(provider).__name__):
                agent = AnalysisAgent(ROOT / "templates", deepseek_provider=provider)
                result = agent.analyze(
                    AnalysisRequest(
                        template_id="standard-premium",
                        dataset=self.dataset,
                        provider="deepseek",
                    )
                )
                self.assertEqual(result.status, "completed")
                self.assertEqual(result.expression.used_provider, "deterministic")
                self.assertTrue(result.expression.fallback_reason)

    def test_missing_deepseek_key_is_clear_configuration_failure(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "template_analysis_agent_v4.expression._dotenv_value",
            return_value=None,
        ):
            agent = AnalysisAgent(ROOT / "templates")
            result = agent.analyze(
                AnalysisRequest(
                    template_id="standard-premium",
                    dataset=self.dataset,
                    provider="deepseek",
                )
            )
        self.assertEqual(result.status, "failed")
        self.assertIn("DEEPSEEK_API_KEY", result.errors[0])

    def test_root_dotenv_value_supports_quotes_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "# comment\nexport DEEPSEEK_API_KEY='test-secret'\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _dotenv_value("DEEPSEEK_API_KEY", env_file), "test-secret"
            )


class CliTests(unittest.TestCase):
    def test_cli_writes_only_two_files_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            args = [
                "--templates",
                str(ROOT / "templates"),
                "analyze",
                "--question",
                "分析本月标保",
                "--input",
                str(ROOT / "examples" / "standard-premium.json"),
                "--output",
                str(output),
            ]
            self.assertEqual(cli_run(args), 0)
            self.assertEqual(
                {path.name for path in output.iterdir()}, {"report.md", "run.json"}
            )
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["result"]["status"], "completed")
            self.assertNotIn("dataset", run["request"])
            self.assertEqual(cli_run(args), 1)

    def test_cli_clarification_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            code = cli_run(
                [
                    "--templates",
                    str(ROOT / "templates"),
                    "analyze",
                    "--question",
                    "库存情况",
                    "--input",
                    str(ROOT / "examples" / "standard-premium.json"),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
