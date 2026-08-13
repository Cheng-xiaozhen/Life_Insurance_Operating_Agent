from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_DIR.parent
SKILL_DIR = PROJECT_DIR / "skills" / "monthly-performance-analysis"
SCRIPTS_DIR = SKILL_DIR / "scripts"
REPORT_PATH = SKILL_DIR / "references" / "reports" / "monthly-performance.yaml"
DATA_DIR = WORKSPACE_DIR / "docs"
EXPECTED_SCENE_IDS = [
    "standard-premium",
    "value",
    "active-manpower",
    "sunshine-manpower",
    "supervisor-activity",
    "supervisor-double-star",
    "standard-team",
    "recruitment",
    "co-recruitment",
]
DATA_FILES = (
    "标保.csv",
    "价值.csv",
    "活动人力.csv",
    "阳光人力.csv",
    "主管活动.csv",
    "主管双星.csv",
    "标准组.csv",
    "新增.csv",
    "同引.csv",
)
sys.path.insert(0, str(SCRIPTS_DIR))

from monthly_analysis import TemplateExecutionError, execute_report, render_markdown


class MinimalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = execute_report(REPORT_PATH, DATA_DIR)
        cls.scenes = {scene["scene_id"]: scene for scene in cls.result["scenes"]}
        cls.scene = cls.scenes["standard-premium"]
        cls.facts = cls.scene["facts"]
        cls.value_scene = cls.scenes["value"]
        cls.value_facts = cls.value_scene["facts"]

    def test_report_and_summary(self) -> None:
        self.assertEqual("monthly-performance", self.result["report_id"])
        self.assertEqual("五月业绩分析报告", self.result["title"])
        self.assertEqual("standard-premium", self.scene["scene_id"])

        overall = self.facts["overall"]
        expected = {
            "month_amount": 12444,
            "month_rate": 61.8,
            "month_yoy": -11.6,
            "year_amount": 100538,
            "year_progress": 53.8,
            "year_yoy": 20.6,
        }
        self.assertEqual(expected, {key: cell["value"] for key, cell in overall.items()})
        self.assertEqual("61.8%", overall["month_rate"]["display"])

    def test_value_summary(self) -> None:
        self.assertEqual(
            EXPECTED_SCENE_IDS,
            [scene["scene_id"] for scene in self.result["scenes"]],
        )
        self.assertEqual("价值", self.value_scene["title"])
        self.assertEqual(
            {"overall", "high_month_rate", "low_month_rate"},
            set(self.value_facts),
        )

        overall = self.value_facts["overall"]
        expected = {
            "month_amount": 7045,
            "month_rate": 74.1,
            "month_yoy": 9.8,
            "year_progress": 54.2,
            "year_yoy": 7.2,
        }
        self.assertEqual(expected, {key: cell["value"] for key, cell in overall.items()})
        self.assertNotIn("year_amount", overall)
        self.assertEqual("74.1%", overall["month_rate"]["display"])

    def test_manpower_summaries_and_selections(self) -> None:
        active = self.scenes["active-manpower"]
        active_facts = active["facts"]
        self.assertEqual("活动人力", active["title"])
        self.assertEqual(
            {"activity_count": 5531, "activity_rate": 77.9, "activity_yoy": -9.4},
            {
                key: cell["value"]
                for key, cell in active_facts["overall"].items()
            },
        )
        self.assertEqual("5531", active_facts["overall"]["activity_count"]["display"])
        self.assertEqual(
            ["宁波", "陕西", "吉林", "重庆", "潍坊", "新疆"],
            [row["organization"] for row in active_facts["high_activity_rate"]],
        )
        self.assertEqual(
            ["宁波"],
            [row["organization"] for row in active_facts["target_met"]],
        )
        self.assertEqual(
            ["上海", "深圳", "北京", "贵州", "内蒙古", "安徽"],
            [row["organization"] for row in active_facts["low_activity_rate"]],
        )

        sunshine = self.scenes["sunshine-manpower"]
        sunshine_facts = sunshine["facts"]
        self.assertEqual("阳光人力", sunshine["title"])
        self.assertEqual(
            {
                "sunshine_count": 4772,
                "sunshine_rate": 82.2,
                "sunshine_yoy": -2.1,
                "sunshine_share": 85.2,
            },
            {
                key: cell["value"]
                for key, cell in sunshine_facts["overall"].items()
            },
        )
        self.assertEqual(
            ["重庆", "吉林", "潍坊", "湖南", "新疆", "陕西", "河南", "宁波"],
            [row["organization"] for row in sunshine_facts["high_sunshine_rate"]],
        )
        self.assertEqual(
            ["上海", "北京", "贵州", "内蒙古", "安徽", "深圳", "黑龙江"],
            [row["organization"] for row in sunshine_facts["low_sunshine_rate"]],
        )

    def test_supervisor_summaries_and_selections(self) -> None:
        activity = self.scenes["supervisor-activity"]["facts"]
        self.assertEqual(
            {
                "in_service": 3484,
                "active_count": 2485,
                "activity_rate": 71.3,
                "activity_yoy": -1.1,
            },
            {key: cell["value"] for key, cell in activity["overall"].items()},
        )
        self.assertEqual("-1.1pt", activity["overall"]["activity_yoy"]["display"])
        self.assertEqual(
            ["深圳", "重庆", "浙江", "江苏", "潍坊"],
            [row["organization"] for row in activity["high_activity_rate"]],
        )
        self.assertEqual(
            ["上海", "青岛", "山西", "陕西", "宁夏", "广东"],
            [row["organization"] for row in activity["low_activity_rate"]],
        )

        double_star = self.scenes["supervisor-double-star"]["facts"]
        self.assertEqual(
            {"double_star_count": 1300, "double_star_rate": 37.3, "double_star_yoy": -4.3},
            {key: cell["value"] for key, cell in double_star["overall"].items()},
        )
        self.assertEqual(
            ["深圳", "海南", "浙江", "福建", "潍坊", "江苏", "吉林"],
            [row["organization"] for row in double_star["high_double_star_rate"]],
        )
        self.assertEqual(
            ["青岛", "宁夏", "甘肃", "陕西", "贵州", "山西"],
            [row["organization"] for row in double_star["low_double_star_rate"]],
        )

    def test_standard_team_summary_and_selections(self) -> None:
        facts = self.scenes["standard-team"]["facts"]
        self.assertEqual(
            {
                "in_service": 3484,
                "standard_count": 1175,
                "standard_yoy": -4.3,
                "standard_share": 33.9,
            },
            {key: cell["value"] for key, cell in facts["overall"].items()},
        )
        self.assertEqual(
            ["深圳", "上海", "贵州", "北京", "海南", "宁波", "广东", "安徽", "山西", "内蒙古", "黑龙江"],
            [row["organization"] for row in facts["low_standard_yoy"]],
        )
        self.assertEqual(
            ["深圳", "贵州", "上海", "青岛", "陕西", "山西", "广东", "黑龙江", "北京", "海南", "宁波", "宁夏", "安徽", "甘肃", "山东", "新疆"],
            [row["organization"] for row in facts["low_standard_share"]],
        )

    def test_recruitment_summary_and_selections(self) -> None:
        facts = self.scenes["recruitment"]["facts"]
        self.assertEqual(
            {
                "training_count": 1510,
                "added_count": 739,
                "added_rate": 62.6,
                "added_yoy": -7.4,
                "first_sun_count": 425,
                "first_sun_rate": 45.6,
                "college_share": 47.5,
                "college_yoy": 16.3,
                "bachelor_share": 25.4,
                "bachelor_gap": 5.4,
            },
            {key: cell["value"] for key, cell in facts["overall"].items()},
        )
        expected = {
            "high_added_rate": ["宁波", "深圳", "山西", "河北", "河南", "青岛"],
            "target_met": ["宁波", "深圳"],
            "low_added_rate": ["贵州", "海南", "宁夏", "北京", "黑龙江", "广东", "天津", "吉林", "新疆"],
            "bachelor_excess": ["深圳", "内蒙古", "四川", "福建", "陕西", "江苏", "上海", "甘肃"],
            "bachelor_target_not_met": ["宁波", "青岛", "宁夏", "广东", "浙江", "重庆", "天津", "河南"],
            "bachelor_gap_missing": ["贵州", "海南"],
        }
        for fact_id, organizations in expected.items():
            self.assertEqual(
                organizations,
                [row["organization"] for row in facts[fact_id]],
                fact_id,
            )
        self.assertEqual(21, len(facts["bachelor_target_met"]))

    def test_co_recruitment_summary_and_selections(self) -> None:
        facts = self.scenes["co-recruitment"]["facts"]
        self.assertEqual(
            {
                "month_training_count": 111,
                "month_training_yoy": -14,
                "quarter_target": 275,
                "quarter_achieved": 105,
                "quarter_rate": 38.2,
                "quarter_yoy": 47.9,
                "quarter_college_share": 41,
                "quarter_college_yoy": 0.1,
                "quarter_bachelor_share": 16.2,
                "quarter_bachelor_yoy": 0.7,
            },
            {key: cell["value"] for key, cell in facts["overall"].items()},
        )
        self.assertEqual(
            ["青岛", "河南"],
            [row["organization"] for row in facts["target_met"]],
        )
        self.assertEqual(
            ["青岛", "河南", "甘肃", "陕西", "江苏"],
            [row["organization"] for row in facts["high_quarter_rate"]],
        )
        self.assertEqual(
            ["上海", "重庆", "福建", "吉林", "天津", "贵州", "宁波", "海南", "深圳"],
            [row["organization"] for row in facts["month_training_zero"]],
        )
        self.assertEqual(
            ["重庆", "福建", "北京", "山西", "安徽", "天津", "贵州", "宁波", "海南", "深圳"],
            [row["organization"] for row in facts["quarter_achieved_zero"]],
        )

    def test_value_selections_and_order(self) -> None:
        self.assertEqual(
            ["河南", "天津", "云南", "吉林", "新疆"],
            [row["organization"] for row in self.value_facts["high_month_rate"]],
        )
        self.assertEqual(
            ["深圳", "上海", "北京", "宁夏", "青岛", "山西", "贵州", "宁波"],
            [row["organization"] for row in self.value_facts["low_month_rate"]],
        )

    def test_default_selections_and_order(self) -> None:
        expected = {
            "high_month_rate": ["新疆", "天津", "潍坊", "云南", "海南", "河南", "内蒙古", "宁波", "甘肃"],
            "low_month_rate": ["深圳", "北京", "宁夏", "青岛", "上海", "山西", "安徽", "贵州"],
            "ahead_year_progress": ["宁波", "新疆", "贵州", "潍坊", "天津", "甘肃", "云南"],
            "behind_year_progress": ["深圳", "北京", "青岛", "宁夏", "海南", "安徽"],
        }
        for fact_id, organizations in expected.items():
            self.assertEqual(
                organizations,
                [row["organization"] for row in self.facts[fact_id]],
                fact_id,
            )

    def test_report_section_can_override_threshold_and_preserve_strict_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            references = Path(temp_dir) / "references"
            shutil.copytree(SKILL_DIR / "references", references)
            report = references / "reports" / "monthly-performance.yaml"
            text = report.read_text(encoding="utf-8")
            text = text.replace(
                "  - scene: standard-premium\n",
                "  - scene: standard-premium\n    parameters:\n      high_rate: 78.7\n",
            )
            report.write_text(text, encoding="utf-8")
            result = execute_report(report, DATA_DIR)
        organizations = [
            row["organization"]
            for row in result["scenes"][0]["facts"]["high_month_rate"]
        ]
        self.assertEqual(["新疆", "天津", "潍坊", "云南", "海南"], organizations)

    def test_active_manpower_high_rate_uses_strict_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            references = Path(temp_dir) / "references"
            shutil.copytree(SKILL_DIR / "references", references)
            report = references / "reports" / "monthly-performance.yaml"
            text = report.read_text(encoding="utf-8")
            text = text.replace(
                "  - scene: active-manpower\n",
                "  - scene: active-manpower\n    parameters:\n      high_rate: 90.4\n",
            )
            report.write_text(text, encoding="utf-8")
            result = execute_report(report, DATA_DIR)

        scenes = {scene["scene_id"]: scene for scene in result["scenes"]}
        organizations = [
            row["organization"]
            for row in scenes["active-manpower"]["facts"]["high_activity_rate"]
        ]
        self.assertEqual(["宁波", "陕西", "吉林", "重庆", "潍坊"], organizations)

    def test_total_row_is_excluded_and_equal_values_keep_source_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            references = Path(temp_dir) / "references"
            shutil.copytree(SKILL_DIR / "references", references)
            report = references / "reports" / "monthly-performance.yaml"
            text = report.read_text(encoding="utf-8").replace(
                "  - scene: standard-premium\n",
                "  - scene: standard-premium\n    parameters:\n      high_rate: 60\n",
            )
            report.write_text(text, encoding="utf-8")
            result = execute_report(report, DATA_DIR)

        rows = result["scenes"][0]["facts"]["high_month_rate"]
        organizations = [row["organization"] for row in rows]
        self.assertEqual(14, len(organizations))
        self.assertNotIn("", organizations)
        self.assertLess(organizations.index("广东"), organizations.index("辽宁"))

    def test_month_and_quarter_column_bindings_are_parameterized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_data_dir = Path(temp_dir)
            for file_name in DATA_FILES:
                source = (DATA_DIR / file_name).read_text(encoding="utf-8")
                first_line, remainder = source.split("\n", 1)
                if file_name in {"标保.csv", "价值.csv", "同引.csv"}:
                    first_line = first_line.replace("5月", "6月")
                if file_name == "同引.csv":
                    first_line = first_line.replace("二季度", "三季度")
                (temp_data_dir / file_name).write_text(
                    first_line + "\n" + remainder,
                    encoding="utf-8",
                )
            result = execute_report(
                REPORT_PATH,
                temp_data_dir,
                {
                    "report_month": "六月",
                    "month_label": "6月",
                    "quarter_label": "三季度",
                },
            )
        self.assertEqual("六月业绩分析报告", result["title"])
        scenes = {scene["scene_id"]: scene for scene in result["scenes"]}
        self.assertEqual(61.8, scenes["standard-premium"]["facts"]["overall"]["month_rate"]["value"])
        self.assertEqual(74.1, scenes["value"]["facts"]["overall"]["month_rate"]["value"])
        self.assertEqual(111, scenes["co-recruitment"]["facts"]["overall"]["month_training_count"]["value"])

    def test_missing_file_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(TemplateExecutionError, "CSV file not found"):
                execute_report(REPORT_PATH, temp_dir)

    def test_missing_parameterized_column_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(TemplateExecutionError, "6月达成"):
            execute_report(REPORT_PATH, DATA_DIR, {"month_label": "6月"})

    def test_missing_quarter_column_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(TemplateExecutionError, "三季度同引"):
            execute_report(REPORT_PATH, DATA_DIR, {"quarter_label": "三季度"})

    def test_unknown_scene_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            references = Path(temp_dir) / "references"
            shutil.copytree(SKILL_DIR / "references", references)
            report = references / "reports" / "monthly-performance.yaml"
            report.write_text(
                report.read_text(encoding="utf-8").replace(
                    "scene: standard-premium", "scene: unknown-scene"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TemplateExecutionError, "Scene 'unknown-scene' not found"):
                execute_report(report, DATA_DIR)

    def test_markdown_contains_deterministic_facts(self) -> None:
        markdown = render_markdown(self.result)
        self.assertIn("# 五月业绩分析报告", markdown)
        self.assertIn("## 标保", markdown)
        self.assertIn("月度标保达成 12444万", markdown)
        self.assertIn("月度标保达成率高于 70% 的机构", markdown)
        self.assertNotIn("$70%", markdown)
        self.assertIn("新疆（176.3%）", markdown)
        self.assertIn("## 价值", markdown)
        self.assertIn("月度价值达成 7045万", markdown)
        self.assertIn("月度价值达成率高于 85% 的机构", markdown)
        self.assertIn("## 活动人力", markdown)
        self.assertIn("活动人力 5531人", markdown)
        self.assertIn("宁波（100.0%）", markdown)
        self.assertIn("## 阳光人力", markdown)
        self.assertIn("阳光人力达成 4772人", markdown)
        self.assertIn("阳光人力达成率不低于 90% 的机构", markdown)
        self.assertIn("## 主管活动", markdown)
        self.assertIn("主管活动人数 2485人", markdown)
        self.assertIn("## 主管双星", markdown)
        self.assertIn("主管双星人数 1300人", markdown)
        self.assertIn("## 标准组", markdown)
        self.assertIn("标准组达成 1175个", markdown)
        self.assertIn("## 新增", markdown)
        self.assertIn("新增送训 1510人", markdown)
        self.assertIn("## 同引", markdown)
        self.assertIn("月度同引送训 111人", markdown)
        headings = [
            markdown.index(f"## {self.scenes[scene_id]['title']}")
            for scene_id in EXPECTED_SCENE_IDS
        ]
        self.assertEqual(sorted(headings), headings)

    def test_markdown_uses_report_heading_level(self) -> None:
        result = dict(self.result)
        result["output"] = {"format": "markdown", "heading_level": 2}
        markdown = render_markdown(result)
        self.assertTrue(markdown.startswith("## 五月业绩分析报告\n"))
        self.assertIn("### 标保", markdown)


if __name__ == "__main__":
    unittest.main()
