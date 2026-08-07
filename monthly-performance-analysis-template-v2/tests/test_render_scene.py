from __future__ import annotations

import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import render_scene as runtime


SCENE_PATH = PROJECT_DIR / "assets" / "scenes" / "standard-premium" / "scene.yaml"
CATALOG_PATH = PROJECT_DIR / "assets" / "metrics" / "standard-premium.yaml"
REPORT_PATH = (
    PROJECT_DIR / "assets" / "reports" / "monthly-performance" / "report.yaml"
)
DATA_PATH = WORKSPACE_DIR / "docs" / "标保.csv"
PARAMS = {
    "data_month_name": "5月",
    "quarter_name": "二季度",
    "cutoff_date": "5月31日",
}
DATASETS = {"standard_premium": DATA_PATH}


class StandardPremiumStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = runtime.load_yaml(SCENE_PATH)
        cls.catalog = runtime.load_yaml(CATALOG_PATH)

    @staticmethod
    def step(context: dict, step_id: str) -> dict:
        return next(item for item in context["steps"] if item["id"] == step_id)

    @staticmethod
    def band(step: dict, band_id: str) -> dict:
        return next(item for item in step["bands"] if item["id"] == band_id)

    def build(self, config: dict | None = None, params: dict | None = None) -> dict:
        return runtime.build_scene_context(
            config or self.config,
            self.catalog,
            DATA_PATH,
            params or PARAMS,
        )

    def render_context(self, context: dict) -> str:
        return runtime.make_environment(SCENE_PATH.parent).get_template(
            self.config["render"]["template"]
        ).render(**context)

    def test_may_steps_match_reference_analysis(self) -> None:
        _, context = runtime.render_scene_file(SCENE_PATH, DATASETS, PARAMS)
        summary = self.step(context, "overall_summary")
        self.assertEqual(
            {item["id"]: item["value"] for item in summary["metrics"]},
            {
                "month_amount": 12444,
                "month_rate": 61.8,
                "month_yoy": -11.6,
                "year_amount": 100538,
                "year_progress": 53.8,
                "year_yoy": 20.6,
            },
        )

        month = self.step(context, "monthly_rate_classification")
        year = self.step(context, "yearly_progress_classification")
        self.assertEqual(
            {item["id"]: item["organizations"] for item in month["bands"]},
            {
                "high": [
                    "河南", "天津", "潍坊", "云南", "新疆", "海南", "内蒙古", "宁波", "甘肃"
                ],
                "target_met": ["天津", "新疆"],
                "low": ["上海", "安徽", "山西", "北京", "贵州", "青岛", "宁夏", "深圳"],
                "very_low": ["深圳"],
            },
        )
        self.assertEqual(
            {item["id"]: item["organizations"] for item in year["bands"]},
            {
                "high": ["天津", "潍坊", "云南", "新疆", "宁波", "甘肃", "贵州"],
                "very_high": ["宁波"],
                "low": ["安徽", "北京", "海南", "青岛", "宁夏", "深圳"],
            },
        )

    def test_may_markdown_is_golden(self) -> None:
        rendered, _ = runtime.render_scene_file(SCENE_PATH, DATASETS, PARAMS)
        expected = """# 标保

- 截至5月31日，全系统5月标保共达成12444万，达成率 61.8%，同比负增11.6%，全年达成100538万，全年进度 53.8%，全年标保同比正增20.6%。
- 9家机构5月标保达成率高于 70%：河南、天津、潍坊、云南、新疆、海南、内蒙古、宁波、甘肃；其中天津、新疆达成5月标保目标；8家机构5月标保达成率低于 50%：上海、安徽、山西、北京、贵州、青岛、宁夏、深圳；其中深圳达成率不足 5%；
- 7家机构全年标保进度达成高于 60%：天津、潍坊、云南、新疆、宁波、甘肃、贵州；其中宁波达成进度超 80%；6家机构全年标保进度达成低于 45%：安徽、北京、海南、青岛、宁夏、深圳。"""
        self.assertEqual(rendered.strip(), expected)

    def test_report_renders_step_signal_at_report_level(self) -> None:
        rendered, context = runtime.render_report_file(
            REPORT_PATH,
            ALL_DATASETS,
            {**PARAMS, "report_month_name": "五月"},
        )
        self.assertIn("# 五月工作总结", rendered)
        self.assertIn("热烈祝贺 天津、新疆达成5月标保目标", rendered)
        self.assertLess(rendered.index("## 贺报"), rendered.index("# 标保"))
        signal = context["scenes"]["standard-premium"]["signals"]["target_met"]
        self.assertEqual(signal["organizations"], ["天津", "新疆"])
        self.assertEqual(signal["step"], "monthly_rate_classification")
        self.assertEqual(signal["band"], "target_met")

    def test_runtime_threshold_overrides_default(self) -> None:
        context = self.build(params={**PARAMS, "monthly_high_threshold": 90})
        high = self.band(self.step(context, "monthly_rate_classification"), "high")
        self.assertEqual(high["threshold"], 90)
        self.assertEqual(high["organizations"], ["天津", "潍坊", "云南", "新疆"])

    def test_remove_classification_step_without_code_or_template_change(self) -> None:
        config = copy.deepcopy(self.config)
        config["steps"] = [
            step
            for step in config["steps"]
            if step["id"] != "yearly_progress_classification"
        ]
        context = self.build(config=config)
        rendered = self.render_context(context)
        self.assertEqual(
            [step["id"] for step in context["steps"]],
            ["overall_summary", "monthly_rate_classification"],
        )
        self.assertNotIn("全年标保进度达成高于", rendered)

    def test_add_candidate_metric_classification_step(self) -> None:
        config = copy.deepcopy(self.config)
        config["inputs"]["parameters"]["yoy_zero_threshold"] = {
            "type": "number",
            "default": 0,
        }
        config["steps"].append(
            {
                "id": "monthly_yoy_classification",
                "type": "classify",
                "metric": "month_yoy",
                "display_order": "source",
                "bands": [
                    {
                        "id": "positive",
                        "operator": "gt",
                        "threshold_param": "yoy_zero_threshold",
                        "presentation": {
                            "style": "threshold_list",
                            "subject": "${data_month_name}标保同比",
                            "comparison": "高于 ",
                        },
                    }
                ],
                "presentation": {"separator": "；", "terminator": "。"},
            }
        )
        context = self.build(config=config)
        positive = self.band(self.step(context, "monthly_yoy_classification"), "positive")
        self.assertEqual(
            positive["organizations"],
            [
                "河南", "山东", "浙江", "四川", "天津", "潍坊", "云南", "吉林",
                "辽宁", "湖南", "新疆", "海南", "宁波", "甘肃", "宁夏",
            ],
        )
        self.assertIn("15家机构5月标保同比高于 0%", self.render_context(context))

    def test_metric_display_orders(self) -> None:
        config = copy.deepcopy(self.config)
        month_step = next(
            step for step in config["steps"] if step["id"] == "monthly_rate_classification"
        )
        month_step["display_order"] = "metric_desc"
        context = self.build(config=config)
        high = self.band(self.step(context, "monthly_rate_classification"), "high")
        self.assertEqual(
            high["organizations"],
            ["新疆", "天津", "潍坊", "云南", "海南", "河南", "内蒙古", "宁波", "甘肃"],
        )

        month_step["display_order"] = "metric_asc"
        context = self.build(config=config)
        high = self.band(self.step(context, "monthly_rate_classification"), "high")
        self.assertEqual(high["organizations"][0], "甘肃")
        self.assertEqual(high["organizations"][-1], "新疆")

    def test_empty_classifications_do_not_render_partial_sentences(self) -> None:
        config = copy.deepcopy(self.config)
        for name, definition in config["inputs"]["parameters"].items():
            if name.endswith("_threshold"):
                definition["default"] = -1000 if "low" in name else 1000
        context = self.build(config=config)
        rendered = self.render_context(context)
        self.assertNotIn("家机构", rendered)
        self.assertNotIn("其中", rendered)
        self.assertEqual(rendered.count("- "), 1)

    def test_missing_dynamic_month_columns_fail(self) -> None:
        with self.assertRaisesRegex(runtime.SceneError, "CSV 缺少必需列"):
            runtime.render_scene_file(
                SCENE_PATH,
                DATASETS,
                {"data_month_name": "6月", "cutoff_date": "6月30日"},
            )

    def test_duplicate_total_row_fails(self) -> None:
        with DATA_PATH.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        rows.append(list(rows[-1]))
        with tempfile.TemporaryDirectory() as temp_dir:
            duplicate_path = Path(temp_dir) / "duplicate.csv"
            with duplicate_path.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerows(rows)
            with self.assertRaisesRegex(runtime.SceneError, "恰好1行，实际为2行"):
                runtime.build_scene_context(
                    self.config, self.catalog, duplicate_path, PARAMS
                )

    def test_invalid_numeric_value_fails(self) -> None:
        with DATA_PATH.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
        month_rate_column = next(
            column for column in fieldnames if column.strip() == "5月达成率"
        )
        rows[0][month_rate_column] = "not-a-number"
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_path = Path(temp_dir) / "invalid.csv"
            with invalid_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(runtime.SceneError, "无法把.*转换为数值"):
                runtime.build_scene_context(
                    self.config, self.catalog, invalid_path, PARAMS
                )

    def test_unknown_step_metric_and_operator_fail_at_runtime(self) -> None:
        config = copy.deepcopy(self.config)
        config["steps"][0]["type"] = "agent_magic"
        with self.assertRaisesRegex(runtime.SceneError, "不支持的分析步骤类型"):
            self.build(config=config)

        config = copy.deepcopy(self.config)
        config["steps"][0]["metrics"][0]["metric"] = "unknown_metric"
        with self.assertRaisesRegex(runtime.SceneError, "指标目录中不存在"):
            self.build(config=config)

        config = copy.deepcopy(self.config)
        config["steps"][1]["bands"][0]["operator"] = "around"
        with self.assertRaisesRegex(runtime.SceneError, "不支持的比较运算符"):
            self.build(config=config)

    def test_context_is_json_serializable(self) -> None:
        json.dumps(self.build(), ensure_ascii=False)


