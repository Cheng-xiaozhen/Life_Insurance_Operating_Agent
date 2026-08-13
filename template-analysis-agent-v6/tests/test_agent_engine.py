from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_DIR.parent
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
sys.path.insert(0, str(PROJECT_DIR))

from agent.llm import FakeLLMClient
from agent.models import AgentRequest
from standalone_agent import initialize_agent


class AgentEngineTests(unittest.TestCase):
    def make_agent(self, run_dir: Path, llm: FakeLLMClient | None = None):
        client = llm or FakeLLMClient()
        return initialize_agent(
            llm_client=client,
            data_contexts={"docs": WORKSPACE_DIR / "docs"},
            runs_dir=run_dir,
        ), client

    def test_natural_language_to_report_and_audit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as temp_dir:
            agent, client = self.make_agent(Path(temp_dir))
            result = agent.chat(
                AgentRequest(
                    message="请基于当前数据生成五月业绩分析报告",
                    data_context_id="docs",
                )
            )
            self.assertEqual("completed", result.status)
            self.assertEqual("monthly-performance", result.report_id)
            self.assertEqual(
                {
                    "report_month": "五月",
                    "month_label": "5月",
                    "quarter_label": "二季度",
                },
                result.parameters,
            )
            self.assertEqual(len(EXPECTED_SCENE_IDS), client.analysis_calls)

            run_dir = Path(result.report_path).parent
            self.assertEqual(
                {
                    "events.jsonl",
                    "facts.json",
                    "narratives.json",
                    "report.md",
                    "request.json",
                    "route.json",
                },
                {path.name for path in run_dir.iterdir()},
            )
            facts = json.loads(Path(result.facts_path).read_text(encoding="utf-8"))
            self.assertEqual(
                EXPECTED_SCENE_IDS,
                [scene["scene_id"] for scene in facts["scenes"]],
            )
            scenes = {scene["scene_id"]: scene for scene in facts["scenes"]}
            self.assertEqual(
                61.8,
                scenes["standard-premium"]["facts"]["overall"]["month_rate"]["value"],
            )
            self.assertEqual(
                74.1,
                scenes["value"]["facts"]["overall"]["month_rate"]["value"],
            )
            markdown = Path(result.report_path).read_text(encoding="utf-8")
            heading_offsets = [
                markdown.index(f"## {scenes[scene_id]['title']}")
                for scene_id in EXPECTED_SCENE_IDS
            ]
            self.assertEqual(sorted(heading_offsets), heading_offsets)

    def test_invalid_scene_output_retries_then_falls_back(self) -> None:
        invalid = {
            "scene_id": "standard-premium",
            "content": "- 火星分公司（99%）表现领先。",
            "used_fact_ids": ["overall"],
            "warnings": [],
        }
        llm = FakeLLMClient(analysis_responses=[invalid, invalid])
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as temp_dir:
            agent, _ = self.make_agent(Path(temp_dir), llm)
            result = agent.chat(
                "生成五月业绩分析报告",
                data_context_id="docs",
            )
            self.assertEqual("completed_with_warnings", result.status)
            self.assertEqual(1, len(result.warnings))
            narratives = json.loads(
                (Path(result.report_path).parent / "narratives.json").read_text(encoding="utf-8")
            )
            narrative_scenes = {
                scene["scene_id"]: scene for scene in narratives["scenes"]
            }
            self.assertTrue(narrative_scenes["standard-premium"]["used_fallback"])
            self.assertFalse(narrative_scenes["value"]["used_fallback"])
            self.assertNotIn("火星分公司", Path(result.report_path).read_text(encoding="utf-8"))

    def test_missing_data_context_can_continue_in_same_session(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as temp_dir:
            agent, _ = self.make_agent(Path(temp_dir))
            first = agent.chat(
                "生成五月业绩分析报告",
                session_id="session-1",
            )
            self.assertEqual("needs_input", first.status)
            self.assertIn("数据", first.message)
            second = agent.chat(
                "继续生成",
                session_id="session-1",
                data_context_id="docs",
            )
            self.assertEqual("completed", second.status)

    def test_requested_month_missing_from_csv_fails_before_scene_llm(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as temp_dir:
            agent, client = self.make_agent(Path(temp_dir))
            result = agent.chat("生成六月业绩分析报告", data_context_id="docs")
            self.assertEqual("failed", result.status)
            self.assertEqual("monthly-performance", result.report_id)
            self.assertEqual(0, client.analysis_calls)
            self.assertIn("六月", result.parameters["report_month"])
            self.assertIn("字段", result.message)

    def test_single_scene_request_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as temp_dir:
            agent, client = self.make_agent(Path(temp_dir))
            result = agent.chat("只分析价值", data_context_id="docs")
            self.assertEqual("unsupported", result.status)
            self.assertEqual(0, client.analysis_calls)


class AgentStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_uses_same_business_state_machine(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as temp_dir:
            agent = initialize_agent(
                llm_client=FakeLLMClient(),
                data_contexts={"docs": WORKSPACE_DIR / "docs"},
                runs_dir=Path(temp_dir),
            )
            events = [
                event
                async for event in agent.chat_stream(
                    "生成五月业绩分析报告", data_context_id="docs"
                )
            ]
            event_types = [event.type for event in events]
            self.assertEqual("request_received", event_types[0])
            self.assertEqual("done", event_types[-1])
            self.assertIn("route_selected", event_types)
            self.assertEqual(
                len(EXPECTED_SCENE_IDS),
                event_types.count("scene_analysis_ready"),
            )
            self.assertEqual("completed", events[-1].data["result"]["status"])


if __name__ == "__main__":
    unittest.main()
