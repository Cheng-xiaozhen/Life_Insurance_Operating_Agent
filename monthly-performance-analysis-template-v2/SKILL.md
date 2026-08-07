---
name: monthly-performance-analysis-template-v2
description: Plan and generate auditable, template-driven Chinese insurance performance analysis from structured data. Use when Codex needs to match a question to reusable business scenes, select candidate metrics, translate intent into ordered summarize/classify steps, execute deterministic CSV calculations, compose scenes into a standard monthly report, or maintain the scene library.
---

# Monthly Performance Analysis V2

Use the scene library to turn user intent into a declarative analysis plan, then let Python calculate and Jinja render it.

## Workflow

1. Match the user question against `scene.match` in candidate scene files.
2. Read the scene's referenced metric catalog and expose only its candidate metrics and supported operations.
3. Translate the request into an ordered `steps` plan. Use `summarize` for total-row metrics and `classify` for threshold-based organization lists; use band `conditions` for ranges, AND/OR predicates, and explicit missing-value rules.
4. Add, remove, or reorder step objects without moving calculation logic into the prompt or template.
5. Bind each logical dataset ID to a concrete CSV path and collect declared parameters.
6. Run `scripts/render_scene.py` in standard scene or report mode.
7. Return the rendered Markdown and retain the context JSON for audit.

For a standard report:

```powershell
python scripts/render_scene.py `
  --report assets/reports/monthly-performance/report.yaml `
  --dataset standard_premium=../docs/标保.csv `
  --dataset value=../docs/价值.csv `
  --dataset active_manpower=../docs/活动人力.csv `
  --dataset sunshine_manpower=../docs/阳光人力.csv `
  --dataset supervisor_activity=../docs/主管活动.csv `
  --dataset supervisor_double_star=../docs/主管双星.csv `
  --dataset standard_team=../docs/标准组.csv `
  --dataset recruitment=../docs/新增.csv `
  --dataset co_recruitment=../docs/同引.csv `
  --param report_month_name=五月 `
  --param data_month_name=5月 `
  --param quarter_name=二季度 `
  --param cutoff_date=5月31日 `
  --output output/五月业绩分析报告.md `
  --context-output output/五月业绩分析报告.context.json
```

Pass a declared threshold parameter with another `--param` to override its default for one run.

## Planning Interface

- Natural-language entries in `analysis_instructions` explain intent to people and Agents; they are never executed.
- The structured `steps` list is the sole executable analysis plan.
- A plan may reference only metrics from the linked catalog and only operations allowed by that catalog.
- A simple band uses `metric`, `operator`, and `threshold_param`; a compound band uses `conditions.match` (`all` or `any`) and rule-level metrics/operators/thresholds.
- Use `is_missing` or `not_missing` explicitly when missing-value behavior is part of the business rule.
- Preserve user-requested step order. If the user removes a classification dimension, remove its step; if the user adds one, add a `classify` step for an eligible metric.
- Keep thresholds as declared parameters so defaults remain reusable and runtime overrides remain auditable.

## Rules

- Agent: understand intent, choose scenes and metrics, and produce declarative steps.
- Python: read data, coerce values, select rows, calculate, classify, order, and build signals.
- Jinja: loop over structured results and format the standard report; never read CSV or calculate business results.
- Keep physical CSV paths out of catalogs and scenes; bind logical datasets at runtime.
- Keep raw numeric values in context and formatting choices in metric metadata or presentation configuration.
- Use `signals` for report-level content such as congratulations.
- Treat missing columns, invalid numbers, unknown steps/metrics/operators, operation mismatches, and a non-unique total row as errors. Never coerce missing metrics to zero unless a rule explicitly matches `is_missing`.
- Configuration is currently hand-authored. Do not require external JSON Schema validation; rely on focused runtime guards and tests.

Read [references/scene-contract.md](references/scene-contract.md) before adding or changing a catalog, scene, or step.
