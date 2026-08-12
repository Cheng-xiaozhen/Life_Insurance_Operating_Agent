---
name: monthly-performance-analysis
description: Generate the fixed May life-insurance monthly performance report from nine external CSV datasets using the skill's bundled deterministic runtime and DeepSeek narration. Use when the user asks for 月度分析、月度经营分析、月报、五月业绩分析报告, or to regenerate the current May report. Do not use for another month, a single business scene or metric, template maintenance, or ad hoc CSV analysis.
---

# Monthly Performance Analysis

Generate the fixed May report with the code, templates, source profiles, and
report recipe bundled in this skill. Keep the nine source CSV files outside the
skill and pass their containing directory to the runner.

## Bundled Resources

- `scripts/run_monthly_report.py` is the stable command-line entry point.
- `scripts/monthly_analysis/` contains the deterministic report runtime.
- `templates/*.yaml` contains the nine scene templates.
- `templates/profiles/` contains the external CSV binding profile.
- `templates/reports/` contains the monthly report assembly recipe.

## Workflow

1. Treat a request without an explicit month as a request for the fixed May
   report. If the user explicitly requests another month, explain that the MVP
   supports only May and stop.
2. Locate one external directory containing all nine required CSV files:
   `标保.csv`, `价值.csv`, `活动人力.csv`, `阳光人力.csv`, `主管活动.csv`,
   `主管双星.csv`, `标准组.csv`, `新增.csv`, and `同引.csv`. Use a path supplied
   by the user; otherwise use `docs` under the current workspace when present.
   If no unique directory is available, ask for its path instead of guessing.
3. Resolve `scripts/` relative to the directory containing this `SKILL.md`, then
   run `run_monthly_report.py --data-dir <data-directory>` with Python 3.11 or
   newer. If imports are missing, install `scripts/requirements.txt` into the
   active environment, then retry once.
4. Read the JSON object printed by the runner. On `failed`, report the error and
   log path without inventing or partially reproducing a report.
5. On `completed`, read `report_path` and return the complete Markdown report.
   Also link `report_path`, `run_path`, and `log_path` using absolute paths.

Example:

```powershell
python <skill-directory>\scripts\run_monthly_report.py `
  --data-dir C:\path\to\may-data
```

Set `DEEPSEEK_API_KEY` in the environment or a `.env` file in the working
directory. Optionally set `DEEPSEEK_MODEL`; the bundled runtime otherwise uses
its configured default.

## Guardrails

- Run only code and analysis assets under this skill directory. Do not import
  or copy runtime files from any neighboring source checkout.
- Keep source CSV files external and read-only.
- Keep the fixed context as `五月`, `5月`, `二季度`, and `5月31日`.
- Do not recalculate facts, reinterpret thresholds, or edit bundled templates
  while generating a report.
- Allow narrative-validation warnings without weakening data, computation, or
  model-call failures.
- Write generated reports outside the skill directory.
