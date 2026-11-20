# Numeric input in one pass

Read this short guide instead of the entire parser. The engine uses only Python 3.9 stdlib, exact rational arithmetic and bounded analysis. It does not infer scientific facts. Preserve actual source fields, units and version IDs in your review, even when translating them into this normalized form.

## Input shape

Required top-level: `schema_version: "numeric-audit/v1"`, `plans` (array). Each plan has `plan_id`, `version`, plus optional lists shown below. Do not invent missing candidates, preferences or figures.

When present, plans use unique actual IDs and versions, not automatically generated parent–child links:

```json
{
  "schema_version": "numeric-audit/v1",
  "plans": [{
    "plan_id": "<actual ID>",
    "version": "<actual version>",
    "parameters": [],
    "joint_constraints": [],
    "cost_items": [],
    "schedule": {"tasks": [], "resource_contention_known": false},
    "hard_constraints": [],
    "soft_metrics": [],
    "preference": {"priorities": []}
  }]
}
```

Keep each record’s `source_ref` when available, pointing to an actual source ID, physical PDF location or RFC 6901 pointer. Missing provenance is an uncertainty, not evidence that a value exists. Inputs accept finite numeric JSON numbers, exact decimal strings and safe expression strings. Rational strings like `"1/3"` are supported as results. Explicit `null` means unknown, not a default zero or unavailable-for-free resource.

## Parameters and joint constraints

`parameters`: array of objects:

```json
{"name":"gpu", "unit":"GPU-hour", "domain":[840,1600],
 "allowed_domains":[{"constraint_id":"scenario-cap", "values":[840,960,1600], "source_ref":"<actual locator>"}]
```

Domains are finite sets of exact numbers or booleans. `True` and `1` are distinct types. Preserve discrete domains, not interval midpoints or extrapolated continuous ranges. Intersect each declared list with all `allowed_domains` lists. An empty intersection is different from an omitted `parameters` array.

`joint_constraints` expresses relations among a complete tuple, not merely each axis independently:

```json
{"constraint_id":"pairing", "expr":"(gpu == 840 and valid) or (gpu == 1600)", "source_ref":"<actual locator>"}
```

Whole tuples are enumerated up to the configured cap (default 200000 per plan). Exceeding it is incomplete coverage, never a silently sampled “global optimum”.

## Cost ledger

Each cost item needs an identifier, category, quantity, unit price, currency and matching charging units.

### Machine identifiers and source-label preservation

`parameters[].name` and `cost_items[].item_id` must match the full ASCII pattern `[A-Za-z_][A-Za-z0-9_]*`. If a source label begins with a digit or contains spaces, punctuation or other characters, map it to a unique mechanical alias in your own work, rather than editing the parser or silently using its raw spelling. Keep a work-local mapping `{normalized_id: source_label, source_ref}` for every changed label. Scientific batch labels, physical IDs and original row names remain source evidence; do not confuse them with these machine aliases.

Expressions can refer to `cost_<item_id>`, `cost_<category>` (only when the category is an identifier), and `cost_total`. Those cost aliases must not collide with one another or with parameter names. Use role-distinct normalized IDs, such as `item_003` for an item and `cat_consumables` for its category, preserving the original grouping and labels in the mapping. A category must be a nonempty string; a non-identifier category has no `cost_<category>` alias and produces an explicit warning. A category that is an identifier but collides with an item or parameter alias makes that plan invalid, rather than merely producing a warning. An item name copied as its category can collide with that item. **Do not infer a missing category from the item ID**: distinguish an explicit category from a genuinely uncategorized fee.

Retain every source cost row, including explicit-zero activities, and verify row counts, unit/currency fields and source pointers after mapping. Never merge distinct items or discard a zero event to simplify identifiers. This mapping changes syntax only—not physical treatments, sample counts, costs or experimental conclusions.

