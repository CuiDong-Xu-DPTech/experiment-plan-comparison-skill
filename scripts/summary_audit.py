#!/usr/bin/env python3
"""Compact a shipped numeric-audit/v1 report without re-running its calculations.

Python 3.9 stdlib only:
  python3 summary_audit.py INPUT.json [--output summary.json]
           [--max-chars 12000] [--keep-representatives 4]

The compact JSON is mechanical selection/constraint diagnostics for an Agent,
not scientific validation or comprehensive evidence review. The original report
and its recomputed SHA-256 remain the linked evidence for Human Review. Numeric
strings, rational strings, true zeroes, booleans and explicit null unknowns are
copied, never evaluated. Configuration, IO and schema failures emit structured
JSON on stderr and exit 2. A cap refusal is valid JSON with summary_status
"refused" and a full-report link; essential constraint/blocker IDs are never
silently clipped to make a pseudo-summary fit.
"""

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal


SOURCE_SCHEMA = "numeric-audit/v1"
SUMMARY_SCHEMA = "numeric-audit-summary/v1"
DEFAULT_MAX_CHARS = 12000
DEFAULT_REPRESENTATIVES = 4
MAX_REPRESENTATIVES = 20
PLAN_STATUSES = {"feasible", "infeasible", "undetermined", "error"}
CONSTRAINT_STATUSES = {"satisfied", "violated", "undetermined", "vacuous"}
RESULT_COLLECTIONS = (
    "feasible_representatives",
    "undetermined_representatives",
    "violations",
)
COVERAGE_COUNTS = (
    "scenario_count",
    "enumerated_count",
    "joint_admissible_count",
    "fully_admissible_count",
    "infeasible_scenario_count",
    "undetermined_scenario_count",
    "metric_incomplete_scenario_count",
    "incomplete_cost_scenario_count",
    "incomplete_schedule_scenario_count",
    "unproven_parallel_scenario_count",
    "feasible_representatives_omitted",
    "undetermined_representatives_omitted",
    "violations_omitted",
)
COVERAGE_LAYOUT = (
    "state",
    "max_combinations",
    "scenario_count",
    "enumerated_count",
    "joint_admissible_count",
    "fully_admissible_count",
    "infeasible_scenario_count",
    "undetermined_scenario_count",
    "metric_incomplete_scenario_count",
    "incomplete_cost_scenario_count",
    "incomplete_schedule_scenario_count",
    "unproven_parallel_scenario_count",
    "all_scenarios_enumerated",
    "all_scenarios_fully_admissible",
    "representative_limit",
    "feasible_representatives_omitted",
    "undetermined_representatives_omitted",
    "violations_omitted",
)
REPRESENTATIVE_DIAGNOSTIC_LAYOUT = (
    "stored_records",
    "distinct_stored_configurations",
    "included_groups",
    "included_stored_records_with_multiplicity",
    "identical_records_folded",
    "summary_omitted_stored_records",
    "upstream_omitted_records_before_summary",
)
CONSTRAINT_STATUS_LAYOUT = ("satisfied", "violated", "undetermined", "vacuous")
REPRESENTATIVE_KEYS = {
    "representatives",
    "nondominated_representatives",
    "nondominated",
    "unique_winner",
}
SCOPE = {
    "mode": "compact_only",
    "purpose": "mechanical selection and constraint diagnostics",
    "agent_use": "Agent triage; raw report is not required",
    "human_review": "full_report holds complete evidence and ledgers",
    "not_assessed": ["scientific_validity", "comprehensive_evidence"],
    "inspection_rule": "any nonzero flag, unknown or omission requires Human Review",
    "value_encoding": "values follow parameters.names; unit_refs indexes parameter_units; zeroes/nulls are preserved",
    "plan_status_meaning": "engine calculation only; not scientific viability",
    "constraint_status_layout": CONSTRAINT_STATUS_LAYOUT,
    "constraint_row_layout": [
        "kind",
        "constraint_id",
        "status_index",
        "satisfied_count",
        "violation_count",
        "unknown_count",
        "source_ref_index_or_null",
        "expr_only_for_violated_or_undetermined",
    ],
    "coverage_row_layout": COVERAGE_LAYOUT,
    "coverage_encoding": "complete plans use coverage_row_layout; sparse error objects and null remain explicit",
    "representative_diagnostic_layout": REPRESENTATIVE_DIAGNOSTIC_LAYOUT,
}


class ConfigError(Exception):
    pass


class SchemaError(Exception):
    pass


