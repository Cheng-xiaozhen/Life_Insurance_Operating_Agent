"""Run the self-contained fixed-May monthly performance analysis skill."""

from __future__ import annotations

import argparse
import importlib
import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence, cast


CliRunner = Callable[[Sequence[str] | None], int]
SKILL_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(__file__).resolve().parent
REQUIREMENTS_PATH = Path(__file__).resolve().parent / "requirements.txt"
FIXED_CONTEXT = {
    "report_month_name": "五月",
    "data_month_name": "5月",
    "quarter_name": "二季度",
    "cutoff_date": "5月31日",
}
REQUIRED_DATA_FILES = (
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


def _missing_data_files(data_dir: Path) -> list[str]:
    return [name for name in REQUIRED_DATA_FILES if not (data_dir / name).is_file()]


def resolve_data_dir(
    data_dir: str | Path | None,
    *,
    working_directory: str | Path | None = None,
) -> Path:
    """Resolve an external directory containing all nine required CSV files."""

    cwd = Path(working_directory or Path.cwd()).resolve()
    configured = data_dir or os.getenv("MONTHLY_PERFORMANCE_DATA_DIR")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = cwd / path
        path = path.resolve()
        missing = _missing_data_files(path)
        if missing:
            raise RuntimeError(
                f"数据目录缺少必需 CSV：{path}；" + "、".join(missing)
            )
        return path

    for candidate in (cwd / "docs", cwd):
        if not _missing_data_files(candidate):
            return candidate.resolve()
    raise RuntimeError(
        "未找到五月源数据。请通过 --data-dir 或 "
        "MONTHLY_PERFORMANCE_DATA_DIR 指定包含九个 CSV 的目录。"
    )


def allocate_default_output(
    *,
    output_root: str | Path | None = None,
    working_directory: str | Path | None = None,
    timestamp: str | None = None,
) -> Path:
    """Choose a portable output path without overwriting prior runs or logs."""

    cwd = Path(working_directory or Path.cwd()).resolve()
    outputs = Path(output_root or (cwd / "monthly-performance-outputs"))
    if not outputs.is_absolute():
        outputs = cwd / outputs
    outputs = outputs.resolve()
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    base = outputs / f"may-skill-{stamp}"
    candidate = base
    suffix = 2
    while candidate.exists() or _log_path(candidate).exists():
        candidate = outputs / f"{base.name}-{suffix}"
        suffix += 1
    return candidate


def resolve_output(
    output: str | Path | None,
    *,
    working_directory: str | Path | None = None,
) -> Path:
    """Resolve explicit outputs from the caller's working directory."""

    cwd = Path(working_directory or Path.cwd()).resolve()
    if output is None:
        return allocate_default_output(working_directory=cwd)
    path = Path(output).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def build_cli_args(data_dir: str | Path, output: str | Path) -> list[str]:
    """Build the single supported bundled-runtime request."""

    return [
        "monthly-report",
        "--data-dir",
        str(Path(data_dir).resolve()),
        "--report-month-name",
        FIXED_CONTEXT["report_month_name"],
        "--data-month-name",
        FIXED_CONTEXT["data_month_name"],
        "--quarter-name",
        FIXED_CONTEXT["quarter_name"],
        "--cutoff-date",
        FIXED_CONTEXT["cutoff_date"],
        "--output",
        str(Path(output).resolve()),
        "--allow-invalid-expression",
    ]


def _log_path(output: Path) -> Path:
    return output.with_name(output.name + ".log")


def _load_cli_runner(runtime_root: str | Path | None = None) -> CliRunner:
    root = Path(runtime_root or RUNTIME_ROOT).resolve()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        module = importlib.import_module("monthly_analysis.cli")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少月报运行依赖。请执行："
            f"{sys.executable} -m pip install -r {REQUIREMENTS_PATH}"
        ) from exc
    actual_path = Path(module.__file__).resolve()
    expected_path = (root / "monthly_analysis" / "cli.py").resolve()
    if actual_path != expected_path:
        raise RuntimeError(
            f"加载了错误的 monthly_analysis.cli：{actual_path}，"
            f"预期为 {expected_path}"
        )
    return cast(CliRunner, module.run)


def execute(
    data_dir: str | Path | None = None,
    output: str | Path | None = None,
    *,
    working_directory: str | Path | None = None,
    runtime_root: str | Path | None = None,
    runner: CliRunner | None = None,
) -> tuple[dict[str, object], int]:
    """Execute the bundled runtime and return a result manifest plus exit code."""

    source_path = resolve_data_dir(
        data_dir,
        working_directory=working_directory,
    )
    output_path = resolve_output(
        output,
        working_directory=working_directory,
    )
    report_path = output_path / "report.md"
    run_path = output_path / "run.json"
    log_path = _log_path(output_path)
    cli_runner = runner or _load_cli_runner(runtime_root)

    # The bundled CLI prints report.md to stdout. Suppress that line so this
    # adapter owns a single JSON stdout contract; errors and logs stay visible.
    with redirect_stdout(io.StringIO()):
        exit_code = cli_runner(build_cli_args(source_path, output_path))

    result: dict[str, object] = {
        "status": "completed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "data_dir": str(source_path),
        "report_path": str(report_path.resolve()),
        "run_path": str(run_path.resolve()),
        "log_path": str(log_path.resolve()),
    }
    if exit_code != 0:
        result["error"] = "月报生成失败；请查看命令错误和日志文件。"
        return result, exit_code

    missing = [path for path in (report_path, run_path, log_path) if not path.is_file()]
    if missing:
        result["status"] = "failed"
        result["exit_code"] = 1
        result["error"] = "运行时返回成功但缺少输出：" + "、".join(
            str(path) for path in missing
        )
        return result, 1
    return result, 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用 SKILL 内置运行时生成固定五月寿险月度经营分析报告"
    )
    parser.add_argument(
        "--data-dir",
        help=(
            "九个五月业务 CSV 所在目录；未指定时依次检查环境变量 "
            "MONTHLY_PERFORMANCE_DATA_DIR、当前目录/docs、当前目录"
        ),
    )
    parser.add_argument(
        "--output",
        help="可选输出目录；相对路径按当前工作目录解析",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    working_directory: str | Path | None = None,
    runtime_root: str | Path | None = None,
    runner: CliRunner | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        result, exit_code = execute(
            args.data_dir,
            args.output,
            working_directory=working_directory,
            runtime_root=runtime_root,
            runner=runner,
        )
    except Exception as exc:  # CLI boundary: always return structured failure.
        result = {
            "status": "failed",
            "exit_code": 1,
            "error": str(exc),
        }
        exit_code = 1
    print(json.dumps(result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
