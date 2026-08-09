"""Command-line interface for the local V3 prototype."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from .application import AnalysisApplication
from .errors import AnalysisAgentError
from .models import AnalysisRequest, DataBinding
from .routing import PlanCompiler


def _bindings(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key.strip() or not item.strip():
            raise ValueError(f"参数必须使用 name=value：{value!r}")
        key = key.strip()
        if key in result:
            raise ValueError(f"参数重复：{key}")
        result[key] = item.strip()
    return result


def _add_request_arguments(parser: argparse.ArgumentParser, *, data: bool) -> None:
    parser.add_argument("--question", required=True, help="用户自然语言问题")
    parser.add_argument("--report", dest="report_id", help="显式报告 ID")
    parser.add_argument(
        "--scene",
        dest="scene_ids",
        action="append",
        default=[],
        help="显式场景 ID，可重复",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="模板参数",
    )
    parser.add_argument(
        "--provider",
        choices=["deterministic", "deepseek"],
        default="deterministic",
        help="表达器；离线默认 deterministic",
    )
    parser.add_argument(
        "--style",
        dest="output_style",
        help="已注册的表达风格 ID",
    )
    if data:
        parser.add_argument(
            "--dataset",
            action="append",
            default=[],
            metavar="ID=PATH",
            help="逻辑数据绑定",
        )
        parser.add_argument("--output-dir", help="运行审计输出根目录")


def parse_args() -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="模板驱动、确定性计算、受约束表达的经营分析 Agent"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help="template-analysis-agent-v3 项目根目录",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="执行分析并生成报告与审计记录")
    _add_request_arguments(analyze, data=True)
    inspect_plan = commands.add_parser("inspect-plan", help="只路由并编译分析计划")
    _add_request_arguments(inspect_plan, data=False)
    inspect_facts = commands.add_parser("inspect-facts", help="执行并打印结构化事实")
    _add_request_arguments(inspect_facts, data=True)
    commands.add_parser("validate-template", help="校验全部模板与查询配置")
    return parser.parse_args()


def _request(args: argparse.Namespace) -> AnalysisRequest:
    parameters = _bindings(args.param)
    datasets = _bindings(getattr(args, "dataset", []))
    return AnalysisRequest(
        question=args.question,
        report_id=args.report_id,
        scene_ids=args.scene_ids,
        parameters=parameters,
        data_bindings={
            dataset_id: DataBinding(source=path)
            for dataset_id, path in datasets.items()
        },
        expression_provider=args.provider,
        output_style=args.output_style,
        output_dir=getattr(args, "output_dir", None),
    )


def main() -> None:
    args = parse_args()
    try:
        application = AnalysisApplication(args.root)
        if args.command == "validate-template":
            print(
                json.dumps(
                    application.validate_configuration(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        request = _request(args)
        if args.command == "inspect-plan":
            routing = application._route(request)
            plan = PlanCompiler(application.template_registry).compile(
                routing,
                output_style=request.output_style,
            )
            print(plan.model_dump_json(indent=2))
            return
        result = application.run(request)
        if args.command == "inspect-facts":
            print(
                json.dumps(
                    [
                        bundle.model_dump(mode="json")
                        for bundle in result.fact_bundles
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"报告已生成：{result.report_path}")
            print(f"审计目录：{result.audit_path}")
    except (AnalysisAgentError, ValidationError, ValueError) as exc:
        raise SystemExit(f"错误：{exc}") from exc


if __name__ == "__main__":
    main()
