from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import (  # noqa: E402
    AnalysisAgent,
    AnalysisDataset,
    AnalysisRequest,
    OrganizationRow,
)
from src.cli import run as cli_run  # noqa: E402
from src.expression import (  # noqa: E402
    NarrativeValidator,
    _dotenv_value,
)
from src.models import (  # noqa: E402
    ExpressionError,
    NarrativeBlock,
)
from src.templates import load_templates  # noqa: E402


def load_example(name: str) -> AnalysisDataset:
    raw = json.loads(
        (ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8")
    )
    return AnalysisDataset(
        summary=raw["summary"],
        rows=[OrganizationRow(**row) for row in raw["rows"]],
    )


class ValidFakeProvider:
    def express(self, title, facts, groups, context):
        del title, context
        fact_index = {fact.fact_id: fact for fact in facts}
        blocks = []
        for group in groups:
            parts = []
            for fact_id in group.fact_ids:
                fact = fact_index[fact_id]
                if fact.kind == "summary":
                    text = f"{fact.title}：{fact.display_value}。"
                elif fact.kind == "ranking":
                    items = "、".join(
                        f"{item.organization}（{item.display_value}）"
                        for item in fact.items
                    )
                    text = f"{fact.title}共{fact.count}家：{items}。"
                elif fact.items:
                    organizations = "、".join(
                        item.organization for item in fact.items
                    )
                    text = (
                        f"{fact.title}（{fact.rule_text}）共{fact.count}家："
                        f"{organizations}。"
                    )
                else:
                    text = f"{fact.title}（{fact.rule_text}）共0家。"
                parts.append(text)
            blocks.append(
                NarrativeBlock(
                    group_id=group.id,
                    fact_ids=group.fact_ids,
                    text="- " + " ".join(parts),
                )
            )
        return blocks


class InvalidFakeProvider:
    def express(self, title, facts, groups, context):
        blocks = ValidFakeProvider().express(title, facts, groups, context)
        blocks[0] = NarrativeBlock(
            group_id=blocks[0].group_id,
            fact_ids=blocks[0].fact_ids,
            text=blocks[0].text + "预计增长999%。",
        )
        return blocks


class FailingFakeProvider:
    def express(self, title, facts, groups, context):
        raise ExpressionError("模拟模型故障")


class TemplateTests(unittest.TestCase):
    def test_nine_templates_load(self):
        templates = load_templates(ROOT / "templates")
        self.assertEqual(len(templates), 9)
        self.assertIn("standard-premium", templates)
        self.assertIn("co-recruitment", templates)

    def test_all_scene_keywords_route_uniquely(self):
        agent = AnalysisAgent(ROOT / "templates")
        dataset = AnalysisDataset(summary={}, rows=[])
        cases = {
            "标保": "standard-premium",
            "价值": "value",
            "活动人力": "active-manpower",
            "阳光人力": "sunshine-manpower",
            "主管活动": "supervisor-activity",
            "主管双星": "supervisor-double-star",
            "标准组": "standard-team",
            "新增": "recruitment",
            "同引": "co-recruitment",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                routing = agent._route(
                    AnalysisRequest(question=question, dataset=dataset)
                )
                self.assertEqual(routing.selected_template_id, expected)


class RoutingAndExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = AnalysisAgent(
            ROOT / "templates", expression_provider=ValidFakeProvider()
        )
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

    def test_standard_premium_historical_overlapping_bands(self):
        result = self.agent.analyze(
            AnalysisRequest(question="分析标保", dataset=self.premium)
        )
        self.assertEqual(result.status, "completed")
        facts = {fact.fact_id: fact for fact in result.facts}
        target = facts["standard-premium.monthly_rate_classification.target_met"]
        high = facts["standard-premium.monthly_rate_classification.high"]
        severe = facts["standard-premium.monthly_rate_classification.very_low"]
        low = facts["standard-premium.monthly_rate_classification.low"]
        groups = [
            {item.organization for item in fact.items}
            for fact in [target, high, severe, low]
        ]
        self.assertEqual(groups[0], {"天津", "新疆"})
        self.assertEqual(groups[1], {"天津", "新疆", "河南", "宁波"})
        self.assertEqual(groups[2], {"深圳"})
        self.assertEqual(groups[3], {"上海", "深圳", "安徽", "北京"})
        self.assertLessEqual(groups[0], groups[1])
        self.assertLessEqual(groups[2], groups[3])

    def test_recruitment_compound_and_missing_rules(self):
        result = self.agent.analyze(
            AnalysisRequest(question="分析新增人力", dataset=self.recruitment)
        )
        self.assertEqual(result.status, "completed")
        facts = {fact.fact_id: fact for fact in result.facts}
        middle = facts["recruitment.recruitment_rate_classification.middle"]
        not_met = facts["recruitment.bachelor_target_not_met.not_met"]
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
            question="分析标保", dataset=self.premium
        )
        first = self.agent.analyze(request)
        second = self.agent.analyze(request)
        self.assertEqual(first.to_dict(), second.to_dict())


class ExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_example("standard-premium")
        cls.base = AnalysisAgent(
            ROOT / "templates", expression_provider=ValidFakeProvider()
        ).analyze(
            AnalysisRequest(question="分析标保", dataset=cls.dataset)
        )

    def test_validator_rejects_unknown_number_organization_duplicate_and_advice(self):
        facts = self.base.facts
        universe = [row.organization for row in self.dataset.rows]
        validator = NarrativeValidator()
        base_blocks = self.base.blocks
        agent = AnalysisAgent(ROOT / "templates")
        groups = agent._resolve_narrative_groups(
            agent.templates["standard-premium"], facts
        )
        cases = [
            NarrativeBlock(group_id=base_blocks[0].group_id, fact_ids=base_blocks[0].fact_ids, text="- 月度标保达成：999万。"),
            NarrativeBlock(group_id=base_blocks[0].group_id, fact_ids=base_blocks[0].fact_ids, text="- 月度标保达成：12444万，河南表现。"),
            NarrativeBlock(group_id=base_blocks[0].group_id, fact_ids=base_blocks[0].fact_ids, text="- 月度标保达成：12444万，建议优化。"),
        ]
        for block in cases:
            with self.subTest(text=block.text):
                candidate = [block, *base_blocks[1:]]
                self.assertFalse(
                    validator.validate(facts, candidate, universe, groups, {}).valid
                )
        duplicate = [*base_blocks, base_blocks[0]]
        self.assertFalse(
            validator.validate(facts, duplicate, universe, groups, {}).valid
        )
        omitted = list(base_blocks)
        omitted[1] = NarrativeBlock(
            group_id=base_blocks[1].group_id,
            fact_ids=base_blocks[1].fact_ids[:-1],
            text=base_blocks[1].text,
        )
        self.assertFalse(
            validator.validate(facts, omitted, universe, groups, {}).valid
        )

    def test_valid_fake_provider_is_used(self):
        agent = AnalysisAgent(
            ROOT / "templates", expression_provider=ValidFakeProvider()
        )
        result = agent.analyze(
            AnalysisRequest(
                question="分析标保",
                dataset=self.dataset,
            )
        )
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.report_markdown)

    def test_invalid_or_failing_provider_returns_failed(self):
        for provider in [InvalidFakeProvider(), FailingFakeProvider()]:
            with self.subTest(provider=type(provider).__name__):
                agent = AnalysisAgent(
                    ROOT / "templates", expression_provider=provider
                )
                result = agent.analyze(
                    AnalysisRequest(
                        question="分析标保",
                        dataset=self.dataset,
                    )
                )
                self.assertEqual(result.status, "failed")
                self.assertTrue(result.errors)

    def test_missing_deepseek_key_is_clear_configuration_failure(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "src.expression._dotenv_value",
            return_value=None,
        ):
            agent = AnalysisAgent(ROOT / "templates")
            result = agent.analyze(
                AnalysisRequest(
                    question="分析标保",
                    dataset=self.dataset,
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
            with patch(
                "src.engine.DeepSeekExpressionProvider",
                return_value=ValidFakeProvider(),
            ):
                self.assertEqual(cli_run(args), 0)
                self.assertEqual(
                    {path.name for path in output.iterdir()}, {"report.md", "run.json"}
                )
                log_file = output.with_name(output.name + ".log")
                self.assertTrue(log_file.is_file())
                self.assertIn(
                    "agent.analyze() 输入",
                    log_file.read_text(encoding="utf-8"),
                )
                run = json.loads((output / "run.json").read_text(encoding="utf-8"))
                self.assertEqual(run["result"]["status"], "completed")
                self.assertNotIn("dataset", run["request"])
                self.assertNotIn("template_id", run["request"])
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
            self.assertTrue(output.with_name(output.name + ".log").is_file())


if __name__ == "__main__":
    unittest.main()
