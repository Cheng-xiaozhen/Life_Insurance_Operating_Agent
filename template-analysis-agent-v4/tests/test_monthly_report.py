from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cli import run as cli_run  # noqa: E402
from src.data import CsvDatasetLoader, load_csv_profiles  # noqa: E402
from src.models import (  # noqa: E402
    DataError,
    ExpressionError,
    MonthlyReportRequest,
    NarrativeBlock,
)
from src.monthly import MonthlyReportAgent  # noqa: E402


CONTEXT = {
    "report_month_name": "五月",
    "data_month_name": "5月",
    "quarter_name": "二季度",
    "cutoff_date": "5月31日",
}


class GroupedFakeProvider:
    def express(self, title, facts, groups, context):
        del title, context
        fact_index = {fact.fact_id: fact for fact in facts}
        blocks = []
        for group in groups:
            parts = []
            for fact_id in group.fact_ids:
                fact = fact_index[fact_id]
                if fact.kind == "summary":
                    parts.append(f"{fact.title}：{fact.display_value}。")
                    continue
                organizations = "、".join(
                    item.organization for item in fact.items
                )
                suffix = f"：{organizations}" if organizations else ""
                parts.append(
                    f"{fact.title}（{fact.rule_text}）共{fact.count}家{suffix}。"
                )
            blocks.append(
                NarrativeBlock(
                    group_id=group.id,
                    fact_ids=group.fact_ids,
                    text="- " + " ".join(parts),
                )
            )
        return blocks


class FailingThirdProvider(GroupedFakeProvider):
    def __init__(self):
        self.calls = 0

    def express(self, title, facts, groups, context):
        self.calls += 1
        if self.calls == 3:
            raise ExpressionError("第三个场景模拟失败")
        return super().express(title, facts, groups, context)


class InvalidGroupedFakeProvider(GroupedFakeProvider):
    def express(self, title, facts, groups, context):
        blocks = super().express(title, facts, groups, context)
        blocks[0] = NarrativeBlock(
            group_id=blocks[0].group_id,
            fact_ids=blocks[0].fact_ids,
            text=blocks[0].text + "建议使用999。",
        )
        return blocks


def request(data_dir: Path | None = None) -> MonthlyReportRequest:
    return MonthlyReportRequest(
        data_dir=str(data_dir or (REPOSITORY_ROOT / "docs")),
        **CONTEXT,
    )


class CsvProfileTests(unittest.TestCase):
    def test_nine_profiles_load_and_dynamic_column_is_required(self):
        profiles = load_csv_profiles(ROOT / "profiles" / "monthly-performance.yaml")
        self.assertEqual(len(profiles), 9)
        loader = CsvDatasetLoader(profiles)
        dataset, source = loader.load(
            "standard-premium",
            REPOSITORY_ROOT / "docs",
            CONTEXT,
        )
        self.assertEqual(dataset.summary["month_rate"], 61.8)
        self.assertGreater(len(dataset.rows), 20)
        self.assertEqual(source.scene_id, "standard-premium")
        with self.assertRaisesRegex(DataError, "缺少必需列"):
            loader.load(
                "standard-premium",
                REPOSITORY_ROOT / "docs",
                {**CONTEXT, "data_month_name": "6月"},
            )

    def test_missing_data_directory_is_rejected(self):
        profiles = load_csv_profiles(ROOT / "profiles" / "monthly-performance.yaml")
        with self.assertRaisesRegex(DataError, "找不到 CSV"):
            CsvDatasetLoader(profiles).load(
                "value",
                ROOT / "does-not-exist",
                CONTEXT,
            )

    def test_invalid_numbers_total_rows_and_empty_details_are_rejected(self):
        profile = {
            "sample": {
                "filename": "sample.csv",
                "encoding": "utf-8-sig",
                "row_label_column": "分组",
                "organization_column": "机构",
                "total_value": "全系统",
                "metrics": {"value": "指标"},
            }
        }
        cases = [
            (
                "分组,机构,指标\n全系统,-,1\n全系统,-,2\n机构,A,3\n",
                "汇总行必须恰好1行",
            ),
            (
                "分组,机构,指标\n全系统,-,1\n",
                "没有机构明细",
            ),
            (
                "分组,机构,指标\n全系统,-,1\n机构,A,abc\n",
                "不是合法数值",
            ),
        ]
        for content, error in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                Path(directory, "sample.csv").write_text(
                    content,
                    encoding="utf-8-sig",
                )
                with self.assertRaisesRegex(DataError, error):
                    CsvDatasetLoader(profile).load("sample", directory, CONTEXT)


class MonthlyReportRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = MonthlyReportAgent(
            ROOT,
            expression_provider=GroupedFakeProvider(),
        ).analyze(request())

    def test_complete_report_and_scene_order(self):
        self.assertEqual(self.result.status, "completed", self.result.errors)
        self.assertEqual(len(self.result.sources), 9)
        self.assertEqual(len(self.result.scene_results), 9)
        self.assertEqual(
            [result.template_id for result in self.result.scene_results],
            [
                "standard-premium",
                "value",
                "active-manpower",
                "sunshine-manpower",
                "supervisor-activity",
                "supervisor-double-star",
                "standard-team",
                "recruitment",
                "co-recruitment",
            ],
        )
        report = self.result.report_markdown
        self.assertIn("# 五月业绩分析报告", report)
        self.assertIn("## 贺报", report)
        self.assertIn("天津、新疆", report)
        self.assertEqual(report.count("\n## "), 10)

    def test_facts_are_exactly_equivalent_to_v2_context(self):
        context_path = (
            REPOSITORY_ROOT
            / "monthly-performance-analysis-template-v2"
            / "output"
            / "五月业绩分析报告.context.json"
        )
        v2_scenes = json.loads(
            context_path.read_text(encoding="utf-8")
        )["scenes"]
        results = {
            str(result.template_id): result
            for result in self.result.scene_results
        }
        compared = 0
        for scene_id, v2_scene in v2_scenes.items():
            facts = {fact.fact_id: fact for fact in results[scene_id].facts}
            for step in v2_scene["steps"]:
                if step["type"] == "summarize":
                    for metric in step["metrics"]:
                        fact_id = f"{scene_id}.{step['id']}.{metric['id']}"
                        self.assertEqual(
                            facts[fact_id].raw_value,
                            metric["value"],
                            fact_id,
                        )
                        compared += 1
                    continue
                for band in step["bands"]:
                    fact_id = f"{scene_id}.{step['id']}.{band['id']}"
                    fact = facts[fact_id]
                    self.assertEqual(
                        [item.organization for item in fact.items],
                        band["organizations"],
                        fact_id,
                    )
                    self.assertEqual(fact.count, band["count"], fact_id)
                    expected_rules = band.get("conditions", {}).get("rules", [])
                    actual_rules = (fact.rule or {}).get("rules", [])
                    self.assertEqual(len(actual_rules), len(expected_rules), fact_id)
                    for expected, actual in zip(expected_rules, actual_rules):
                        self.assertEqual(actual["metric"], expected["metric"], fact_id)
                        self.assertEqual(actual["operator"], expected["operator"], fact_id)
                        self.assertEqual(actual["threshold"], expected["threshold"], fact_id)
                    compared += 1
        self.assertEqual(compared, 73)
        self.assertEqual(
            sum(len(result.facts) for result in self.result.scene_results),
            73,
        )

    def test_provider_failure_aborts_the_complete_report(self):
        provider = FailingThirdProvider()
        result = MonthlyReportAgent(
            ROOT,
            expression_provider=provider,
        ).analyze(request())
        self.assertEqual(result.status, "failed")
        self.assertEqual(provider.calls, 3)
        self.assertEqual(len(result.scene_results), 3)
        self.assertFalse(result.report_markdown)

    def test_invalid_expression_can_be_non_blocking(self):
        result = MonthlyReportAgent(
            ROOT,
            expression_provider=InvalidGroupedFakeProvider(),
            allow_invalid_expression=True,
        ).analyze(request())
        self.assertEqual(result.status, "completed", result.errors)
        self.assertEqual(len(result.scene_results), 9)
        for scene_result in result.scene_results:
            self.assertIsNotNone(scene_result.validation)
            self.assertFalse(scene_result.validation.valid)
            self.assertTrue(scene_result.validation.errors)


class MonthlyReportCliTests(unittest.TestCase):
    def test_cli_writes_report_run_json_and_sibling_log(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "monthly"
            args = [
                "monthly-report",
                "--data-dir",
                str(REPOSITORY_ROOT / "docs"),
                "--report-month-name",
                CONTEXT["report_month_name"],
                "--data-month-name",
                CONTEXT["data_month_name"],
                "--quarter-name",
                CONTEXT["quarter_name"],
                "--cutoff-date",
                CONTEXT["cutoff_date"],
                "--output",
                str(output),
            ]
            with patch(
                "src.monthly.DeepSeekExpressionProvider",
                return_value=GroupedFakeProvider(),
            ):
                self.assertEqual(cli_run(args), 0)
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {"report.md", "run.json"},
            )
            self.assertTrue(output.with_name("monthly.log").is_file())
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["schema_version"], "2.0")
            self.assertEqual(len(run["result"]["scene_results"]), 9)
            self.assertNotIn("dataset", json.dumps(run, ensure_ascii=False))

    def test_cli_failure_keeps_only_the_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "monthly"
            log_file = output.with_name("monthly.log")
            log_file.write_text("stale log\n", encoding="utf-8")
            code = cli_run(
                [
                    "monthly-report",
                    "--data-dir",
                    str(root / "empty"),
                    "--report-month-name",
                    CONTEXT["report_month_name"],
                    "--data-month-name",
                    CONTEXT["data_month_name"],
                    "--quarter-name",
                    CONTEXT["quarter_name"],
                    "--cutoff-date",
                    CONTEXT["cutoff_date"],
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 1)
            self.assertFalse(output.exists())
            self.assertTrue(log_file.is_file())
            self.assertNotIn("stale log", log_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