class ParserCLI(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigError(message)


def fail(path, message):
    raise SchemaError("%s: %s" % (path, message))


def obj(value, path):
    if not isinstance(value, dict):
        fail(path, "expected an object, got %s" % type(value).__name__)
    return value


def arr(value, path):
    if not isinstance(value, list):
        fail(path, "expected an array, got %s" % type(value).__name__)
    return value


def required(owner, key, path):
    if key not in owner:
        fail(path, "missing required key %s" % key)
    return owner[key]


def string(value, path, nullable=False):
    if value is None and nullable:
        return value
    if not isinstance(value, str):
        fail(path, "expected string or null" if nullable else "expected a string")
    return value


def count(value, path):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        fail(path, "expected a nonnegative integer count")
    return value


def scalar(value, path):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        fail(path, "non-integer JSON numbers are not emitted by numeric-audit/v1")
    fail(path, "expected a numeric string, integer, boolean, or explicit null")


def json_subset(value, path):
    if isinstance(value, dict):
        for key, child in value.items():
            json_subset(child, "%s/%s" % (path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            json_subset(child, "%s/%d" % (path, index))
    elif isinstance(value, Decimal):
        fail(path, "non-integer JSON numbers are not emitted by numeric-audit/v1")
    elif isinstance(value, float):
        fail(path, "non-finite or binary floating-point data is forbidden")
    return value


def scalar_map(value, path):
    result = obj(value, path)
    for key, child in result.items():
        scalar(child, "%s/%s" % (path, key))
    return result


def object_array(value, path):
    result = arr(value, path)
    for index, child in enumerate(result):
        obj(child, "%s/%d" % (path, index))
    return result


def text_array(value, path):
    result = arr(value, path)
    for index, child in enumerate(result):
        string(child, "%s/%d" % (path, index))
    return result


def pointer_tokens(*values):
    return "/" + "/".join(str(value).replace("~", "~0").replace("/", "~1") for value in values)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def dump_json(value):
    return canonical(value) + "\n"


def validate_coverage(value, path, error_plan):
    coverage = obj(value, path)
    count(required(coverage, "scenario_count", path), path + "/scenario_count")
    enumerated = required(coverage, "all_scenarios_enumerated", path)
    if not isinstance(enumerated, bool):
        fail(path + "/all_scenarios_enumerated", "expected a boolean")
    if not error_plan:
        for key in COVERAGE_COUNTS:
            count(required(coverage, key, path), path + "/" + key)
        text = string(required(coverage, "state", path), path + "/state")
        if not text:
            fail(path + "/state", "coverage state must not be empty")
        for key in ("all_scenarios_fully_admissible",):
            flag = required(coverage, key, path)
            if flag is not None and not isinstance(flag, bool):
                fail(path + "/" + key, "expected boolean or null")
        count(required(coverage, "max_combinations", path), path + "/max_combinations")
        count(required(coverage, "representative_limit", path), path + "/representative_limit")
    return coverage


def validate_costs(value, path):
    costs = obj(value, path)
    string(required(costs, "status", path), path + "/status")
    scalar(required(costs, "cost_total", path), path + "/cost_total")
    string(required(costs, "currency", path), path + "/currency", nullable=True)
    scalar_map(required(costs, "currency_totals", path), path + "/currency_totals")
    scalar_map(required(costs, "category_totals", path), path + "/category_totals")
    text_array(required(costs, "issues", path), path + "/issues")
    items = obj(required(costs, "items", path), path + "/items")
    for item_id, item in items.items():
        item_path = pointer_tokens(*path.strip("/").split("/"), "items", item_id)
        obj(item, item_path)
        for key in ("events", "quantity", "unit_price", "fixed_cost", "total"):
            scalar(required(item, key, item_path), item_path + "/" + key)
        string(required(item, "item_id", item_path), item_path + "/item_id")
        string(required(item, "currency", item_path), item_path + "/currency", nullable=True)
        string(required(item, "quantity_unit", item_path), item_path + "/quantity_unit", nullable=True)
        string(required(item, "price_unit", item_path), item_path + "/price_unit", nullable=True)
        if "source_ref" in item:
            string(item["source_ref"], item_path + "/source_ref", nullable=True)
        text_array(required(item, "unknown_fields", item_path), item_path + "/unknown_fields")
        arr(required(item, "issues", item_path), item_path + "/issues")
    return costs


def compact_costs(costs):
    return {
        "status": costs["status"],
        "cost_total": costs["cost_total"],
        "currency": costs["currency"],
        "currency_totals": costs["currency_totals"],
        "category_totals": costs["category_totals"],
        "issues": costs["issues"],
    }


def validate_schedule(value, path):
    schedule = obj(value, path)
    string(required(schedule, "status", path), path + "/status")
    for key in (
        "elapsed_hours",
        "person_hours_total",
        "machine_hours_total",
        "precedence_elapsed_lower_bound_hours",
    ):
        scalar(required(schedule, key, path), path + "/" + key)
    string(required(schedule, "resource_contention_status", path), path + "/resource_contention_status")
    string(required(schedule, "timing_basis", path), path + "/timing_basis")
    parallel = required(schedule, "parallelism_possible", path)
    if not isinstance(parallel, bool):
        fail(path + "/parallelism_possible", "expected a boolean")
    tasks = object_array(required(schedule, "tasks", path), path + "/tasks")
    for index, task in enumerate(tasks):
        task_path = "%s/tasks/%d" % (path, index)
        string(required(task, "task_id", task_path), task_path + "/task_id")
        for key in (
            "duration_hours",
            "earliest_start_hours",
            "earliest_end_hours",
            "person_hours",
            "machine_hours",
        ):
            scalar(required(task, key, task_path), task_path + "/" + key)
        arr(required(task, "issues", task_path), task_path + "/issues")
    return schedule


def compact_schedule(schedule):
    return {
        key: schedule[key]
        for key in (
            "status",
            "elapsed_hours",
            "person_hours_total",
            "machine_hours_total",
            "resource_contention_status",
            "timing_basis",
            "parallelism_possible",
            "precedence_elapsed_lower_bound_hours",
        )
    }


def validate_record(record, path, collection):
    obj(record, path)
    count(required(record, "scenario_index", path), path + "/scenario_index")
    scalar_map(required(record, "values", path), path + "/values")
    if collection == "feasible_representatives":
        string(required(record, "plan_id", path), path + "/plan_id")
        scalar(required(record, "version", path), path + "/version")
        scalar_map(required(record, "metrics", path), path + "/metrics")
        object_array(required(record, "metric_issues", path), path + "/metric_issues")
    elif collection == "undetermined_representatives":
        for key in ("joint_unknown", "hard_unknown"):
            value = required(record, key, path)
            if not isinstance(value, bool):
                fail(path + "/" + key, "expected a boolean")
    elif collection == "violations":
        if required(record, "kind", path) not in ("joint", "hard"):
            fail(path + "/kind", "expected joint or hard")
        string(required(record, "constraint_id", path), path + "/constraint_id")
    if "costs" in record:
        validate_costs(record["costs"], path + "/costs")
    if "schedule" in record:
        validate_schedule(record["schedule"], path + "/schedule")
    if collection != "violations" or record["kind"] == "hard":
        required(record, "costs", path)
        required(record, "schedule", path)
    return record


def compact_record(record, parameter_names, pointer):
    values = obj(record["values"], "values")
    names = list(values) if parameter_names is None else parameter_names
    if set(values) != set(names):
        fail(pointer, "representative values do not match the plan parameter table")
    result = {
        "scenario_index": record["scenario_index"],
        "values": [values[name] for name in names],
        "report_pointer": pointer,
    }
    if "metrics" in record:
        result["metrics"] = record["metrics"]
        result["metric_issues"] = record["metric_issues"]
    if "joint_unknown" in record:
        result["joint_unknown"] = record["joint_unknown"]
        result["hard_unknown"] = record["hard_unknown"]
    if "constraint_id" in record:
        result["kind"] = record["kind"]
        result["constraint_id"] = record["constraint_id"]
    if "costs" in record:
        result["costs"] = compact_costs(record["costs"])
        result["schedule"] = compact_schedule(record["schedule"])
    return result


def grouped_records(records):
    groups = {}
    order = []
    for index, record in enumerate(records):
        identity = {key: value for key, value in record.items() if key != "scenario_index"}
        key = canonical(identity)
        if key not in groups:
            groups[key] = {"record": record, "indices": [index]}
            order.append(key)
        else:
            groups[key]["indices"].append(index)
    return [groups[key] for key in order]


def group_diagnostics(groups, included, stored_count, upstream_omitted):
    included_records = sum(len(group["indices"]) for group in included)
    included_groups = len(included)
    return [
        stored_count,
        len(groups),
        included_groups,
        included_records,
        included_records - included_groups,
        stored_count - included_records,
        upstream_omitted,
    ]


def discard_representatives(value):
    result = {}
    for key, child in value.items():
        if key not in REPRESENTATIVE_KEYS:
            result[key] = child
        elif key == "unique_winner" and child is None:
            result[key] = None
    return result


def item_flags(item):
    flags = []
    if item["currency"] is None:
        flags.append("missing_currency")
    if item["quantity_unit"] is None:
        flags.append("missing_quantity_unit")
    if item["price_unit"] is None:
        flags.append("missing_price_unit")
    if (
        item["quantity_unit"] is not None
        and item["price_unit"] is not None
        and item["quantity_unit"] != item["price_unit"]
    ):
        flags.append("mismatched_quantity_price_units")
    return flags


def merge_observation(target, key, entry, pointer):
    identity = canonical(entry)
    if identity not in target:
        target[identity] = dict(entry)
        target[identity]["first_observation"] = pointer
        target[identity]["stored_observation_count"] = 1
    else:
        target[identity]["stored_observation_count"] += 1


def collect_resource_diagnostics(plan, plan_index):
    diagnostics = {}
    declared = {}
    cost_audit = obj(plan.get("cost_audit", {}), "/cost_audit")
    for index, item in enumerate(arr(cost_audit.get("items", []), "/cost_audit/items")):
        item_path = "/plans/%d/cost_audit/items/%d" % (plan_index, index)
        obj(item, item_path)
        for key in ("item_id", "currency", "quantity_unit", "price_unit"):
            string(required(item, key, item_path), item_path + "/" + key, nullable=key != "item_id")
        for key in ("events", "quantity", "unit_price", "fixed_cost"):
            scalar(required(item, key, item_path), item_path + "/" + key)
        if "source_ref" in item:
            string(item["source_ref"], item_path + "/source_ref", nullable=True)
        flags = item_flags(item)
        if flags:
            merge_observation(
                declared,
                item["item_id"],
                {
                    "item_id": item["item_id"],
                    "currency": item["currency"],
                    "quantity_unit": item["quantity_unit"],
                    "price_unit": item["price_unit"],
                    "events": item["events"],
                    "quantity": item["quantity"],
                    "unit_price": item["unit_price"],
                    "fixed_cost": item["fixed_cost"],
                    "flags": flags,
                },
                item_path,
            )
    if declared:
        diagnostics["declared_cost_items"] = list(declared.values())

    ledger_items = {}
    ledger_states = {}
    schedule_states = {}
    task_states = {}
    candidate_identities = {}
    results = obj(plan.get("results", {}), "/results")
    record_sources = []
    for collection in RESULT_COLLECTIONS:
        records = arr(results.get(collection, []), "/results/" + collection)
        record_sources.append((collection, records, "/plans/%d/results/%s" % (plan_index, collection)))
    optimization = obj(plan.get("optimization", {}), "/optimization")
    for collection in ("representatives", "nondominated_representatives"):
        records = arr(optimization.get(collection, []), "/optimization/" + collection)
        for index, record in enumerate(records):
            validate_record(record, "/plans/%d/optimization/%s/%d" % (plan_index, collection, index), "feasible_representatives")
        record_sources.append((collection, records, "/plans/%d/optimization/%s" % (plan_index, collection)))
    for collection, records, base_path in record_sources:
        for record_index, record in enumerate(records):
            record_path = "%s/%d" % (base_path, record_index)
            if "plan_id" in record and (
                record["plan_id"] != plan["plan_id"] or record["version"] != plan["version"]
            ):
                merge_observation(
                    candidate_identities,
                    record["plan_id"],
                    {
                        "candidate_plan_id": record["plan_id"],
                        "candidate_version": record["version"],
                        "enclosing_plan_id": plan["plan_id"],
                        "enclosing_version": plan["version"],
                        "scenario_index": record["scenario_index"],
                        "storage_collection": collection,
                    },
                    record_path,
                )
            if "costs" not in record:
                continue
            costs = record["costs"]
            if costs["status"] != "complete" or costs["issues"]:
                merge_observation(
                    ledger_states,
                    canonical(costs),
                    compact_costs(costs),
                    record_path + "/costs",
                )
            for item_id, item in costs["items"].items():
                flags = item_flags(item)
                if item["unknown_fields"]:
                    flags.append("unknown_cost_components")
                if item["issues"]:
                    flags.append("cost_component_issues")
                if item["total"] is None:
                    flags.append("unknown_item_total")
                if not flags:
                    continue
                entry = {
                    key: item[key]
                    for key in (
                        "item_id",
                        "currency",
                        "quantity_unit",
                        "price_unit",
                        "events",
                        "quantity",
                        "unit_price",
                        "fixed_cost",
                        "total",
                        "unknown_fields",
                        "issues",
                    )
                }
                entry["flags"] = flags
                merge_observation(
                    ledger_items,
                    item_id,
                    entry,
                    record_path + "/costs/items/" + item_id,
                )
            schedule = record["schedule"]
            if schedule["status"] != "complete" or schedule["resource_contention_status"] == "unproven_parallel":
                merge_observation(
                    schedule_states,
                    canonical(schedule),
                    compact_schedule(schedule),
                    record_path + "/schedule",
                )
            for task_index, task in enumerate(schedule["tasks"]):
                if not task["issues"] and all(
                    task[key] is not None
                    for key in ("duration_hours", "person_hours", "machine_hours")
                ):
                    continue
                entry = {
                    key: task[key]
                    for key in (
                        "task_id",
                        "duration_hours",
                        "earliest_start_hours",
                        "earliest_end_hours",
                        "person_hours",
                        "machine_hours",
                        "issues",
                    )
                }
                merge_observation(
                    task_states,
                    task["task_id"],
                    entry,
                    record_path + "/tasks/%d" % task_index,
                )
    for key, values in (
        ("candidate_identity_mismatches", candidate_identities),
        ("evaluated_cost_ledgers", ledger_states),
        ("evaluated_cost_items", ledger_items),
        ("evaluated_schedules", schedule_states),
        ("evaluated_schedule_tasks", task_states),
    ):
        if values:
            diagnostics[key] = list(values.values())
    return diagnostics


def summarize_plan(plan, plan_index, keep_representatives, compact_mode):
    path = "/plans/%d" % plan_index
    obj(plan, path)
    plan_id = string(required(plan, "plan_id", path), path + "/plan_id")
    version = scalar(required(plan, "version", path), path + "/version")
    status = required(plan, "status", path)
    if status not in PLAN_STATUSES:
        fail(path + "/status", "unknown numeric engine plan status %r" % status)
    reasons = object_array(required(plan, "reasons", path), path + "/reasons")
    output = {
        "plan_id": plan_id,
        "version": version,
        "status": status,
        "reasons": reasons,
        "warnings": arr(plan.get("warnings", []), path + "/warnings"),
        "uncertainties": object_array(plan.get("uncertainties", []), path + "/uncertainties"),
        "report_pointer": path,
    }
    error_plan = status == "error"
    if "coverage" not in plan:
        if not error_plan:
            fail(path, "non-error plan has no coverage")
        output["coverage"] = None
        output["coverage_note"] = "unavailable after plan execution error; no zero counts inferred"
    else:
        output["coverage"] = validate_coverage(plan["coverage"], path + "/coverage", error_plan)
    optimization = obj(required(plan, "optimization", path), path + "/optimization")
    output["optimization"] = discard_representatives(optimization)
    output["optimization"]["report_pointer"] = path + "/optimization"
    if "blocking_reasons" in optimization:
        text_array(optimization["blocking_reasons"], path + "/optimization/blocking_reasons")

    domains = arr(plan.get("domain_resolution", []), path + "/domain_resolution")
    if not error_plan and "domain_resolution" not in plan:
        fail(path, "non-error plan has no domain_resolution")
    parameters = {"names": [], "units": []}
    domain_diagnostics = []
    for index, domain in enumerate(domains):
        domain_path = "%s/domain_resolution/%d" % (path, index)
        obj(domain, domain_path)
        name = string(required(domain, "name", domain_path), domain_path + "/name")
        unit = scalar(required(domain, "unit", domain_path), domain_path + "/unit")
        scalar_values = arr(required(domain, "values", domain_path), domain_path + "/values")
        for value_index, value in enumerate(scalar_values):
            scalar(value, "%s/values/%d" % (domain_path, value_index))
        duplicates = count(required(domain, "duplicates_removed", domain_path), domain_path + "/duplicates_removed")
        allowed = arr(required(domain, "allowed_constraints", domain_path), domain_path + "/allowed_constraints")
        empty = required(domain, "empty", domain_path)
        if not isinstance(empty, bool):
            fail(domain_path + "/empty", "expected a boolean")
        parameters["names"].append(name)
        parameters["units"].append(unit)
        if empty or duplicates or unit is None:
            domain_diagnostics.append({
                "name": name,
                "unit": unit,
                "empty": empty,
                "duplicates_removed": duplicates,
                "allowed_constraints": allowed,
                "values_pointer": domain_path + "/values",
            })
    output["parameters"] = parameters if not error_plan or domains else None
    if domain_diagnostics:
        output["domain_diagnostics"] = domain_diagnostics

    constraint_rows = []
    constraint_keys = set()
    violated_without_stored_example = []
    constraints_available = False
    for collection, kind in (("joint_constraints", "joint"), ("hard_constraints", "hard")):
        if collection in plan:
            constraints_available = True
        constraints = arr(plan.get(collection, []), path + "/" + collection)
        if not error_plan and collection not in plan:
            fail(path, "non-error plan has no " + collection)
        for index, constraint in enumerate(constraints):
            constraint_path = "%s/%s/%d" % (path, collection, index)
            obj(constraint, constraint_path)
            constraint_id = string(required(constraint, "constraint_id", constraint_path), constraint_path + "/constraint_id")
            key = (kind, constraint_id)
            if key in constraint_keys:
                fail(constraint_path, "duplicate %s constraint_id %s" % (kind, constraint_id))
            constraint_keys.add(key)
            constraint_status = required(constraint, "status", constraint_path)
            if constraint_status not in CONSTRAINT_STATUSES:
                fail(constraint_path + "/status", "invalid constraint status")
            satisfied = count(required(constraint, "satisfied_count", constraint_path), constraint_path + "/satisfied_count")
            violation = count(required(constraint, "violation_count", constraint_path), constraint_path + "/violation_count")
            unknown = count(required(constraint, "unknown_count", constraint_path), constraint_path + "/unknown_count")
            expected = "violated" if violation else "undetermined" if unknown else "vacuous" if not satisfied else "satisfied"
            if constraint_status != expected:
                fail(constraint_path, "status does not match its satisfaction/violation/unknown counts")
            source_ref = required(constraint, "source_ref", constraint_path)
            string(source_ref, constraint_path + "/source_ref", nullable=True)
            row = [kind, constraint_id, constraint_status, satisfied, violation, unknown, source_ref]
            if constraint_status in ("violated", "undetermined"):
                row.append(json_subset(required(constraint, "expr", constraint_path), constraint_path + "/expr"))
            if constraint_status == "violated":
                violated_without_stored_example.append([kind, constraint_id, constraint_path])
            constraint_rows.append(row)
    output["constraints"] = constraint_rows if constraints_available or not error_plan else None

    results_unavailable = error_plan and "results" not in plan
    results = obj(plan.get("results", {}), path + "/results")
    for collection in RESULT_COLLECTIONS:
        if not error_plan and collection not in results:
            fail(path + "/results", "missing " + collection)
        records = arr(results.get(collection, []), path + "/results/" + collection)
        for index, record in enumerate(records):
            record_path = "%s/results/%s/%d" % (path, collection, index)
            validate_record(record, record_path, collection)
            if collection == "violations":
                key = (record["kind"], record["constraint_id"])
                if key not in constraint_keys:
                    fail(record_path, "violation refers to an undeclared constraint")
                marker = [record["kind"], record["constraint_id"]]
                violated_without_stored_example = [
                    row for row in violated_without_stored_example if row[:2] != marker
                ]
    if violated_without_stored_example:
        output["violated_constraints_without_stored_example"] = [
            {"kind": row[0], "constraint_id": row[1], "report_pointer": row[2]}
            for row in violated_without_stored_example
        ]

    feasible_groups = grouped_records(arr(results.get("feasible_representatives", []), "feasible"))
    unknown_groups = grouped_records(arr(results.get("undetermined_representatives", []), "undetermined"))
    violation_groups = grouped_records(arr(results.get("violations", []), "violations"))
    feasible_limit = min(keep_representatives, 1) if compact_mode >= 2 else keep_representatives
    unknown_limit = 1 if compact_mode else max(1, keep_representatives)
    feasible_included = feasible_groups[:feasible_limit]
    unknown_included = unknown_groups[:unknown_limit]
    if compact_mode:
        seen_failures = set()
        violation_included = []
        for group in violation_groups:
            record = group["record"]
            key = (record["kind"], record["constraint_id"])
            if key not in seen_failures:
                seen_failures.add(key)
                violation_included.append(group)
    else:
        violation_included = violation_groups

    def converted(groups, collection):
        result = []
        for group in groups:
            index = group["indices"][0]
            pointer = "%s/results/%s/%d" % (path, collection, index)
            record = compact_record(group["record"], parameters["names"], pointer)
            record["identical_records_folded"] = len(group["indices"]) - 1
            record["stored_record_multiplicity"] = len(group["indices"])
            if "plan_id" in group["record"] and (
                group["record"]["plan_id"] != plan_id or group["record"]["version"] != version
            ):
                record["plan_id"] = group["record"]["plan_id"]
                record["version"] = group["record"]["version"]
                record["version_note"] = "candidate identity differs from enclosing plan; copied verbatim"
            result.append(record)
        return result

    coverage = output["coverage"] or {}
    if results_unavailable:
        output["representatives"] = None
        output["representative_diagnostics"] = None
        output["representatives_note"] = "unavailable after plan execution error; no empty results or zero counts inferred"
    else:
        output["representatives"] = {
            "feasible": converted(feasible_included, "feasible_representatives"),
            "violations": converted(violation_included, "violations"),
            "undetermined": converted(unknown_included, "undetermined_representatives"),
        }
        output["representative_diagnostics"] = {
            "feasible": group_diagnostics(
                feasible_groups,
                feasible_included,
                len(results.get("feasible_representatives", [])),
                coverage.get("feasible_representatives_omitted"),
            ),
            "violations": group_diagnostics(
                violation_groups,
                violation_included,
                len(results.get("violations", [])),
                coverage.get("violations_omitted"),
            ),
            "undetermined": group_diagnostics(
                unknown_groups,
                unknown_included,
                len(results.get("undetermined_representatives", [])),
                coverage.get("undetermined_representatives_omitted"),
            ),
        }
    diagnostics = collect_resource_diagnostics(plan, plan_index)
    if diagnostics:
        output["resource_diagnostics"] = diagnostics
    if "cost_audit" in plan:
        cost_audit = obj(plan["cost_audit"], path + "/cost_audit")
        output["cost_audit"] = {
            key: value for key, value in cost_audit.items() if key != "items"
        }
        output["cost_audit"]["items_pointer"] = path + "/cost_audit/items"
    if "schedule_audit" in plan:
        output["schedule_audit"] = plan["schedule_audit"]
    return {"output": output, "parameters": parameters, "raw": plan}


def selection_output(selection, plans):
    selection = obj(selection, "/selection")
    state = string(required(selection, "state", "/selection"), "/selection/state")
    output = discard_representatives(selection)
    if "blocking_reasons" in selection:
        text_array(selection["blocking_reasons"], "/selection/blocking_reasons")
    winner = selection.get("unique_winner")
    if state == "unique_best":
        if not isinstance(winner, dict):
            fail("/selection/unique_winner", "unique_best requires an actual candidate object")
        if selection.get("best_candidate_count") != 1:
            fail("/selection", "unique_best requires best_candidate_count exactly one")
        if selection.get("blocking_reasons"):
            fail("/selection", "unique_best with blocking reasons is not deterministic")
        validate_record(winner, "/selection/unique_winner", "feasible_representatives")
        matches = []
        for index, plan in enumerate(plans):
            raw = plan["raw"]
            if (
                raw.get("plan_id") == winner["plan_id"]
                and raw.get("version") == winner["version"]
            ):
                matches.append(plan)
        matching_parameters = matches[0]["parameters"] if len(matches) == 1 else None
        candidate = compact_record(winner, matching_parameters["names"] if matching_parameters else None, "/selection/unique_winner")
        candidate["plan_id"] = winner["plan_id"]
        candidate["version"] = winner["version"]
        if matching_parameters:
            candidate["parameter_units"] = {
                name: matching_parameters["units"][index]
                for index, name in enumerate(matching_parameters["names"])
            }
        output["unique_winner"] = candidate
        output["determinism_basis"] = "copied from actual global unique_best selection; no new preference applied"
    else:
        if winner is not None:
            fail("/selection/unique_winner", "non-unique global state cannot contain a winner")
        output["unique_winner"] = None
    output["report_pointer"] = "/selection"
    return output


def build_summary(document, full_report, keep_representatives, compact_mode, max_chars):
    obj(document, "/")
    if document.get("schema_version") != SOURCE_SCHEMA:
        raise SchemaError("top-level schema_version must be numeric-audit/v1")
    json_subset(document, "/")
    engine_status = string(required(document, "status", "/"), "/status")
    engine_totals = obj(required(document, "summary", "/"), "/summary")
    for key in ("plan_count", "feasible", "infeasible", "undetermined", "error"):
        count(required(engine_totals, key, "/summary"), "/summary/" + key)
    all_feasible = required(engine_totals, "all_plans_feasible", "/summary")
    if all_feasible is not None and not isinstance(all_feasible, bool):
        fail("/summary/all_plans_feasible", "expected boolean or null")
    plans = arr(required(document, "plans", "/"), "/plans")
    summarized = [summarize_plan(plan, index, keep_representatives, compact_mode) for index, plan in enumerate(plans)]

    selected = selection_output(required(document, "selection", "/"), summarized)
    source_refs = []
    source_indexes = {}
    parameter_units = []
    unit_indexes = {}
    status_indexes = {status: index for index, status in enumerate(CONSTRAINT_STATUS_LAYOUT)}
    for plan in summarized:
        output = plan["output"]
        coverage = output["coverage"]
        if isinstance(coverage, dict) and "state" in coverage:
            output["coverage"] = [coverage[key] for key in COVERAGE_LAYOUT]
        parameters = output["parameters"]
        if parameters is not None:
            unit_refs = []
            for unit in parameters["units"]:
                key = canonical(unit)
                if key not in unit_indexes:
                    unit_indexes[key] = len(parameter_units)
                    parameter_units.append(unit)
                unit_refs.append(unit_indexes[key])
            output["parameters"] = {"names": parameters["names"], "unit_refs": unit_refs}
        rows = output["constraints"]
        if rows is None:
            continue
        for row in rows:
            row[2] = status_indexes[row[2]]
            source_ref = row[6]
            if source_ref is not None:
                if source_ref not in source_indexes:
                    source_indexes[source_ref] = len(source_refs)
                    source_refs.append(source_ref)
                row[6] = source_indexes[source_ref]
    return {
        "schema_version": SUMMARY_SCHEMA,
        "summary_status": "completed",
        "scope": SCOPE,
        "full_report": full_report,
        "engine_calculation_status": engine_status,
        "scientific_viability": "not_assessed",
        "engine_plan_totals": engine_totals,
        "selection": selected,
        "source_refs": source_refs,
        "parameter_units": parameter_units,
        "plans": [plan["output"] for plan in summarized],
        "diagnostics": {
            "max_chars": max_chars,
            "keep_representatives": keep_representatives,
            "compact_mode": ("reviewer", "failure_groups", "essential")[compact_mode],
            "selection_policy": "feasible first, then failed/undetermined; no inferred ranking",
            "cap_policy": "fold only representatives; never clip IDs, unknowns or blockers",
            "representative_scope": "representative counts are not scenario counts; see coverage/constraints",
            "no_expression_execution": True,
        },
    }


def refusal(full_report, required_chars, max_chars):
    return {
        "schema_version": SUMMARY_SCHEMA,
        "summary_status": "refused",
        "scope": {
            "mode": "compact_only_refusal",
            "not_assessed": ["scientific_validity", "comprehensive_evidence"],
            "human_review": "the linked full report remains authoritative",
        },
        "full_report": full_report,
        "reason": {
            "code": "essential_constraint_or_blocker_details_exceed_cap",
            "message": "refusing to discard essential IDs, violations, blockers, unknown reasons or source diagnostics",
            "essential_mode_chars": required_chars,
            "max_chars": max_chars,
            "action": "increase --max-chars or inspect full_report as Human Review",
        },
    }


def duplicate_reject(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("duplicate JSON key: " + key)
        result[key] = value
    return result


def nonfinite_reject(token):
    raise ConfigError("non-finite JSON literal is forbidden: " + token)


def emit_error(exc, full_report=None):
    kind = "schema" if isinstance(exc, SchemaError) else "io" if isinstance(exc, (OSError, UnicodeError)) else "configuration"
    error = {
        "schema_version": SUMMARY_SCHEMA,
        "summary_status": "error",
        "error": {"kind": kind, "type": type(exc).__name__, "message": str(exc)},
    }
    if full_report is not None:
        error["full_report"] = full_report
    sys.stderr.write(dump_json(error))


def main(argv=None):
    parser = ParserCLI(description=__doc__.splitlines()[0])
    parser.add_argument("input")
    parser.add_argument("--output", default=None)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--keep-representatives", type=int, default=DEFAULT_REPRESENTATIVES)
    full_report = None
    try:
        args = parser.parse_args(argv)
        if args.max_chars < 1:
            raise ConfigError("max-chars must be a positive integer")
        if not 0 <= args.keep_representatives <= MAX_REPRESENTATIVES:
            raise ConfigError("keep-representatives must be between 0 and 20")
        input_path = os.path.abspath(args.input)
        if args.output and (
            os.path.abspath(args.output) == input_path
            or (os.path.exists(args.output) and os.path.samefile(args.output, input_path))
        ):
            raise ConfigError("output must not overwrite the read-only input report")
        with open(input_path, "rb") as handle:
            raw = handle.read()
        full_report = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "path": input_path,
            "bytes": len(raw),
        }
        text = raw.decode("utf-8")
        document = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=nonfinite_reject,
            object_pairs_hook=duplicate_reject,
        )
        rendered = ""
        for compact_mode in (0, 1, 2):
            summary = build_summary(document, full_report, args.keep_representatives, compact_mode, args.max_chars)
            rendered = dump_json(summary)
            if len(rendered) <= args.max_chars:
                break
        if len(rendered) > args.max_chars:
            refused = refusal(full_report, len(rendered), args.max_chars)
            rendered = dump_json(refused)
            if len(rendered) > args.max_chars:
                raise ConfigError(
                    "max-chars %d is too small for a structured refusal linked to the full report; need at least %d"
                    % (args.max_chars, len(rendered))
                )
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
        else:
            sys.stdout.write(rendered)
        return 0
    except (ConfigError, SchemaError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        emit_error(exc, full_report)
        return 2


if __name__ == "__main__":
    sys.exit(main())