ALL_DATASETS = {
    "standard_premium": WORKSPACE_DIR / "docs" / "标保.csv",
    "value": WORKSPACE_DIR / "docs" / "价值.csv",
    "active_manpower": WORKSPACE_DIR / "docs" / "活动人力.csv",
    "sunshine_manpower": WORKSPACE_DIR / "docs" / "阳光人力.csv",
    "supervisor_activity": WORKSPACE_DIR / "docs" / "主管活动.csv",
    "supervisor_double_star": WORKSPACE_DIR / "docs" / "主管双星.csv",
    "standard_team": WORKSPACE_DIR / "docs" / "标准组.csv",
    "recruitment": WORKSPACE_DIR / "docs" / "新增.csv",
    "co_recruitment": WORKSPACE_DIR / "docs" / "同引.csv",
}
ALL_PARAMS = {
    "report_month_name": "五月",
    "data_month_name": "5月",
    "quarter_name": "二季度",
    "cutoff_date": "5月31日",
}
SCENE_IDS = [
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


class AdditionalSceneTests(unittest.TestCase):
    @staticmethod
    def scene_path(scene_id: str) -> Path:
        return PROJECT_DIR / "assets" / "scenes" / scene_id / "scene.yaml"

    @classmethod
    def render(cls, scene_id: str) -> tuple[str, dict]:
        return runtime.render_scene_file(
            cls.scene_path(scene_id), ALL_DATASETS, ALL_PARAMS
        )

    @staticmethod
    def band(context: dict, step_id: str, band_id: str) -> dict:
        step = next(item for item in context["steps"] if item["id"] == step_id)
        return next(item for item in step["bands"] if item["id"] == band_id)

    def test_all_scene_files_render(self) -> None:
        for scene_id in SCENE_IDS:
            rendered, context = self.render(scene_id)
            self.assertIn(f"# {context['scene']['title']}", rendered)
            json.dumps(context, ensure_ascii=False)

    def test_value_scene_matches_reference_lists(self) -> None:
        rendered, context = self.render("value")
        self.assertIn("7045万", rendered)
        self.assertEqual(
            self.band(context, "month_rate_classification", "high")["organizations"],
            ["河南", "天津", "云南", "吉林", "新疆"],
        )
        self.assertEqual(
            self.band(context, "month_rate_classification", "low")["organizations"],
            ["上海", "山西", "北京", "宁波", "贵州", "青岛", "宁夏", "深圳"],
        )

    def test_manpower_and_supervisor_lists(self) -> None:
        _, active = self.render("active-manpower")
        self.assertEqual(
            self.band(active, "activity_rate_classification", "high")["organizations"],
            ["重庆", "陕西", "吉林", "潍坊", "宁波", "新疆"],
        )
        _, sunshine = self.render("sunshine-manpower")
        self.assertEqual(
            self.band(sunshine, "sunshine_rate_classification", "high")["organizations"],
            ["重庆", "河南", "陕西", "吉林", "潍坊", "湖南", "宁波", "新疆"],
        )
        _, supervisor = self.render("supervisor-activity")
        self.assertEqual(
            self.band(supervisor, "activity_rate_classification", "low")["organizations"],
            ["广东", "上海", "陕西", "山西", "宁夏", "青岛"],
        )
        _, double_star = self.render("supervisor-double-star")
        high = self.band(double_star, "double_star_rate_classification", "high")
        self.assertEqual(high["organizations"], ["浙江", "江苏", "福建", "潍坊", "深圳", "海南"])
        self.assertEqual(high["threshold"], 50)
        self.assertEqual(high["conditions"]["rules"][0]["threshold"], 55)

    def test_standard_team_and_recruitment_compound_rules(self) -> None:
        _, standard_team = self.render("standard-team")
        self.assertEqual(
            self.band(standard_team, "standard_team_classification", "yoy_low")["count"],
            11,
        )
        self.assertEqual(
            self.band(standard_team, "standard_team_classification", "share_low")["count"],
            16,
        )

        rendered, recruitment = self.render("recruitment")
        middle = self.band(recruitment, "recruitment_rate_classification", "middle")
        self.assertEqual(middle["organizations"], ["河北", "河南", "山西", "青岛"])
        self.assertEqual(
            self.band(recruitment, "recruitment_rate_classification", "target_met")["organizations"],
            ["宁波", "深圳"],
        )
        self.assertEqual(
            self.band(recruitment, "bachelor_classification", "target_met")["count"],
            21,
        )
        self.assertEqual(
            self.band(recruitment, "bachelor_classification", "excess")["count"],
            8,
        )
        not_met = self.band(recruitment, "bachelor_target_not_met", "not_met")
        self.assertEqual(not_met["count"], 10)
        self.assertIn("10家机构未达成新增本科占比目标", rendered)

    def test_co_recruitment_compound_rules(self) -> None:
        rendered, context = self.render("co-recruitment")
        target = self.band(context, "quarter_rate_classification", "target_met")
        middle = self.band(context, "quarter_rate_classification", "middle")
        zero = self.band(context, "zero_classification", "both_zero")
        self.assertEqual(target["organizations"], ["河南", "青岛"])
        self.assertEqual(middle["organizations"], ["江苏", "陕西", "甘肃"])
        self.assertEqual(zero["organizations"], ["重庆", "福建", "天津", "贵州", "宁波", "海南", "深圳"])
        self.assertIn("7家机构二季度同引挂0且5月送训挂0", rendered)

    def test_operations_and_formatters_are_enforced(self) -> None:
        value_config = runtime.load_yaml(
            PROJECT_DIR / "assets" / "scenes" / "value" / "scene.yaml"
        )
        value_catalog = runtime.load_yaml(
            PROJECT_DIR / "assets" / "metrics" / "value.yaml"
        )
        invalid = copy.deepcopy(value_config)
        invalid["steps"][1]["metric"] = "month_amount"
        with self.assertRaisesRegex(runtime.SceneError, "不允许用于 classify"):
            runtime.build_scene_context(
                invalid,
                value_catalog,
                ALL_DATASETS["value"],
                ALL_PARAMS,
            )
        self.assertEqual(runtime.format_metric(5531, "person"), "5531人")
        self.assertEqual(runtime.format_metric(-20, "abs_pct"), "20%")

    def test_full_report_order_and_scope(self) -> None:
        rendered, context = runtime.render_report_file(
            REPORT_PATH, ALL_DATASETS, ALL_PARAMS
        )
        self.assertEqual(context["scene_order"], SCENE_IDS)
        self.assertLess(rendered.index("# 标保"), rendered.index("# 价值"))
        self.assertLess(rendered.index("# 价值"), rendered.index("# 同引"))
        self.assertNotIn("公司评价", rendered)
        self.assertIn("# 五月工作总结", rendered)
        self.assertIn("热烈祝贺 天津、新疆达成5月标保目标", rendered)


if __name__ == "__main__":
    unittest.main()