Example: source `19F_qNMR_sample` could become `item_003`, with a saved mapping to its exact original item and JSON pointer. Its formula category can be `cat_analysis` only if that is the actual source-backed category; the resulting expression aliases `cost_item_003` and `cost_cat_analysis` remain distinct. A missing or ambiguous source category is a disclosed limitation, not permission to invent a scientific classification.

```json
{
 "item_id":"kits", "category":"consumables",
 "quantity":"n", "unit_price":"4.20", "events":"2",
 "fixed_cost":"0.30", "currency":"CNY",
 "quantity_unit":"kit", "price_unit":"kit",
 "recurrence":"recurring", "fixedness":"variable",
 "source_ref":"<actual locator>"
}
```

Every line is exactly:

`events * quantity * unit_price + fixed_cost`

A supplied `source_ref` must be a JSON string or explicit `null`. Missing or empty references produce an uncertainty; they are not converted to zero or numeric IDs. `source_ref` is retained in the declared cost rows and evaluated cost ledgers. Null remains supported, including when summarizing older reports where the key was absent. A non-string object or number is an invalid reference, not a default source, and is rejected consistently. Retaining the reference is not proof that its contents support the scientific interpretation.

`events` defaults to 1 and `fixed_cost` defaults to 0, only when those fields are genuinely omitted. Optional runtime values must not be mistaken for missing measurements. Recurrence/fixedness is descriptive, not a license to multi-count an event or share setup across mutually exclusive routes. Coproduct/capacity and allocation must be explicit in the case contract. A billed event count, quota, repeated physical unit, batch, staff-hour or elapsed day is not interchangeable.

Do not add currencies or infer a conversion. Currency totals stay separate. Unit mismatches and incomplete rows are problems; do not manufacture a fully paid total. An explicit zero activity/cost stays zero. An absent component or unknown allocation stays unknown.

## Schedule and parameters

`tasks`: each has `task_id`, `duration_hours`, `predecessors`, optional `person_hours`/`machine_hours`. Duration and resource formulas may reference selected parameters; resource formulas may also use `duration_hours`.

- Durations are relative positive-or-zero hours under an explicit normalized convention, not an automatically approved wall-clock date. All negative duration/person/machine hours are invalid; actual zero is allowed.
- Predecessors govern earliest starts. Unresolved previous starts/durations propagate unknown rather than zero.
- A forced-serial chain gives a sum of durations. This still is not a real booking or proof of stored capacity.
- `resource_contention_known: true` **means the source explicitly declares no relevant contention on the DAG**. Then, and only then, DAG-parallel earliest-start is available under the stated rule; actual booking still needs resource/window verification.
- When parallel capacity is unproven, `elapsed_hours` is unknown and `precedence_elapsed_lower_bound_hours` is only a lower bound. It cannot satisfy a deadline by itself.
- No implicit staffing speed multiplier, day/week/month conversion, overlap or “booked” availability is inferred. If the case gives calendar units, record explicit unit conversions in the normalized mapping rather than relabeling a day as a workday or an hour of labor.
- The schedule input above does not automatically model every daily/shift/manpower/window constraint. Audit each individual session's stated duration and peak occupied resources against real daily/shift limits separately. A total-hours or window-ceiling pass cannot repair a 14-hour session under an 8-hour daily cap. Unapproved splitting, staffing overlap or a changed version requires recomputation and a change record.
- Model dependencies through the last specified analysis/review and its actual completion day, including non-midnight deadlines and permitted working hours. Expose explicitly which calendar/booking requirements remain unmodeled or unknown; a global engine `feasible` then means only the supplied mechanical checks, never approval, real availability or complete scientific feasibility.

Optional omitted person/machine hour fields contribute zero, which is not the same as a supplied unknown value. A nonempty task whose relevant duration is unknown prevents complete schedule proof.

## Bounded expressions

Allowed: `+ - * /`, parentheses, `== != < <= > >=`, `and/or/not`, `True/False`, exact numeric strings.

