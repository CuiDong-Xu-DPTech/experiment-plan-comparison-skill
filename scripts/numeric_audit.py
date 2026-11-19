#!/usr/bin/env python3
"""Deterministic numeric audit, Python 3.9 stdlib only.
CLI: python3 numeric_audit.py INPUT.json [--output report.json]
     [--max-combinations N] (default 200000; omitted/empty output means stdout).

numeric-audit/v1 input: {schema_version, plans:[{plan_id, version, parameters,
joint_constraints, cost_items, schedule, hard_constraints, soft_metrics,
preference}]}. Parameter: {name, unit, domain, allowed_domains:[{constraint_id,
values, source_ref?}]}. Joint/hard constraint: {constraint_id, expr, source_ref?}.
Cost: {item_id, category, quantity, unit_price, events?, fixed_cost?, currency,
quantity_unit, price_unit, recurrence?, fixedness?, source_ref?}. Schedule:
{tasks:[{task_id, duration_hours, predecessors, person_hours?, machine_hours?}],
resource_contention_known}. Metric: {metric_id, expr, direction}. Preference:
{priorities:[{metric_id, priority, direction}]}. Numbers are JSON numbers,
decimal strings, or expression strings; explicit null components are unknown.

Expressions permit decimal/scientific literals, names, True/False, + - * /,
< <= > >= == !=, and/or/not, unary +/- and parentheses. Chained comparisons and
parenthesized tuples (equality only) are supported. Calls, attributes, indexing,
other containers, exponentiation and unknown names are forbidden. No eval/exec
or binary float: numeric source spans become Decimal then exact Fraction.
Boolean logic is three-valued and short-circuiting; bool differs from numbers.
Domain values are intersected with every allowed_domains set. Exhaustive product
enumeration checks joint/hard constraints and schedule; exceeding the limit is
undetermined with zero sampling, never silent truncation. Reports retain at most
20 representatives per collection without changing counts or optimization.

Cost = events*quantity*unit_price+fixed_cost, regardless of recurrence. Missing
components are not zero; units must agree when both supplied; currencies are
never converted or cross-summed. item_id must be an identifier; a category gets
cost_CATEGORY only when it is an identifier. Cost components may use parameters,
elapsed_hours, person_hours_total and machine_hours_total, but not other costs.
Task fields use parameters; task hours may additionally use duration_hours.
Absent optional person/machine fields contribute zero; explicit null is unknown.
Duration/person/machine hours must be nonnegative (real zero remains valid).
Hard constraints/metrics also receive cost_ITEM/category/total values. Missing
source_ref remains an uncertainty. resource_contention_known=true is a case
declaration that DAG parallelism has no resource contention; otherwise only a
forced-serial chain yields elapsed_hours. An unproven parallel DAG exposes only a
precedence lower bound and unknown elapsed_hours. No absolute booking is claimed.
Empty cost/task sets are complete; incomplete resource ledgers block determinate
optimization without automatically making evaluated hard constraints infeasible.

Shared priorities give exact lexicographic rank; equal priority retains array
order. Pareto uses only numeric comparisons and never auto-selects a unique winner;
an Agent may separately justify choosing a singleton front. Ties/missing values/
unknown scenarios/incomplete resource ledgers block determinate optimization.
Exit 0 covers completed vacuous/infeasible/undetermined/per-plan-error audits;
configuration and IO failures emit structured JSON on stderr and exit 2.
"""
import argparse, heapq, itertools, json, math, re, sys
from decimal import Decimal, InvalidOperation
from fractions import Fraction
SCHEMA = "numeric-audit/v1"
MAX_REPRESENTATIVES = 20
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NUMBER_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
RESERVED = {"cost_total", "elapsed_hours", "person_hours_total", "machine_hours_total"}
class ConfigError(Exception): pass
class PlanError(Exception): pass
class ValueProblem(Exception): pass
class Unknown: pass
UNKNOWN = Unknown()
EXPR_CACHE = {}
def text_number(value):
    if value.denominator == 1: return str(value.numerator)
    denominator, twos, fives = value.denominator, 0, 0
    while denominator % 2 == 0:
        denominator //= 2; twos += 1
    while denominator % 5 == 0:
        denominator //= 5; fives += 1
    if denominator != 1: return "%d/%d" % (value.numerator, value.denominator)
    scale = max(twos, fives)
    digits = str(abs(value.numerator) * (10 ** scale // value.denominator))
    if scale:
        digits = digits.rjust(scale + 1, "0")
        digits = (digits[:-scale] + "." + digits[-scale:]).rstrip("0").rstrip(".")
    return ("-" if value.numerator < 0 else "") + digits
def clean(value):
    if isinstance(value, Fraction): return text_number(value)
    if isinstance(value, Decimal): return text_number(Fraction(value))
    if isinstance(value, Unknown): return None
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    return value
def emit(stream, obj):
    stream.write(json.dumps(clean(obj), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")
def reject_constant(token): raise ConfigError("non-finite JSON literal is forbidden: " + token)
def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ConfigError("duplicate JSON key: " + key)
        result[key] = value
    return result
def decimal_fraction(token):
    try: number = Decimal(token)
    except InvalidOperation: raise PlanError("invalid decimal literal: " + token)
    if not number.is_finite(): raise PlanError("numeric literal must be finite")
    return Fraction(number)
def tokens(source):
    result, pos = [], 0
    while pos < len(source):
        if source[pos].isspace(): pos += 1; continue
        if source.startswith("**", pos): raise PlanError("exponentiation is not supported")
        pair = next((op for op in ("<=", ">=", "==", "!=") if source.startswith(op, pos)), None)
        if pair:
            result.append(("op", pair, pair)); pos += 2; continue
        match = NUMBER_RE.match(source, pos)
        if match:
            token = match.group(0)
            result.append(("number", token, token)); pos = match.end(); continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", source[pos:])
        if match:
            token = match.group(0)
            result.append(("name", token, token)); pos += len(token); continue
        char = source[pos]
        if char not in "+-*/<>(),": raise PlanError("forbidden token at character %d" % (pos + 1))
        result.append(("op", char, char)); pos += 1
    result.append(("end", "", ""))
    return result
class Parser:
    def __init__(self, source): self.source, self.tokens, self.pos = source, tokens(source), 0
    def current(self): return self.tokens[self.pos]
    def take(self, spelling=None):
        token = self.current()
        if spelling is not None and token[1] != spelling:
            raise PlanError("expected %r in expression %r" % (spelling, self.source))
        self.pos += 1
        return token
    def parse(self):
        if self.current()[0] == "end": raise PlanError("empty expression")
        node = self.parse_or()
        if self.current()[0] != "end" or self.pos != len(self.tokens) - 1:
            raise PlanError("unexpected trailing token %r in %r" % (self.current()[1], self.source))
        return node
    def parse_or(self):
        node = self.parse_and()
        while self.current()[1] == "or":
            self.take(); node = ("or", node, self.parse_and())
        return node
    def parse_and(self):
        node = self.parse_not()
        while self.current()[1] == "and":
            self.take(); node = ("and", node, self.parse_not())
        return node
    def parse_not(self):
        if self.current()[1] == "not":
            self.take(); return ("not", self.parse_not())
        return self.parse_compare()
    def parse_compare(self):
        node, comparisons = self.parse_additive(), []
        while self.current()[1] in ("<", "<=", ">", ">=", "==", "!="):
            op = self.take()[1]; comparisons.append((op, self.parse_additive()))
        return ("compare", node, comparisons) if comparisons else node
    def parse_additive(self):
        node = self.parse_term()
        while self.current()[1] in ("+", "-"):
            op = self.take()[1]; node = (op, node, self.parse_term())
        return node
    def parse_term(self):
        node = self.parse_unary()
        while self.current()[1] in ("*", "/"):
            op = self.take()[1]; node = (op, node, self.parse_unary())
        return node
    def parse_unary(self):
        if self.current()[1] in ("+", "-"): return (self.take()[1] + "u", self.parse_unary())
        return self.parse_atom()
    def parse_atom(self):
        kind, spelling, value = self.take()
        if kind == "number": return ("number", value)
        if kind == "name":
            if spelling in ("True", "False"): return ("literal", spelling == "True")
            if spelling in ("and", "or", "not"): raise PlanError("misplaced boolean operator in %r" % self.source)
            return ("name", spelling)
        if spelling == "(":
            if self.current()[1] == ")": self.take(); return ("tuple", [])
            first = self.parse_or()
            if self.current()[1] != ",": self.take(")"); return first
            items = [first]
            while self.current()[1] == ",":
                self.take()
                if self.current()[1] == ")": break
                items.append(self.parse_or())
            self.take(")")
            return ("tuple", items)
        raise PlanError("expected operand, got %r in %r" % (spelling, self.source))
def node_for(value):
    if isinstance(value, str):
        if value not in EXPR_CACHE: EXPR_CACHE[value] = Parser(value).parse()
        return EXPR_CACHE[value]
    if value is None: return ("literal", UNKNOWN)
    if isinstance(value, bool): return ("literal", value)
    if isinstance(value, Fraction): return ("literal", value)
    if isinstance(value, int): return ("literal", Fraction(value))
    if isinstance(value, Decimal):
        if not value.is_finite(): raise PlanError("numeric literal must be finite")
        return ("literal", Fraction(value))
    raise PlanError("expected a number or expression string, got %s" % type(value).__name__)
def node_names(node):
    names = set()
    def walk(value):
        if isinstance(value, tuple):
            if len(value) == 2 and value[0] == "name": names.add(value[1])
            else:
                for child in value: walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(node)
    return names
def checked_node(value, allowed, label):
    node = node_for(value)
    missing = node_names(node) - set(allowed)
    if missing: raise PlanError("%s uses unknown names: %s" % (label, ", ".join(sorted(missing))))
    return node
def equal_values(left, right):
    if isinstance(left, Fraction) and isinstance(right, Fraction): return left == right
    if isinstance(left, bool) and isinstance(right, bool): return left == right
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(equal_values(a, b) for a, b in zip(left, right))
    if isinstance(left, Unknown) or isinstance(right, Unknown): return UNKNOWN
    return False
def evaluate_node(node, env):
    kind = node[0]
    if kind == "number": return decimal_fraction(node[1])
    if kind == "literal": return node[1]
    if kind == "name":
        if node[1] not in env: raise PlanError("unknown expression name: " + node[1])
        return env[node[1]]
    if kind == "tuple": return tuple(evaluate_node(child, env) for child in node[1])
    if kind in ("+u", "-u"):
        value = evaluate_node(node[1], env)
        if isinstance(value, Unknown): return UNKNOWN
        if not isinstance(value, Fraction): raise ValueProblem("unary numeric operator needs a number")
        return value if kind == "+u" else -value
    if kind in ("+", "-", "*", "/"):
        left, right = evaluate_node(node[1], env), evaluate_node(node[2], env)
        if isinstance(left, Unknown) or isinstance(right, Unknown): return UNKNOWN
        if not isinstance(left, Fraction) or not isinstance(right, Fraction):
            raise ValueProblem("arithmetic needs numeric operands")
        if kind == "/" and right == 0: raise ValueProblem("division by zero")
        return {"+": lambda: left + right, "-": lambda: left - right,
                "*": lambda: left * right, "/": lambda: left / right}[kind]()
    if kind == "compare":
        left, unknown_seen = evaluate_node(node[1], env), False
        for op, right_node in node[2]:
            right = evaluate_node(right_node, env)
            if isinstance(left, Unknown) or isinstance(right, Unknown): result = UNKNOWN
            elif op in ("==", "!="):
                equality = equal_values(left, right)
                result = equality if isinstance(equality, Unknown) else (equality if op == "==" else not equality)
            else:
                if not isinstance(left, Fraction) or not isinstance(right, Fraction):
                    raise ValueProblem("ordered comparison needs numbers")
                result = {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[op]
            if result is False: return False
            unknown_seen = unknown_seen or isinstance(result, Unknown); left = right
        return UNKNOWN if unknown_seen else True
    if kind == "not":
        value = evaluate_node(node[1], env)
        if isinstance(value, Unknown): return UNKNOWN
        if not isinstance(value, bool): raise ValueProblem("not needs a boolean")
        return not value
    if kind in ("and", "or"):
        unknown_seen = False
        for child in node[1:]:
            value = evaluate_node(child, env)
            if isinstance(value, Unknown): unknown_seen = True
            elif not isinstance(value, bool): raise ValueProblem("boolean operator needs booleans")
            elif (kind == "and" and not value) or (kind == "or" and value): return value
        return UNKNOWN if unknown_seen else kind == "and"
    raise PlanError("unsupported expression node")
def evaluated(node, env):
    try: return evaluate_node(node, env), None
    except ValueProblem as exc: return UNKNOWN, str(exc)
def numeric(node, env):
    value, problem = evaluated(node, env)
    if not isinstance(value, (Fraction, Unknown)) or isinstance(value, bool):
        raise PlanError("expression must produce a number or unknown")
    return value, problem
def boolean(node, env):
    value, problem = evaluated(node, env)
    if not isinstance(value, (bool, Unknown)): raise PlanError("constraint must produce a boolean or unknown")
    return value, problem
def scalar(value):
    result, problem = evaluated(node_for(value), {})
    if problem or not isinstance(result, (Fraction, bool)):
        raise PlanError("domain values must be finite numeric or boolean literals" + (": " + problem if problem else ""))
    return result
def typed_key(value): return ("bool", value) if isinstance(value, bool) else ("number", value)
def value_order(value): return (0, value) if isinstance(value, bool) else (1, value)
def mapping(value, label):
    if not isinstance(value, dict): raise PlanError(label + " must be an object")
    return value
def listing(owner, key):
    value = owner.get(key, [])
    if not isinstance(value, list): raise PlanError(key + " must be an array")
    return value
def valid_name(value, label):
    if not isinstance(value, str) or not NAME_RE.match(value): raise PlanError(label + " must be a valid identifier")
    return value
def unique_id(value, seen, label):
    if not isinstance(value, str) or not value or value in seen: raise PlanError("missing or duplicate " + label)
    seen.add(value)
    return value
def source_audit(entry, kind, identifier, uncertainties):
    if not entry.get("source_ref"):
        uncertainties.append({"kind": "missing_source_ref", "owner_kind": kind, "owner_id": identifier})
def cap_add(target, value, coverage, omitted_key):
    if len(target) < MAX_REPRESENTATIVES: target.append(value)
    else: coverage[omitted_key] += 1
def prepare_dag(task_entries):
    tasks, order, ids = [], {}, set()
    for index, raw in enumerate(task_entries):
        raw = mapping(raw, "task")
        task_id = unique_id(raw.get("task_id"), ids, "task_id")
        if "duration_hours" not in raw: raise PlanError("task duration_hours is required (use null for unknown)")
        predecessors = listing(raw, "predecessors")
        if not all(isinstance(x, str) for x in predecessors) or len(set(predecessors)) != len(predecessors):
            raise PlanError("predecessors must contain unique task identifiers")
        order[task_id] = index
        tasks.append({"task_id": task_id, "duration": raw["duration_hours"], "predecessors": predecessors,
                      "person": raw.get("person_hours", Fraction(0)), "machine": raw.get("machine_hours", Fraction(0))})
    edges = {task_id: [] for task_id in order}
    indegree = {task_id: 0 for task_id in order}
    for task in tasks:
        for predecessor in task["predecessors"]:
            if predecessor not in order: raise PlanError("unknown predecessor: " + predecessor)
            edges[predecessor].append(task["task_id"]); indegree[task["task_id"]] += 1
    heap = [(order[task_id], task_id) for task_id, degree in indegree.items() if degree == 0]
    heapq.heapify(heap); topological = []; parallel_placeholder = len(heap) > 1
    while heap:
        _, task_id = heapq.heappop(heap); topological.append(task_id)
        for successor in edges[task_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0: heapq.heappush(heap, (order[successor], successor))
        if len(heap) > 1: parallel_placeholder = True
    if len(topological) != len(tasks):
        raise PlanError("schedule cycle involving: " + ", ".join(sorted(t for t, d in indegree.items() if d)))
    return tasks, topological, parallel_placeholder
def schedule_value(prepared, parameters, resource_known):
    tasks, topological, parallel_placeholder = prepared
    by_id, ends, rows = {task["task_id"]: task for task in tasks}, {}, []
    for task_id in topological:
        task = by_id[task_id]
        predecessor_ends = [ends[p] for p in task["predecessors"]]
        start = UNKNOWN if any(isinstance(x, Unknown) for x in predecessor_ends) else (max(predecessor_ends) if predecessor_ends else Fraction(0))
        duration, issue = numeric(task["duration_node"], parameters)
        if isinstance(duration, Fraction) and duration < 0: raise PlanError("negative duration_hours for task " + task_id)
        end = UNKNOWN if isinstance(start, Unknown) or isinstance(duration, Unknown) else start + duration
        ends[task_id] = end
        local = dict(parameters); local["duration_hours"] = duration
        person, person_issue = numeric(task["person_node"], local)
        machine, machine_issue = numeric(task["machine_node"], local)
        if isinstance(person, Fraction) and person < 0: raise PlanError("negative person_hours for task " + task_id)
        if isinstance(machine, Fraction) and machine < 0: raise PlanError("negative machine_hours for task " + task_id)
        rows.append({"task_id": task_id, "duration_hours": duration, "earliest_start_hours": start,
                     "earliest_end_hours": end, "person_hours": person, "machine_hours": machine,
                     "issues": [x for x in (issue, person_issue, machine_issue) if x]})
    precedence_elapsed = UNKNOWN if any(isinstance(x, Unknown) for x in ends.values()) else (max(ends.values()) if ends else Fraction(0))
    unproven_parallel = parallel_placeholder and not resource_known
    elapsed = UNKNOWN if unproven_parallel else precedence_elapsed
    def hour_total(key):
        values = [row[key] for row in rows]
        return UNKNOWN if any(isinstance(x, Unknown) for x in values) else sum(values, Fraction(0))
    schedule = {"elapsed_hours": elapsed, "person_hours_total": hour_total("person_hours"),
                "machine_hours_total": hour_total("machine_hours")}
    public = dict(schedule)
    complete = not any(isinstance(schedule[k], Unknown) for k in schedule)
    if not tasks:
        contention, timing = "not_applicable_empty_schedule", "empty task set; elapsed_hours is exactly zero"
    elif unproven_parallel:
        contention, timing = "unproven_parallel", "elapsed_hours unknown: DAG parallelism without a no-resource-contention declaration; lower bound is not a hard scheduling guarantee"
    elif parallel_placeholder:
        contention, timing = "case_declared_no_contention", "DAG earliest-start makespan allowed by case declaration; not an absolute booking time"
    else:
        contention, timing = "not_applicable_forced_serial", "forced-serial precedence chain; known task durations sum exactly, not an absolute booking time"
    public.update({"status": "complete" if complete else "incomplete", "method": "precedence DAG only; no calendar slots",
                   "resource_contention_status": contention, "timing_basis": timing,
                   "parallelism_possible": parallel_placeholder, "precedence_elapsed_lower_bound_hours": precedence_elapsed,
                   "tasks": rows})
    return schedule, public
def cost_value(items, base_env):
    env, ledger_items, issues, by_currency, categories = dict(base_env), {}, [], {}, {}
    for item in items:
        values, item_issues = {}, []
        for field in ("events", "quantity", "unit_price", "fixed_cost"):
            values[field], problem = numeric(item[field + "_node"], base_env)
            if problem: item_issues.append(field + ": " + problem)
        unknown_fields = [field for field, value in values.items() if isinstance(value, Unknown)]
        total = UNKNOWN if unknown_fields else values["events"] * values["quantity"] * values["unit_price"] + values["fixed_cost"]
        currency, category = item["currency"], item["category"]
        entry = {"item_id": item["item_id"], "category": category, "currency": currency,
                 "quantity_unit": item["quantity_unit"], "price_unit": item["price_unit"],
                 "recurrence": item["recurrence"], "fixedness": item["fixedness"],
                 "events": values["events"], "quantity": values["quantity"], "unit_price": values["unit_price"],
                 "fixed_cost": values["fixed_cost"], "total": total, "unknown_fields": unknown_fields,
                 "issues": item_issues, "formula": "events * quantity * unit_price + fixed_cost"}
        ledger_items[item["item_id"]] = entry; env["cost_" + item["item_id"]] = total
        if currency is None: issues.append("missing currency for " + item["item_id"])
        else: by_currency.setdefault(currency, []).append(total)
        categories.setdefault(category, []).append((currency, total))
        issues.extend(item["item_id"] + ": " + message for message in item_issues)
    currency_totals = {c: UNKNOWN if any(isinstance(x, Unknown) for x in values) else sum(values, Fraction(0))
                       for c, values in by_currency.items()}
    unknown_present = any(isinstance(entry["total"], Unknown) for entry in ledger_items.values())
    mixed = len(by_currency) > 1
    missing_currency = any(entry["currency"] is None for entry in ledger_items.values())
    if not items: total, status = Fraction(0), "complete"
    elif mixed:
        total, status = UNKNOWN, "mixed_currencies"
        issues.append("cost_total unavailable: multiple currencies; no conversion or cross-currency sum")
    elif missing_currency or unknown_present or not by_currency: total, status = UNKNOWN, "partial"
    else:
        total = next(iter(currency_totals.values()))
        status = "complete" if not isinstance(total, Unknown) else "partial"
    env["cost_total"] = total; category_totals = {}
    for category, entries in categories.items():
        currencies = {entry[0] for entry in entries}; amounts = [entry[1] for entry in entries]
        category_total = UNKNOWN if len(currencies) != 1 or None in currencies or any(isinstance(x, Unknown) for x in amounts) else sum(amounts, Fraction(0))
        category_totals[category] = category_total
        if NAME_RE.match(category): env["cost_" + category] = category_total
    public = {"status": status, "currency": next(iter(by_currency)) if len(by_currency) == 1 and not missing_currency else None,
              "cost_total": total, "currency_totals": currency_totals, "category_totals": category_totals,
              "items": ledger_items, "issues": issues}
    return env, public
def constraint_status(report):
    if report["violation_count"]: return "violated"
    if report["unknown_count"]: return "undetermined"
    if not report["satisfied_count"]: return "vacuous"
    return "satisfied"
def dominates(left, right, directions):
    a = tuple(left["metrics"][m] * (1 if directions[m] == "min" else -1) for m in directions)
    b = tuple(right["metrics"][m] * (1 if directions[m] == "min" else -1) for m in directions)
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))
def pareto_add(front, candidate, directions):
    if any(dominates(old, candidate, directions) for old in front): return
    front[:] = [old for old in front if not dominates(candidate, old, directions)]
    front.append(candidate)
def empty_meta(priority=()):
    return {"priority": list(priority), "best": [], "best_count": 0, "best_key": None,
            "nondominated": [], "blocked": True, "metrics": {}, "directions": {}}
def _audit_plan(raw, index, maximum):
    raw = mapping(raw, "plan")
    plan_id, version = raw.get("plan_id", "plan_%d" % (index + 1)), raw.get("version")
    uncertainties, warnings, reasons = [], [], []
    result = {"plan_id": plan_id, "version": version, "status": "undetermined", "reasons": reasons,
              "warnings": warnings, "uncertainties": uncertainties,
              "coverage": {"state": "complete", "max_combinations": maximum, "scenario_count": 0,
                           "enumerated_count": 0, "joint_admissible_count": 0, "fully_admissible_count": 0,
                           "infeasible_scenario_count": 0, "undetermined_scenario_count": 0,
                           "metric_incomplete_scenario_count": 0, "incomplete_cost_scenario_count": 0,
                           "incomplete_schedule_scenario_count": 0, "unproven_parallel_scenario_count": 0,
                           "all_scenarios_enumerated": True,
                           "all_scenarios_fully_admissible": None, "representative_limit": MAX_REPRESENTATIVES,
                           "feasible_representatives_omitted": 0, "undetermined_representatives_omitted": 0,
                           "violations_omitted": 0},
              "domain_resolution": [], "joint_constraints": [], "hard_constraints": [],
              "cost_audit": {"formula": "events * quantity * unit_price + fixed_cost", "states_seen": [], "items": []},
              "schedule_audit": {"method": "precedence-DAG earliest-start only"},
              "results": {"feasible_representatives": [], "undetermined_representatives": [], "violations": []},
              "optimization": {}}
    coverage = result["coverage"]
    parameters, parameter_names, seen_parameters = {}, [], set()
    for raw_parameter in listing(raw, "parameters"):
        raw_parameter = mapping(raw_parameter, "parameter")
        name = valid_name(raw_parameter.get("name"), "parameter name")
        if name in seen_parameters or name in RESERVED or name == "duration_hours":
            raise PlanError("duplicate or reserved parameter name: " + name)
        seen_parameters.add(name)
        domain, keys, duplicates = [], set(), 0
        for value in listing(raw_parameter, "domain"):
            converted = scalar(value)
            if typed_key(converted) in keys: duplicates += 1
            else: keys.add(typed_key(converted)); domain.append(converted)
        domain.sort(key=value_order); applied = []
        for allowed in listing(raw_parameter, "allowed_domains"):
            allowed = mapping(allowed, "allowed_domains entry"); constraint_id = allowed.get("constraint_id")
            source_audit(allowed, "allowed_domain", constraint_id, uncertainties)
            allowed_keys = {typed_key(scalar(value)) for value in listing(allowed, "values")}
            domain = [value for value in domain if typed_key(value) in allowed_keys]
            applied.append(constraint_id)
        parameters[name] = domain; parameter_names.append(name)
        result["domain_resolution"].append({"name": name, "unit": raw_parameter.get("unit"), "values": domain,
                                             "duplicates_removed": duplicates, "allowed_constraints": applied, "empty": not domain})
    total = math.prod((len(parameters[name]) for name in parameter_names), start=1)
    coverage["scenario_count"] = total; joint, joint_ids = [], set()
    for constraint in listing(raw, "joint_constraints"):
        constraint = mapping(constraint, "joint constraint")
        constraint_id = unique_id(constraint.get("constraint_id"), joint_ids, "joint constraint_id")
        source_audit(constraint, "joint_constraint", constraint_id, uncertainties)
        node = checked_node(constraint.get("expr"), parameter_names, "joint constraint " + constraint_id)
        report = {"constraint_id": constraint_id, "expr": constraint.get("expr"), "source_ref": constraint.get("source_ref"),
                  "satisfied_count": 0, "violation_count": 0, "unknown_count": 0}
        joint.append({**report, "node": node}); result["joint_constraints"].append(report)
    schedule_raw = mapping(raw.get("schedule", {}), "schedule")
    resource_known = schedule_raw.get("resource_contention_known", False)
    if not isinstance(resource_known, bool): raise PlanError("resource_contention_known must be boolean")
    prepared = prepare_dag(listing(schedule_raw, "tasks"))
    for task in prepared[0]:
        task["duration_node"] = checked_node(task["duration"], parameter_names, "task duration")
        allowed_hours = parameter_names + ["duration_hours"]
        task["person_node"] = checked_node(task["person"], allowed_hours, "task person_hours")
        task["machine_node"] = checked_node(task["machine"], allowed_hours, "task machine_hours")
    if not resource_known: warnings.append("elapsed_hours is not a slot/resource-contention-proven schedule")
    schedule_names = ["elapsed_hours", "person_hours_total", "machine_hours_total"]
    items, item_ids, item_aliases, category_aliases = [], set(), set(), set()
    for raw_item in listing(raw, "cost_items"):
        raw_item = mapping(raw_item, "cost_item"); item_id = valid_name(raw_item.get("item_id"), "cost item_id")
        if item_id in item_ids or "cost_" + item_id in RESERVED: raise PlanError("duplicate or reserved cost item_id: " + item_id)
        item_ids.add(item_id); item_aliases.add("cost_" + item_id)
        category = raw_item.get("category")
        if not isinstance(category, str) or not category: raise PlanError("cost category must be a nonempty string")
        if "cost_" + category in RESERVED: raise PlanError("reserved cost category: " + category)
        if NAME_RE.match(category): category_aliases.add("cost_" + category)
        else: warnings.append("category alias unavailable for non-identifier category: " + category)
        if "quantity" not in raw_item or "unit_price" not in raw_item:
            raise PlanError("quantity and unit_price are required (use explicit null for unknown)")
        quantity_unit, price_unit = raw_item.get("quantity_unit"), raw_item.get("price_unit")
        if quantity_unit is not None and price_unit is not None and quantity_unit != price_unit:
            raise PlanError("quantity_unit/price_unit mismatch for " + item_id)
        if quantity_unit is None or price_unit is None: warnings.append("unit match not fully proven for " + item_id)
        currency = raw_item.get("currency")
        if currency is not None and (not isinstance(currency, str) or not currency):
            raise PlanError("currency must be a nonempty string or null")
        source_audit(raw_item, "cost_item", item_id, uncertainties)
        item = {"item_id": item_id, "category": category, "currency": currency,
                "quantity_unit": quantity_unit, "price_unit": price_unit,
                "recurrence": raw_item.get("recurrence", "recurring"), "fixedness": raw_item.get("fixedness")}
        for field, default in (("events", Fraction(1)), ("quantity", None), ("unit_price", None), ("fixed_cost", Fraction(0))):
            item[field] = raw_item.get(field, default)
        items.append(item); result["cost_audit"]["items"].append(dict(item))
    aliases = item_aliases | category_aliases
    collisions = (set(parameter_names) & aliases) | (item_aliases & category_aliases)
    if collisions: raise PlanError("parameter/item/category environment collisions: " + ", ".join(sorted(collisions)))
    for item in items:
        for field in ("events", "quantity", "unit_price", "fixed_cost"):
            item[field + "_node"] = checked_node(item[field], parameter_names + schedule_names, item["item_id"] + " " + field)
    expression_names = parameter_names + schedule_names + sorted(aliases) + ["cost_total"]
    hard, hard_ids = [], set()
    for constraint in listing(raw, "hard_constraints"):
        constraint = mapping(constraint, "hard constraint")
        constraint_id = unique_id(constraint.get("constraint_id"), hard_ids, "hard constraint_id")
        source_audit(constraint, "hard_constraint", constraint_id, uncertainties)
        node = checked_node(constraint.get("expr"), expression_names, "hard constraint " + constraint_id)
        report = {"constraint_id": constraint_id, "expr": constraint.get("expr"), "source_ref": constraint.get("source_ref"),
                  "satisfied_count": 0, "violation_count": 0, "unknown_count": 0}
        hard.append({**report, "node": node}); result["hard_constraints"].append(report)
    metrics, metric_ids, directions = [], set(), {}
    for metric in listing(raw, "soft_metrics"):
        metric = mapping(metric, "soft metric")
        metric_id = unique_id(metric.get("metric_id"), metric_ids, "metric_id")
        direction = metric.get("direction", "min")
        if direction not in ("min", "max"): raise PlanError("metric direction must be min or max")
        node = checked_node(metric.get("expr"), expression_names, "soft metric " + metric_id)
        metrics.append({"metric_id": metric_id, "expr": metric.get("expr"), "direction": direction, "node": node})
        directions[metric_id] = direction
    preference = mapping(raw.get("preference", {}), "preference")
    priorities, priority_metrics = [], set()
    for position, entry in enumerate(listing(preference, "priorities")):
        entry = mapping(entry, "priority"); metric_id, priority = entry.get("metric_id"), entry.get("priority")
        if metric_id not in directions or metric_id in priority_metrics:
            raise PlanError("priority metric is undefined or duplicated: " + str(metric_id))
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            raise PlanError("priority must be a nonnegative integer")
        priority_metrics.add(metric_id)
        priorities.append({"metric_id": metric_id, "priority": priority,
                           "direction": entry.get("direction", directions[metric_id]), "position": position})
        if priorities[-1]["direction"] not in ("min", "max"): raise PlanError("priority direction must be min or max")
    priorities.sort(key=lambda entry: (entry["priority"], entry["position"]))
    priority_spec = [(entry["metric_id"], entry["priority"], entry["direction"]) for entry in priorities]
    meta = empty_meta(priority_spec)
    meta.update({"metrics": directions, "directions": directions, "blocked": False})
    rank_directions = {e["metric_id"]: e["direction"] for e in priorities} if priorities else directions
    best_key = None
    if total > maximum:
        coverage.update(state="limit_exceeded", all_scenarios_enumerated=False)
        result["status"] = "undetermined"
        reasons.append({"code": "max_combinations_exceeded", "required": total, "limit": maximum,
                        "message": "no scenarios sampled; no complete robustness or optimization claim"})
        meta["blocked"] = True
    elif total == 0:
        result["status"] = "infeasible"
        reasons.append({"code": "empty_parameter_domain", "message": "base/allowed domain intersection is empty"})
    else:
        cost_states = set()
        for scenario_index, chosen in enumerate(itertools.product(*(parameters[name] for name in parameter_names))):
            selected = dict(zip(parameter_names, chosen)); coverage["enumerated_count"] += 1
            joint_values, joint_unknown = [], False
            for position, constraint in enumerate(joint):
                value, _ = boolean(constraint["node"], selected); report = result["joint_constraints"][position]
                if value is False:
                    report["violation_count"] += 1
                    violation = {"kind": "joint", "constraint_id": constraint["constraint_id"],
                                 "scenario_index": scenario_index, "values": selected}
                    cap_add(result["results"]["violations"], violation, coverage, "violations_omitted")
                elif isinstance(value, Unknown): report["unknown_count"] += 1; joint_unknown = True
                else: report["satisfied_count"] += 1
                joint_values.append(value)
            if any(value is False for value in joint_values):
                coverage["infeasible_scenario_count"] += 1; continue
            coverage["joint_admissible_count"] += 1
            schedule_env, schedule_public = schedule_value(prepared, selected, resource_known)
            base_env = dict(selected); base_env.update(schedule_env)
            cost_env, ledger = cost_value(items, base_env); cost_states.add(ledger["status"])
            env = dict(cost_env); hard_values, hard_unknown = [], False
            for position, constraint in enumerate(hard):
                value, _ = boolean(constraint["node"], env); report = result["hard_constraints"][position]
                if value is False:
                    report["violation_count"] += 1
                    violation = {"kind": "hard", "constraint_id": constraint["constraint_id"],
                                 "scenario_index": scenario_index, "values": selected, "costs": ledger, "schedule": schedule_public}
                    cap_add(result["results"]["violations"], violation, coverage, "violations_omitted")
                elif isinstance(value, Unknown): report["unknown_count"] += 1; hard_unknown = True
                else: report["satisfied_count"] += 1
                hard_values.append(value)
            if any(value is False for value in hard_values):
                coverage["infeasible_scenario_count"] += 1; continue
            if joint_unknown or hard_unknown:
                coverage["undetermined_scenario_count"] += 1
                unknown = {"scenario_index": scenario_index, "values": selected, "joint_unknown": joint_unknown,
                           "hard_unknown": hard_unknown, "costs": ledger, "schedule": schedule_public}
                cap_add(result["results"]["undetermined_representatives"], unknown, coverage, "undetermined_representatives_omitted")
                continue
            coverage["fully_admissible_count"] += 1; metric_values, metric_issues = {}, []
            if ledger["status"] != "complete": coverage["incomplete_cost_scenario_count"] += 1
            if schedule_public["status"] != "complete": coverage["incomplete_schedule_scenario_count"] += 1
            if schedule_public["resource_contention_status"] == "unproven_parallel":
                coverage["unproven_parallel_scenario_count"] += 1
            for metric in metrics:
                value, problem = numeric(metric["node"], env); metric_values[metric["metric_id"]] = value
                if isinstance(value, Unknown):
                    metric_issues.append({"metric_id": metric["metric_id"], "reason": problem or "value unavailable or non-finite"})
            if metric_issues: coverage["metric_incomplete_scenario_count"] += 1
            candidate = {"plan_id": plan_id, "version": version, "scenario_index": scenario_index,
                         "values": selected, "metrics": metric_values, "metric_issues": metric_issues,
                         "costs": ledger, "schedule": schedule_public}
            cap_add(result["results"]["feasible_representatives"], candidate, coverage, "feasible_representatives_omitted")
            if priorities:
                if metric_issues or any(not isinstance(metric_values[e["metric_id"]], Fraction) for e in priorities): continue
                key = tuple(metric_values[e["metric_id"]] * (1 if e["direction"] == "min" else -1) for e in priorities)
                if best_key is None or key < best_key: best_key, meta["best"], meta["best_count"] = key, [candidate], 1
                elif key == best_key:
                    meta["best_count"] += 1
                    if len(meta["best"]) < MAX_REPRESENTATIVES: meta["best"].append(candidate)
            elif metrics and not metric_issues: pareto_add(meta["nondominated"], candidate, rank_directions)
        result["cost_audit"]["states_seen"] = sorted(cost_states)
        if coverage["fully_admissible_count"]: result["status"] = "feasible"
        elif coverage["undetermined_scenario_count"]:
            result["status"] = "undetermined"
            reasons.append({"code": "unknown_constraint_values", "count": coverage["undetermined_scenario_count"]})
        else:
            result["status"] = "infeasible"; reasons.append({"code": "all_scenarios_violate_constraints", "count": total})
    for report in result["joint_constraints"] + result["hard_constraints"]: report["status"] = constraint_status(report)
    coverage["all_scenarios_fully_admissible"] = (coverage["fully_admissible_count"] == total and total > 0) if total <= maximum and not coverage["undetermined_scenario_count"] else None
    blocking = []
    if not coverage["all_scenarios_enumerated"]: blocking.append("enumeration_incomplete")
    if coverage["undetermined_scenario_count"]: blocking.append("undetermined_constraints")
    if coverage["metric_incomplete_scenario_count"]: blocking.append("missing_metric_values")
    for field, code in (("incomplete_cost_scenario_count", "incomplete_cost_data"),
                        ("incomplete_schedule_scenario_count", "incomplete_schedule_data"),
                        ("unproven_parallel_scenario_count", "unproven_parallel_schedule")):
        if coverage[field]:
            blocking.append(code)
            reasons.append({"code": code, "count": coverage[field],
                            "effect": "status reflects evaluated hard constraints; determinate optimization is blocked"})
    meta["blocked"] = bool(blocking)
    if priorities:
        meta["best_key"] = best_key
        optimization = {"mode": "lexicographic_priority", "priorities": priority_spec,
                        "best_candidate_count": meta["best_count"], "blocking_reasons": blocking, "representatives": meta["best"]}
        if blocking: optimization["state"] = "undetermined"
        elif not meta["best_count"]: optimization["state"] = "no_feasible_tuple"
        elif meta["best_count"] == 1: optimization["state"] = "unique_best"
        else: optimization["state"] = "tie"
    else:
        optimization = {"mode": "pareto", "directions": directions, "nondominated_count": len(meta["nondominated"]),
                        "nondominated_representatives": meta["nondominated"][:MAX_REPRESENTATIVES],
                        "blocking_reasons": blocking, "unique_winner": None,
                        "state": "no_criteria" if not metrics else ("partial" if blocking else "complete")}
    result["optimization"] = optimization
    return result, meta
def audit_plan(raw, index, maximum):
    try: return _audit_plan(raw, index, maximum)
    except PlanError as exc:
        plan_id = raw.get("plan_id", "plan_%d" % (index + 1)) if isinstance(raw, dict) else "plan_%d" % (index + 1)
        result = {"plan_id": plan_id, "version": raw.get("version") if isinstance(raw, dict) else None,
                  "status": "error", "reasons": [{"code": "invalid_plan", "message": str(exc)}],
                  "warnings": [], "uncertainties": [], "coverage": {"scenario_count": 0, "all_scenarios_enumerated": False},
                  "results": {"feasible_representatives": [], "undetermined_representatives": [], "violations": []},
                  "optimization": {"state": "error"}}
        return result, empty_meta()
    except Exception as exc:
        plan_id = raw.get("plan_id", "plan_%d" % (index + 1)) if isinstance(raw, dict) else "plan_%d" % (index + 1)
        result = {"plan_id": plan_id, "status": "error", "version": raw.get("version") if isinstance(raw, dict) else None,
                  "reasons": [{"code": "plan_execution_error", "message": "%s: %s" % (type(exc).__name__, exc)}],
                  "optimization": {"state": "error"}}
        return result, empty_meta()
def global_selection(entries):
    if not entries:
        return {"state": "no_plans", "unique_winner": None, "message": "no plans: feasibility and optimality are not vacuously claimed"}
    metas = [meta for _, meta in entries]
    if any(result["status"] == "error" for result, _ in entries):
        return {"state": "undetermined", "unique_winner": None, "reason": "one or more plans are invalid or failed"}
    specs = [tuple(meta["priority"]) for meta in metas]
    if all(specs) and all(spec == specs[0] for spec in specs):
        spec = specs[0]
        directions = {metric_id: direction for metric_id, _, direction in spec}
        best_key, best_count, reps = None, 0, []
        for meta in metas:
            key = meta["best_key"]
            if key is None: continue
            if best_key is None or key < best_key: best_key, best_count, reps = key, meta["best_count"], list(meta["best"])
            elif key == best_key: best_count += meta["best_count"]; reps.extend(meta["best"])
        blocking = ["scenario_or_metric_unknown"] if any(meta["blocked"] for meta in metas) else []
        selection = {"mode": "lexicographic_priority", "priorities": list(spec), "directions": directions,
                     "best_candidate_count": best_count, "representatives": reps[:MAX_REPRESENTATIVES],
                     "unique_winner": None, "blocking_reasons": blocking}
        if blocking: selection["state"] = "undetermined"
        elif not best_count: selection["state"] = "no_feasible_tuple"
        elif best_count == 1: selection["state"] = "unique_best"; selection["unique_winner"] = reps[0]
        else: selection["state"] = "tie"
        return selection
    if any(specs):
        return {"state": "undetermined", "unique_winner": None, "reason": "plans have mixed or inconsistent priorities; no shared ranking assumed"}
    directions = {}
    for meta in metas:
        for metric_id, direction in meta["directions"].items():
            if metric_id in directions and directions[metric_id] != direction:
                return {"state": "undetermined", "unique_winner": None, "reason": "conflicting metric directions across plans"}
            directions[metric_id] = direction
    if not directions: return {"state": "no_criteria", "unique_winner": None, "nondominated": []}
    front, excluded = [], 0
    for meta in metas:
        for candidate in meta["nondominated"]:
            if any(metric not in candidate["metrics"] or isinstance(candidate["metrics"][metric], Unknown) for metric in directions): excluded += 1
            else: pareto_add(front, candidate, directions)
    blocked = any(meta["blocked"] for meta in metas) or excluded > 0
    return {"mode": "pareto", "directions": directions, "state": "partial" if blocked else "complete",
            "nondominated_count": len(front), "nondominated_representatives": front[:MAX_REPRESENTATIVES],
            "representatives_omitted": max(0, len(front) - MAX_REPRESENTATIVES),
            "excluded_for_missing_metrics": excluded, "unique_winner": None,
            "message": "Pareto comparison only; no unique winner is selected"}
class ParserCLI(argparse.ArgumentParser):
    def error(self, message): raise ConfigError(message)
def main(argv=None):
    parser = ParserCLI(description=__doc__.splitlines()[0])
    parser.add_argument("input"); parser.add_argument("--output", default=None)
    parser.add_argument("--max-combinations", type=int, default=200000)
    try:
        args = parser.parse_args(argv)
        if args.max_combinations < 1 or args.max_combinations > 10000000:
            raise ConfigError("max-combinations must be between 1 and 10000000 (adjust main limit deliberately)")
        with open(args.input, "r", encoding="utf-8") as handle:
            document = json.load(handle, parse_float=Decimal, parse_int=int,
                                 parse_constant=reject_constant, object_pairs_hook=no_duplicates)
        if not isinstance(document, dict) or document.get("schema_version") != SCHEMA:
            raise ConfigError("top-level schema_version must be numeric-audit/v1")
        if "plans" not in document or not isinstance(document["plans"], list): raise ConfigError("top-level plans must be an array")
        entries = [audit_plan(plan, index, args.max_combinations) for index, plan in enumerate(document["plans"])]
        reports = [result for result, _ in entries]; statuses = [report["status"] for report in reports]
        report = {"schema_version": SCHEMA, "status": "completed", "plans": reports,
                  "summary": {"plan_count": len(reports), "feasible": statuses.count("feasible"),
                              "infeasible": statuses.count("infeasible"), "undetermined": statuses.count("undetermined"),
                              "error": statuses.count("error"),
                              "all_plans_feasible": all(status == "feasible" for status in statuses) if statuses else None},
                  "selection": global_selection(entries)}
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle: emit(handle, report)
        else: emit(sys.stdout, report)
        return 0
    except (ConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        emit(sys.stderr, {"schema_version": SCHEMA, "status": "error",
                          "error": {"kind": type(exc).__name__, "message": str(exc)}})
        return 2
if __name__ == "__main__": sys.exit(main())
