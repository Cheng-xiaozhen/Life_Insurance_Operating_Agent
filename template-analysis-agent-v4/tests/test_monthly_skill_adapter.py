from __future__ import annotations

import importlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
SKILL_ROOT = (
    REPOSITORY_ROOT
    / ".agents"
    / "skills"
    / "monthly-performance-analysis"
)
ADAPTER_PATH = SKILL_ROOT / "scripts" / "run_monthly_report.py"


def load_adapter(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load_adapter(ADAPTER_PATH, "monthly_skill_adapter")


class GroupedFakeProvider:
    def __init__(self, block_type):
        self.block_type = block_type

    def express(self, title, facts, groups, context):
        del title, context
        fact_index = {fact.fact_id: fact for fact in facts}
        blocks = []
        for group in groups:
            parts = []
            for fact_id in group.fact_ids:
                fact = fact_index[fact_id]
                if fact.kind == "summary":
                    parts.append(f"{fact.title}：{fact.display_value}。")
                    continue
                organizations = "、".join(
                    item.organization for item in fact.items
                )
                suffix = f"：{organizations}" if organizations else ""
                parts.append(
                    f"{fact.title}（{fact.rule_text}）共{fact.count}家{suffix}。"
                )
            blocks.append(
                self.block_type(
                    group_id=group.id,
                    fact_ids=group.fact_ids,
                    text="- " + " ".join(parts),
                )
            )
        return blocks


class MonthlySkillAdapterTests(unittest.TestCase):
    def test_skill_contains_the_complete_runtime_but_no_source_data(self):
        runtime = SKILL_ROOT / "scripts"
        expected_python = {
            "__init__.py",
            "cli.py",
            "data.py",
            "engine.py",
            "expression.py",
            "models.py",
            "monthly.py",
            "templates.py",
        }
        self.assertEqual(
            {path.name for path in (runtime / "monthly_analysis").glob("*.py")},
            expected_python,
        )
        templates = SKILL_ROOT / "templates"
        self.assertEqual(len(list(templates.glob("*.yaml"))), 9)
        self.assertTrue((templates / "profiles" / "monthly-performance.yaml").is_file())
        self.assertTrue((templates / "reports" / "monthly-performance.yaml").is_file())
        self.assertTrue((SKILL_ROOT / "scripts" / "requirements.txt").is_file())
        self.assertFalse(list(SKILL_ROOT.rglob("*.csv")))
        self.assertNotIn(
            "template-analysis-agent-v4",
            ADAPTER_PATH.read_text(encoding="utf-8"),
        )

    def test_resolves_and_validates_an_external_data_directory(self):
        self.assertEqual(
            adapter.resolve_data_dir(REPOSITORY_ROOT / "docs"),
            REPOSITORY_ROOT / "docs",
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "缺少必需 CSV"):
                adapter.resolve_data_dir(directory)

    def test_builds_only_the_fixed_may_request(self):
        output = REPOSITORY_ROOT / "temporary-output"
        args = adapter.build_cli_args(REPOSITORY_ROOT / "docs", output)

        def value(flag):
            return args[args.index(flag) + 1]

        self.assertEqual(args[0], "monthly-report")
        self.assertEqual(value("--data-dir"), str(REPOSITORY_ROOT / "docs"))
        self.assertEqual(value("--report-month-name"), "五月")
        self.assertEqual(value("--data-month-name"), "5月")
        self.assertEqual(value("--quarter-name"), "二季度")
        self.assertEqual(value("--cutoff-date"), "5月31日")
        self.assertEqual(value("--output"), str(output.resolve()))
        self.assertIn("--allow-invalid-expression", args)

    def test_default_output_preserves_existing_directory_and_failure_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = root / "outputs"
            outputs.mkdir()
            (outputs / "may-skill-20260811-120000").mkdir()
            (outputs / "may-skill-20260811-120000-2.log").write_text(
                "prior failure\n",
                encoding="utf-8",
            )
            selected = adapter.allocate_default_output(
                output_root=outputs,
                timestamp="20260811-120000",
            )
            self.assertEqual(
                selected,
                outputs / "may-skill-20260811-120000-3",
            )

    def test_isolated_skill_copy_runs_offline_with_external_data(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            copied_skill = temporary_root / "monthly-performance-analysis"
            shutil.copytree(
                SKILL_ROOT,
                copied_skill,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            copied_adapter = load_adapter(
                copied_skill / "scripts" / "run_monthly_report.py",
                "isolated_monthly_skill_adapter",
            )
            runtime = copied_skill / "scripts"
            output = temporary_root / "monthly"
            self._clear_runtime_modules()
            try:
                runner = copied_adapter._load_cli_runner(runtime)
                cli_module = importlib.import_module("monthly_analysis.cli")
                models_module = importlib.import_module("monthly_analysis.models")
                monthly_module = importlib.import_module("monthly_analysis.monthly")
                self.assertTrue(Path(cli_module.__file__).is_relative_to(copied_skill))
                provider = GroupedFakeProvider(models_module.NarrativeBlock)
                with patch.object(
                    monthly_module,
                    "DeepSeekExpressionProvider",
                    return_value=provider,
                ):
                    result, exit_code = copied_adapter.execute(
                        REPOSITORY_ROOT / "docs",
                        output,
                        working_directory=temporary_root,
                        runtime_root=runtime,
                        runner=runner,
                    )
            finally:
                self._clear_runtime_modules()
                sys.path[:] = [item for item in sys.path if item != str(runtime)]

            self.assertEqual(exit_code, 0, result)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["data_dir"], str(REPOSITORY_ROOT / "docs"))
            self.assertEqual(result["report_path"], str(output / "report.md"))
            self.assertEqual(result["run_path"], str(output / "run.json"))
            self.assertEqual(result["log_path"], str(output.with_name("monthly.log")))
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["request"]["report_month_name"], "五月")
            self.assertEqual(len(run["result"]["scene_results"]), 9)

    def test_main_preserves_runtime_failure_code_and_emits_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "monthly"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = adapter.main(
                    [
                        "--data-dir",
                        str(REPOSITORY_ROOT / "docs"),
                        "--output",
                        str(output),
                    ],
                    working_directory=directory,
                    runner=lambda args: 7,
                )
            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 7)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["exit_code"], 7)
            self.assertEqual(
                result["log_path"],
                str(output.with_name("monthly.log")),
            )

    @staticmethod
    def _clear_runtime_modules():
        for name in list(sys.modules):
            if name == "monthly_analysis" or name.startswith("monthly_analysis."):
                del sys.modules[name]


if __name__ == "__main__":
    unittest.main()