These **only** are callable:

- `ceil(x)` and `floor(x)`: exactly one numeric parameter.
- `min(a,b,...)` and `max(a,b,...)`: 2–8 numeric parameters.

Limits: at most 16 nested calls and 4000 characters in an expression. No keywords/splats/trailing commas, arbitrary functions, attributes, indexing, `eval`, `exec`, sum, import, `math.ceil`, `getattr`, exponentiation or implicit scalar conversion.

Examples of valid boolean expressions include:
- `ceil(840 / 160) == 6`
- `max(10, 1 + ceil(840 / 160)) == 10`
- `min(1/3,1/2) == 1/3`
- `floor(-5 / 2) == -3`

`max(10, cost_total)` returns `10` only when `cost_total` is actually known and at least 10. When it is unknown, the result is unknown. Mixed bool/tuples/minimum comparisons remain unknown with a reason, never an invented response. If names are unknown, the tool rejects them instead of treating them as defaults. Function nesting and expression length are engineering safeguards, not scientific infeasibility.

## Hard minima versus proposed allocations

Keep three different meanings separate, including when an existing output contract uses a terse label:
- `source_constraint`: the original per-item and joint requirements, each with its unit and source.
- `derived_bound`: arithmetic following from those requirements, such as a joint lower bound derived from a count and a per-item minimum. Explain it rather than presenting it as another source rule.
- `proposed_allocation`: a balanced or otherwise chosen sampling/resource assignment. State why it satisfies the source constraints and whether it is optional; do not turn it silently into the new hard minimum.

A field labelled `minimum`, `required`, or `threshold` must not merge a recommended allocation with the actual hard gate. Always retain the original per-item and joint definitions. If an allocation changes charges, machine/staff time, samples, overlaps, risks or downstream dependencies, propagate those changes before retaining eligibility. A proposed allocation is not a measured completion count, and a satisfied planning cap is not execution readiness.

## Preferences and outcomes

`hard_constraints`: each `{constraint_id, expr, source_ref}` is tested for complete tuples, producing pass/fail/unknown across all evaluated scenarios.

`soft_metrics`: each `{metric_id, expr, direction}` uses `min` or `max` if that direction is explicitly supplied by the case. Preferences are not created from labels or generic scientific advice.

`preference.priorities`: list of `{metric_id, priority, direction}` only when an actual explicit priority exists. Shared priorities give exact lexicographic comparison; equal priorities retain the provided array order for discoverable review, not a hidden weighted total. Without shared priorities, compute a Pareto comparison only—no automatic winner, even for a singleton front. An Agent may separately defend a choice using its actual scientific goal and limitations.

Check both per-plan feasibility and coverage. Existence of a feasible tuple does not prove robustness over every scenario. Incomplete costs, schedules, unknown metrics or evidence can block determinate optimization; an upstream tool-complete run is not scientific success.

## Efficient calls

Use the real installation path and your case’s scratch files:

```bash
SKILL_DIR=/skills/weekly-skill
python3 "$SKILL_DIR/scripts/numeric_audit.py" \
  /app/work/numeric-input.json --output /app/work/numeric-report.json
python3 "$SKILL_DIR/scripts/summary_audit.py" \
  /app/work/numeric-report.json --output /app/work/numeric-summary.json
```

The full report preserves every constraint/ledger/check. The summary preserves the identities, hard failures, unknown states, blockers and representative combinations needed for review. It is not a replacement for detailed evidence. Read the summary first, then inspect **only** the relevant plan, error, constraint or representative in the full report. Do not repeatedly ingest all records after every step.

You may inspect `--help` or a tiny schema example rather than the implementation. Consult source code when fixing behavioral errors, not to rediscover the whole CLI contract. Any expressions beyond the supported language require a truthful limit or an explicit derived field with its own source-backed formula—never a fake equivalent value or magic ratio.
