"""Command-line entry points for template validation and offline analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from .engine import AnalysisAgent
from .models import AnalysisDataset, AnalysisRequest, Scalar


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parse_parameters(values: list[str]) -> dict[str, Scalar]:
    result: dict[str, Scalar] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"参数必须使用 key=value：{value}")
        key, raw = value.split("=", 1)
        key = key.strip()
        if not key or key in result:
            raise ValueError(f"参数名为空或重复：{key!r}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        if isinstance(parsed, (dict, list)):
            raise ValueError(f"参数只支持标量：{key}")
        result[key] = parsed
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模板驱动分析 Agent V4")
    parser.add_argument(
        "--templates",
        default=str(PROJECT_ROOT / "templates"),
        help="模板目录",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-templates", help="校验全部模板")
    analyze = subparsers.add_parser("analyze", help="执行一次分析")
    analyze.add_argument("--question", default="", help="自然语言问题")
    analyze.add_argument("--template", dest="template_id", help="显式模板 ID")
    analyze.add_argument("--input", required=True, help="规范化 JSON 数据")
    analyze.add_argument(
        "--provider",
        choices=["deterministic", "deepseek"],
        default="deterministic",
    )
    analyze.add_argument("--param", action="append", default=[], help="key=value")
    analyze.add_argument("--output", required=True, help="新建输出目录")
    return parser


def _write_result(
    output: Path,
    request: AnalysisRequest,
    result: object,
) -> None:
    from .models import AnalysisResult

    assert isinstance(result, AnalysisResult)
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.md").write_text(result.report_markdown, encoding="utf-8")
    run = {
        "schema_version": "1.0",
        "request": {
            "question": request.question,
            "template_id": request.template_id,
            "parameters": request.parameters,
            "provider": request.provider,
        },
        "result": result.model_dump(mode="json", exclude={"report_markdown"}),
    }
    (output / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        agent = AnalysisAgent(args.templates)
        if args.command == "validate-templates":
            print(json.dumps(agent.validate_templates(), ensure_ascii=False, indent=2))
            return 0
        output = Path(args.output).resolve()
        if output.exists():
            raise ValueError(f"输出目录已存在，拒绝覆盖：{output}")
        input_path = Path(args.input).resolve()
        dataset = AnalysisDataset.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        request = AnalysisRequest(
            question=args.question,
            template_id=args.template_id,
            dataset=dataset,
            parameters=_parse_parameters(args.param),
            provider=args.provider,
        )
        result = agent.analyze(request)
        if result.status == "needs_clarification":
            print(result.routing.clarification or "需要补充信息", file=sys.stderr)
            return 2
        if result.status == "failed":
            print("；".join(result.errors), file=sys.stderr)
            return 1
        _write_result(output, request, result)
        print(str(output / "report.md"))
        return 0
    except (OSError, ValueError, ValidationError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
