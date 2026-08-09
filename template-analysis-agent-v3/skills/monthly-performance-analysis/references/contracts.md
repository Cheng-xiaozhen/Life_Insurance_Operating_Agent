# V3 Contract Boundaries

## Ownership

| Concern | Owner |
| --- | --- |
| Trigger phrases and required inputs | scene `manifest.yaml` |
| Analysis intent, thresholds, rules, and steps | scene `scene.yaml` |
| Metric labels, units, formatters, and allowed operations | metric catalog |
| Query inputs, canonical output, timeout, and permissions | query manifest |
| CSV path, physical columns, dynamic columns, total/detail rows | source profile |
| Tone, terminology, and paired examples | expression assets |
| Report order and report-level callouts | report recipe |

## Execution Invariants

- The router returns only `RoutingDecision`.
- `PlanCompiler` alone creates `CompiledAnalysisPlan`.
- A template can reference only registered queries, metrics, steps, operators,
  and sort modes.
- Query results must become `CanonicalDataset` before analysis.
- Step handlers are registered program code; arbitrary formulas and `eval` are
  prohibited.
- Every computed fact retains its query/version and source-row provenance.
- Later steps depend on facts, never on generated prose.
- The expression provider receives facts rather than raw datasets.
- The narrative validator must accept the draft before assembly. Failed repair
  falls back to generic deterministic fact rendering.

## Scene Schema

`scene.yaml` may contain:

- `scene_id`, `query_ref`, and query-parameter mappings;
- typed, declared parameters;
- analyst-facing `analysis_intent`;
- `summarize` and `classify` steps;
- threshold rules, compound `all`/`any` conditions, and sorting;
- report signals and expression-asset references.

It must not contain physical data mappings, SQL, Python/Jinja expressions, or
sentence fragments such as prefix/suffix/subject/comparison/presentation.

## Audit Contract

Each run writes:

1. `request.json`
2. `routing.json`
3. `plan.json`
4. `query-manifest.json`
5. `facts.json`
6. `model-response.json`
7. `validation.json`
8. `report.md`

These artifacts are one immutable evidence set for the assembled report.
