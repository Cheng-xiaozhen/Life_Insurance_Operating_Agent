---
name: monthly-performance-analysis
description: Generate a monthly life-insurance performance report from external CSV data by executing declarative Report, Scene, and Binding YAML resources. Use for the complete v6 monthly performance workflow across performance, manpower, supervisor, organization, recruitment, and co-recruitment scenes, and for validating the template-driven runtime against a CSV data directory.
---

# Monthly Performance Analysis

Generate deterministic scene facts and a baseline Markdown report from external CSV data. Keep business rules in declarative YAML and use the bundled runtime only as an interpreter.

## Workflow

1. Locate the external data directory. The current report requires `标保.csv`, `价值.csv`, `活动人力.csv`, `阳光人力.csv`, `主管活动.csv`, `主管双星.csv`, `标准组.csv`, `新增.csv`, and `同引.csv`.
2. Resolve `scripts/run_report.py` relative to this `SKILL.md`.
3. Install `scripts/requirements.txt` into the active Python environment when PyYAML is unavailable.
4. Run the script with the data directory and an output directory outside this skill:

```powershell
python <skill-directory>\scripts\run_report.py `
  --data-dir <csv-directory> `
  --output-dir <output-directory>
```

5. Read the JSON status printed by the script.
6. On `completed`, return the generated facts JSON and Markdown report. On `failed`, report the error without inventing missing facts.

Use `--param month_label=6月 --param report_month=六月` when the input CSV uses another month label. Use `--param quarter_label=三季度` when the co-recruitment CSV uses another quarter prefix. Use `--report <path>` only when executing another compatible Report YAML.

## Resources

- `references/reports/monthly-performance.yaml` selects and orders scenes.
- `references/scenes/*.yaml` defines the nine report analyses selected by the Report resource.
- `references/bindings/monthly-csv.yaml` maps semantic fields to CSV columns.
- `scripts/monthly_analysis/` implements the minimal `summary` and `select` interpreter.

## Guardrails

- Treat source CSV files as read-only inputs.
- Do not copy CSV data into the skill.
- Do not add business-specific field names, thresholds, or scene order to runtime code.
- Use only facts produced by the interpreter; do not infer values when a file or column is missing.
- Add new scenes by extending Scene and Binding YAML; do not add business rules to runtime code.
