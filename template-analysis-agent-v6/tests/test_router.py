from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from agent.catalog import ReportCatalog
from agent.llm import FakeLLMClient
from agent.models import AgentContractError, RouteDecision, SessionState
from agent.router import IntentRouter, ParameterBinder, normalize_month


REPORTS_DIR = (
    PROJECT_DIR
    / "skills"
    / "monthly-performance-analysis"
    / "references"
    / "reports"
)


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ReportCatalog(REPORTS_DIR)

    def test_catalog_exposes_only_routing_metadata(self) -> None:
        summary = self.catalog.summaries()[0]
        self.assertEqual("monthly-performance", summary["id"])
        self.assertEqual(
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
            summary["scene_ids"],
        )
        self.assertIn("params_schema", summary)
        self.assertNotIn("binding", summary)
        self.assertNotIn("analysis", summary)

    def test_fake_router_matches_natural_language_and_extracts_month(self) -> None:
        client = FakeLLMClient()
        decision = IntentRouter(client, self.catalog).route(
            "请基于当前数据生成2026年5月业绩分析报告"
        )
        self.assertEqual("execute", decision.action)
        self.assertEqual("monthly-performance", decision.report_id)
        self.assertEqual(
            {"report_month": "五月", "month_label": "5月"},
            decision.extracted_params,
        )

    def test_low_confidence_execute_becomes_clarification(self) -> None:
        client = FakeLLMClient(
            route_responses=[
                RouteDecision(
                    action="execute", report_id="monthly-performance", confidence=0.4
                )
            ]
        )
        decision = IntentRouter(client, self.catalog).route("生成报告")
        self.assertEqual("clarify", decision.action)

    def test_unknown_report_is_rejected(self) -> None:
        client = FakeLLMClient(
            route_responses=[
                RouteDecision(action="execute", report_id="invented", confidence=0.99)
            ]
        )
        with self.assertRaisesRegex(AgentContractError, "unknown report id"):
            IntentRouter(client, self.catalog).route("生成报告")

    def test_month_normalization_and_explicit_override(self) -> None:
        self.assertEqual(("五月", "5月"), normalize_month("2026年5月"))
        route = RouteDecision(
            action="execute",
            report_id="monthly-performance",
            confidence=0.99,
            extracted_params={"month_label": "6月"},
        )
        bound = ParameterBinder().bind(
            route,
            self.catalog.get("monthly-performance"),
            SessionState(parameters={"report_month": "五月", "month_label": "5月"}),
        )
        self.assertEqual(
            {
                "report_month": "六月",
                "month_label": "6月",
                "quarter_label": "二季度",
            },
            bound.values,
        )

    def test_unknown_parameter_is_rejected(self) -> None:
        route = RouteDecision(
            action="execute",
            report_id="monthly-performance",
            confidence=0.99,
            extracted_params={"data_dir": "../outside"},
        )
        with self.assertRaisesRegex(AgentContractError, "unknown report parameters"):
            ParameterBinder().bind(route, self.catalog.get("monthly-performance"))


if __name__ == "__main__":
    unittest.main()
