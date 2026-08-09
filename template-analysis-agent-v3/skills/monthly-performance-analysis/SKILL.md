---
name: monthly-performance-analysis
description: Generate auditable life-insurance monthly performance analysis from registered templates and bound CSV or memory datasets. Use when the user asks for a full monthly operating report or a registered scene such as standard premium, value, manpower, supervisor activity, standard teams, recruitment, or co-recruitment; also use to inspect plans/facts or validate this template package.
---

# Monthly Performance Analysis

Use the V3 engine as a controlled template interpreter. The LLM may select a
registered template, extract explicit parameters, and express computed facts. It
must not create analysis steps, queries, metrics, thresholds, calculations, or
recommendations.

## Workflow

1. Identify whether the request targets the `monthly-performance` report or one
   or more registered scenes in `assets/scenes/`.
2. Collect the required report parameters and data bindings. Do not guess a
   missing reporting period or silently substitute a dataset.
3. Run `scripts/run_analysis.py inspect-plan ...` when the selected plan or
   parameter overrides need review.
4. Run `scripts/run_analysis.py analyze ...` to execute registered queries,
   normalize data, compute facts, validate expression, assemble Markdown, and
   write audit files.
5. Report an `insufficient_data` scene as such. Do not call an expression model
   for a required query that failed or returned invalid/empty data.
6. Use `inspect-facts` when exact values, thresholds, organizations, or source
   rows must be verified.

## Guardrails

- Treat `scene.yaml` steps as the only executable analysis source.
- Resolve every `query_ref` through the controlled query registry.
- Keep CSV paths, physical columns, and row-selection rules in data-source
  profiles, never in scenes.
- Allow only template-declared parameter overrides.
- Generate prose only from `FactBundle`; retain every `fact_ref`.
- If narrative repair fails, use the generic deterministic fact rendering.
- Preserve all audit artifacts produced for the run.

## Resources

- Read `references/contracts.md` before changing scene, query, metric,
  source-profile, fact, narrative, or audit contracts.
- Read `references/template-design.md` before adding or modifying a scene,
  report recipe, metric, query contract, source profile, or expression asset.
- `assets/scenes/` contains scene manifests and executable steps.
- `assets/reports/` contains whole-report recipes and report-level signals.
- `assets/metrics/` contains source-independent metric catalogs.
- `assets/styles/`, `assets/glossary.yaml`, and `assets/examples/` constrain
  expression.
- `scripts/run_analysis.py` exposes `analyze`, `inspect-plan`,
  `inspect-facts`, and `validate-template`.
