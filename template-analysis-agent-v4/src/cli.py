"""Command-line entry point for offline template-driven analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from loguru import logger

from .engine import AnalysisAgent
from .models import (
    AnalysisDataset,
    AnalysisRequest,
    MonthlyReportRequest,
    MonthlyReportResult,
    OrganizationRow,
    Scalar,
)
from .monthly import MonthlyReportAgent


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _parse_parameters(values: list[str]) -> dict[str, Scalar]:
    result: dict[str, Scalar] = {}
    for value in values:
        key, raw = value.split("=", 1)
        key = key.strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        result[key] = parsed
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模板驱动分析 Agent V4")
    parser.add_argument(
        "--templates",
        default=str(PACKAGE_ROOT / "templates"),
        help="模板目录",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="执行一次分析")
    analyze.add_argument("--question", default="", help="自然语言问题")
    analyze.add_argument("--input", required=True, help="JSON 数据")
    analyze.add_argument("--param", action="append", default=[], help="key=value")
    analyze.add_argument("--output", required=True, help="新建输出目录")
    analyze.add_argument(
        "--log-file",
        help="日志文件；默认保存在输出目录旁，文件名为 <output>.log",
    )
    analyze.add_argument(
        "--allow-invalid-expression",
        action="store_true",
        help="表达校验失败时记录告警但继续生成结果",
    )
    monthly = subparsers.add_parser(
        "monthly-report",
        help="从九个 CSV 生成完整月度报告",
    )
    monthly.add_argument("--data-dir", required=True, help="九个业务 CSV 所在目录")
    monthly.add_argument("--report-month-name", required=True, help="报告标题月份")
    monthly.add_argument("--data-month-name", required=True, help="CSV 数据月份")
    monthly.add_argument("--quarter-name", required=True, help="CSV 数据季度")
    monthly.add_argument("--cutoff-date", required=True, help="数据截止日期")
    monthly.add_argument("--output", required=True, help="新建输出目录")
    monthly.add_argument(
        "--log-file",
        help="日志文件；默认保存在输出目录旁，文件名为 <output>.log",
    )
    monthly.add_argument(
        "--allow-invalid-expression",
        action="store_true",
        help="表达校验失败时记录告警但继续生成结果",
    )
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
            "parameters": request.parameters,
        },
        "result": result.to_dict(exclude={"report_markdown"}),
    }
    (output / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_monthly_result(
    output: Path,
    request: MonthlyReportRequest,
    result: MonthlyReportResult,
) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.md").write_text(result.report_markdown, encoding="utf-8")
    run = {
        "schema_version": "2.0",
        "request": request.to_dict(),
        "result": result.to_dict(exclude={"report_markdown"}),
    }
    (output / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    file_sink_id: int | None = None
    try:
        output = Path(args.output).resolve()
        if output.exists():
            raise ValueError(f"输出目录已存在，拒绝覆盖：{output}")
        log_file = (
            Path(args.log_file).resolve()
            if args.log_file
            else output.with_name(output.name + ".log")
        )
        if log_file == output or output in log_file.parents:
            raise ValueError("日志文件不能位于输出目录内，请指定输出目录旁的路径")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if log_file.exists():
            if not log_file.is_file():
                raise ValueError(f"日志路径不是文件：{log_file}")
            log_file.unlink()
        file_sink_id = logger.add(
            str(log_file),
            level="INFO",
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
                "{name}:{function}:{line} - {message}"
            ),
            encoding="utf-8",
            colorize=False,
            mode="w",
        )
        logger.info("本次运行日志文件：{}", log_file)

        if args.command == "monthly-report":
            request = MonthlyReportRequest(
                data_dir=str(Path(args.data_dir).resolve()),
                report_month_name=args.report_month_name,
                data_month_name=args.data_month_name,
                quarter_name=args.quarter_name,
                cutoff_date=args.cutoff_date,
            )
            result = MonthlyReportAgent(
                PACKAGE_ROOT,
                template_dir=args.templates,
                allow_invalid_expression=args.allow_invalid_expression,
            ).analyze(request)
            if result.status == "failed":
                print("；".join(result.errors), file=sys.stderr)
                return 1
            _write_monthly_result(output, request, result)
            print(str(output / "report.md"))
            return 0

        agent = AnalysisAgent(
            args.templates,
            allow_invalid_expression=args.allow_invalid_expression,
        )
        input_path = Path(args.input).resolve()
        raw_dataset = json.loads(input_path.read_text(encoding="utf-8"))
        dataset = AnalysisDataset(
            summary=raw_dataset["summary"],
            rows=[OrganizationRow(**row) for row in raw_dataset["rows"]],
        )
        request = AnalysisRequest(
            question=args.question,
            dataset=dataset,
            parameters=_parse_parameters(args.param),
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
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if file_sink_id is not None:
            logger.remove(file_sink_id)


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
