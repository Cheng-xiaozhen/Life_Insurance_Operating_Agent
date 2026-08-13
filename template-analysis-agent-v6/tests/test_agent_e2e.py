from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from standalone_agent import chat, initialize_agent


class AgentEndToEndTests(unittest.TestCase):
    def test_offline_full_closure(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as temp_dir:
            agent = initialize_agent(offline=True, runs_dir=Path(temp_dir))
            result = chat(
                agent,
                "请基于当前数据生成2026年5月业绩分析报告",
                data_context_id="docs",
            )
            self.assertEqual("completed", result.status)
            facts = json.loads(Path(result.facts_path).read_text(encoding="utf-8"))
            scenes = {scene["scene_id"]: scene["facts"] for scene in facts["scenes"]}
            standard = scenes["standard-premium"]
            value = scenes["value"]
            active = scenes["active-manpower"]
            sunshine = scenes["sunshine-manpower"]
            supervisor_activity = scenes["supervisor-activity"]
            supervisor_double_star = scenes["supervisor-double-star"]
            standard_team = scenes["standard-team"]
            recruitment = scenes["recruitment"]
            co_recruitment = scenes["co-recruitment"]
            self.assertEqual(9, len(standard["high_month_rate"]))
            self.assertEqual(8, len(standard["low_month_rate"]))
            self.assertEqual(5, len(value["high_month_rate"]))
            self.assertEqual(8, len(value["low_month_rate"]))
            self.assertEqual(6, len(active["high_activity_rate"]))
            self.assertEqual(1, len(active["target_met"]))
            self.assertEqual(6, len(active["low_activity_rate"]))
            self.assertEqual(8, len(sunshine["high_sunshine_rate"]))
            self.assertEqual(7, len(sunshine["low_sunshine_rate"]))
            self.assertEqual(5, len(supervisor_activity["high_activity_rate"]))
            self.assertEqual(6, len(supervisor_activity["low_activity_rate"]))
            self.assertEqual(7, len(supervisor_double_star["high_double_star_rate"]))
            self.assertEqual(6, len(supervisor_double_star["low_double_star_rate"]))
            self.assertEqual(11, len(standard_team["low_standard_yoy"]))
            self.assertEqual(16, len(standard_team["low_standard_share"]))
            self.assertEqual(6, len(recruitment["high_added_rate"]))
            self.assertEqual(2, len(recruitment["target_met"]))
            self.assertEqual(9, len(recruitment["low_added_rate"]))
            self.assertEqual(21, len(recruitment["bachelor_target_met"]))
            self.assertEqual(8, len(recruitment["bachelor_excess"]))
            self.assertEqual(8, len(recruitment["bachelor_target_not_met"]))
            self.assertEqual(2, len(recruitment["bachelor_gap_missing"]))
            self.assertEqual(2, len(co_recruitment["target_met"]))
            self.assertEqual(5, len(co_recruitment["high_quarter_rate"]))
            self.assertEqual(9, len(co_recruitment["month_training_zero"]))
            self.assertEqual(10, len(co_recruitment["quarter_achieved_zero"]))

    @unittest.skipUnless(
        os.getenv("RUN_REAL_LLM_SMOKE") == "1",
        "set RUN_REAL_LLM_SMOKE=1 to call the configured DeepSeek model",
    )
    def test_real_deepseek_smoke(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as temp_dir:
            agent = initialize_agent(runs_dir=Path(temp_dir))
            result = chat(
                agent,
                "请基于当前数据生成五月业绩分析报告",
                data_context_id="docs",
            )
            self.assertIn(result.status, {"completed", "completed_with_warnings"})
            self.assertTrue(Path(result.report_path).is_file())


if __name__ == "__main__":
    unittest.main()
