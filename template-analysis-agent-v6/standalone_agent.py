from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from dotenv import load_dotenv

from agent.analyzer import ReportAssembler, SceneAnalyzer
from agent.catalog import ReportCatalog
from agent.engine import TemplateAnalysisAgent
from agent.llm import DeepSeekLLMClient, FakeLLMClient, LLMClient
from agent.models import AgentEvent, AgentRequest, AgentResult
from agent.router import IntentRouter, ParameterBinder
from agent.store import InMemorySessionStore, RunStore
from agent.tools import DataContextRegistry, ReportExecutionTool


PROJECT_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = PROJECT_ROOT / "skills" / "monthly-performance-analysis"
SKILL_SCRIPTS = SKILL_ROOT / "scripts"
REPORTS_DIR = SKILL_ROOT / "references" / "reports"


def _load_runtime() -> tuple[Any, Any]:
    scripts_path = str(SKILL_SCRIPTS)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    from monthly_analysis import execute_report, render_markdown

    return execute_report, render_markdown


def initialize_agent(
    *,
    llm_client: LLMClient | None = None,
    data_contexts: Mapping[str, str | Path] | None = None,
    runs_dir: str | Path | None = None,
    offline: bool = False,
) -> TemplateAnalysisAgent:
    """Construct the Agent while keeping configuration out of business modules."""
    load_dotenv(PROJECT_ROOT.parent / ".env")
    if llm_client is None:
        if offline:
            llm_client = FakeLLMClient()
        else:
            llm_client = DeepSeekLLMClient(
                api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            )

    configured_contexts = dict(data_contexts or {})
    default_docs = PROJECT_ROOT.parent / "docs"
    if "docs" not in configured_contexts and default_docs.is_dir():
        configured_contexts["docs"] = default_docs

    execute_report, render_markdown = _load_runtime()
    catalog = ReportCatalog(REPORTS_DIR)
    registry = DataContextRegistry(
        configured_contexts,
        allowed_roots=[PROJECT_ROOT.parent],
    )
    return TemplateAnalysisAgent(
        catalog=catalog,
        router=IntentRouter(llm_client, catalog),
        parameter_binder=ParameterBinder(),
        report_tool=ReportExecutionTool(catalog, registry, execute_report),
        scene_analyzer=SceneAnalyzer(llm_client),
        report_assembler=ReportAssembler(render_markdown),
        run_store=RunStore(runs_dir or PROJECT_ROOT / "runs"),
        session_store=InMemorySessionStore(),
        model_name=llm_client.model_name,
    )


def chat(
    agent: TemplateAnalysisAgent,
    message: str,
    *,
    session_id: str | None = None,
    data_context_id: str | None = "docs",
) -> AgentResult:
    return agent.chat(
        AgentRequest(
            message=message,
            session_id=session_id,
            data_context_id=data_context_id,
        )
    )


async def chat_stream(
    agent: TemplateAnalysisAgent,
    message: str,
    *,
    session_id: str | None = None,
    data_context_id: str | None = "docs",
) -> AsyncIterator[AgentEvent]:
    async for event in agent.chat_stream(
        AgentRequest(
            message=message,
            session_id=session_id,
            data_context_id=data_context_id,
        )
    ):
        yield event


async def _stream_cli(agent: TemplateAnalysisAgent, request: AgentRequest) -> AgentResult:
    result: AgentResult | None = None
    async for event in agent.chat_stream(request):
        print(json.dumps(event.to_dict(), ensure_ascii=False))
        if event.type == "done":
            result = AgentResult.from_mapping(event.data["result"])
    if result is None:
        raise RuntimeError("stream ended without a final result")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the template-analysis Agent")
    parser.add_argument("message", help="natural-language report request")
    parser.add_argument("--data-context-id", default="docs")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--session-id")
    parser.add_argument("--offline", action="store_true", help="use deterministic fake LLM")
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()

    contexts: dict[str, Path] = {}
    if args.data_dir:
        contexts[args.data_context_id] = args.data_dir
    agent = initialize_agent(data_contexts=contexts, offline=args.offline)
    request = AgentRequest(
        message=args.message,
        session_id=args.session_id,
        data_context_id=args.data_context_id,
    )
    if args.stream:
        result = asyncio.run(_stream_cli(agent, request))
    else:
        result = agent.chat(request)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status in {"completed", "completed_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
