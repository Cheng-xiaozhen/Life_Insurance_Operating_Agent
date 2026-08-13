from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from monthly_analysis import TemplateExecutionError, execute_report, render_markdown


def _parse_parameters(items: list[str]) -> dict[str, str]:
    parameters: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"Parameter must use name=value syntax: {item}")
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            raise argparse.ArgumentTypeError(f"Parameter name cannot be empty: {item}")
        parameters[name] = value
    return parameters


def build_parser() -> argparse.ArgumentParser:
    skill_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Execute the minimal declarative monthly performance report."
    )
    parser.add_argument(
        "--data-dir", type=Path, required=True, help="Directory containing report CSV inputs"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=skill_root / "references" / "reports" / "monthly-performance.yaml",
        help="Compatible Report YAML path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "output",
        help="Directory for generated JSON and Markdown",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Override a Report parameter; repeat as needed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        runtime_parameters = _parse_parameters(args.param)
        result = execute_report(args.report, args.data_dir, runtime_parameters)
        markdown = render_markdown(result)

        args.output_dir.mkdir(parents=True, exist_ok=True)
        facts_path = args.output_dir / f"{result['report_id']}.facts.json"
        report_path = args.output_dir / f"{result['report_id']}.md"
        facts_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report_path.write_text(markdown, encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "completed",
                    "facts_path": str(facts_path.resolve()),
                    "report_path": str(report_path.resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (TemplateExecutionError, OSError, argparse.ArgumentTypeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
