# Scene Contract

## Responsibility Boundaries

- The Agent matches user intent to scenes and produces an ordered declarative step plan.
- The metric catalog owns source-column mappings, value types, formatters, and allowed operations for one logical dataset.
- The scene owns parameters, human-readable analysis instructions, executable steps, presentation metadata, and report signals.
- Python performs all data access, coercion, row selection, aggregation, classification, ordering, and signal construction.
- Jinja loops over the structured results and renders the standard Markdown form. It does not access source data or calculate business results.

`analysis_instructions` is documentation for people and Agents. `steps` is the sole executable source of truth.

## Metric Catalog

A scene references one catalog with `metric_catalog`. The catalog contains:

- `catalog`: stable ID, version, title, and logical `dataset` ID.
- `dimensions`: canonical dimension IDs mapped to physical columns and types.
- `row_sets.total`: selector that must resolve to exactly one aggregate row.
- `row_sets.details.organization_field`: dimension containing organization names.
- `metrics`: candidate metric definitions.

Each metric declares `column`, `type`, `unit`, `formatter`, and `operations`. Dynamic physical columns may use only `${parameter_name}` placeholders declared by the scene. `threshold_formatter` is optional; use it when a metric needs different summary and classification-threshold wording, such as同比 narrative versus a numeric percentage threshold.

Supported formatters are `amount_wan`, `person`, `pct`, `abs_pct`, `pt`, `integer`, `yoy_pct_phrase`, `yoy_pt_phrase`, and `gap_pt_phrase`. `unit` records business meaning; `formatter` controls report text. `threshold_formatter` may override the formatter used for displayed classification thresholds.

## Scene Configuration

Use these top-level keys:

- `scene`: stable `id`, semantic `version`, title, description, and matching hints.
- `metric_catalog`: path relative to the scene file.
- `inputs.parameters`: required values and reusable threshold defaults.
- `analysis_instructions`: non-executable natural-language intent.
- `steps`: ordered executable analysis plan.
- `signals`: selected step results exposed to the report layer.
- `render`: Jinja template filename and `markdown_fragment` output type.

Runtime parameters override declared defaults. Use `${parameter_name}` for restricted string interpolation. Do not place Jinja, Python, SQL, environment-variable expressions, or physical dataset paths in YAML.

## Step Contract

### `summarize`

Reads metrics from the unique total row in list order.

```yaml
- id: overall_summary
  type: summarize
  metrics:
    - metric: month_amount
      prefix: ${data_month_name}标保共达成
      separator_after: "；"
    - metric: month_rate
      prefix: "达成率 "
  presentation:
    prefix: 截至${cutoff_date}，全系统
    separator: "，"
    terminator: "。"
```

Each summary metric may specify `separator_after` to override the step separator for that item.

### `classify`

Filters detail rows into zero or more threshold bands.

```yaml
- id: monthly_rate_classification
  type: classify
  metric: month_rate
  display_order: source
  bands:
    - id: high
      operator: gt
      threshold_param: monthly_high_threshold
      presentation:
        style: threshold_list
        subject: ${data_month_name}标保达成率
        comparison: "高于 "
  presentation:
    separator: "；"
    terminator: "。"
```

Supported operators are `eq`, `gt`, `gte`, `lt`, `lte`, `is_missing`, and `not_missing`. Supported display orders are `source`, `metric_asc`, and `metric_desc`. The runtime executes explicit thresholds only; it does not infer thresholds or apply ranked/Top-N fallbacks.

For a band with more than one predicate, use a compound condition. A band may override the step metric with `metric`; `display_metric` and `display_threshold_param` control sorting and the threshold shown in the sentence:

```yaml
- id: middle
  conditions:
    match: all
    rules:
      - metric: added_rate
        operator: gt
        threshold_param: middle_low
      - metric: added_rate
        operator: lt
        threshold_param: target
  display_metric: added_rate
  display_threshold_param: middle_low
```

Compound `match` supports `all` and `any`. In addition to numeric comparisons, `is_missing` and `not_missing` explicitly match missing values. Numeric comparisons against missing values are false.

Supported band presentation styles are:

- `threshold_list`: count + subject + comparison + threshold + organization list.
- `organization_text`: organization list between configured prefix and suffix.
- `organization_threshold`: organization list followed by configured text and threshold.
- `count_list`: count + subject + organization list.
- `count_text`: count + subject without listing organizations.

Templates must skip empty bands without leaving partial punctuation or sentences.

## Signals

Expose a report-level signal by referencing a classification step and band:

```yaml
signals:
  target_met:
    from:
      step: monthly_rate_classification
      band: target_met
```

Signals carry the selected organizations plus source step and band IDs. Report templates should depend on signals rather than internal list positions.

## Runtime Context

Scene templates receive:

```json
{
  "scene": {},
  "params": {},
  "steps": [],
  "signals": {}
}
```

Summary results contain raw metric values, formatters, prefixes, item separators, and presentation metadata. Classification results contain resolved conditions, optional display thresholds, organizations, counts, operators, order, formatters, and presentation metadata.

## Change Procedure

1. Reuse an existing catalog metric, or add a candidate metric with an explicit column, type, formatter, and allowed operations.
2. Add, remove, or reorder a scene step. Do not add a Python branch for a business-specific metric or step ID.
3. Keep the scene Jinja generic; branch only on supported step or presentation types.
4. Add or update golden context and Markdown tests.
5. Add failure tests when a new runtime guard or configuration form is introduced.

`operations` is enforced at runtime: a metric used by a step must declare that operation. Configuration is currently hand-authored and no external JSON Schema is required. The runtime must still fail on missing parameters or columns, invalid numeric values, an unknown step/metric/operator/order/style, a non-unique total row, and an invalid signal reference.
