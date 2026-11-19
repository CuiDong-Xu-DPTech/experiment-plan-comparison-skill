#!/usr/bin/env python3
"""Dynamic output-contract bootstrap, JSON Patch checkpointing, and validation.

Uses only Python 3.9+ standard-library modules. The input contract, not this
script, defines the result structure. Mechanical validity is not a scientific
quality or completeness judgment.
"""
import argparse
import copy
import datetime
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

VERSION = "1.0.0"
MISSING = object()


class AuditError(ValueError):
    pass


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise AuditError("duplicate JSON key: " + key)
        obj[key] = value
    return obj


def load_json(path):
    if not Path(path).is_file():
        raise AuditError("JSON file not found: " + str(path))

    def constant(value):
        raise AuditError("non-finite JSON number: " + value)

    with open(path, encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=unique_object,
                         parse_constant=constant)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2,
                  allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    with open(temp, encoding="utf-8") as handle:
        json.load(handle, object_pairs_hook=unique_object)
    os.replace(str(temp), str(path))


def digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            equal(a, b) for a, b in zip(left, right))
    return left == right


def pointer_parts(pointer, allow_root=False):
    if pointer == "" and allow_root:
        return []
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise AuditError("JSON Pointer must start with '/': " + str(pointer))
    parts = []
    for part in pointer[1:].split("/"):
        if "~" in part:
            part = part.replace("~1", "/").replace("~0", "~")
            if "~" in part:
                raise AuditError("invalid JSON Pointer escape: " + pointer)
        parts.append(part)
    return parts


def get_at(document, pointer):
    value = document
    for part in pointer_parts(pointer, True):
        if isinstance(value, dict):
            if part not in value:
                raise AuditError("path not found: " + pointer)
            value = value[part]
        elif isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", part):
                raise AuditError("invalid array index: " + pointer)
            index = int(part)
            if index >= len(value):
                raise AuditError("array index out of range: " + pointer)
            value = value[index]
        else:
            raise AuditError("cannot descend scalar: " + pointer)
    return value


def set_at(document, pointer, value, replace=False, remove=False):
    parts = pointer_parts(pointer)
    parent = get_at(document, "/" + pointer.rsplit("/", 1)[0].lstrip("/")) \
        if len(parts) > 1 else document
    key = parts[-1]
    if isinstance(parent, dict):
        if remove or replace:
            if key not in parent:
                raise AuditError("path not found: " + pointer)
        if remove:
            del parent[key]
        else:
            parent[key] = value
    elif isinstance(parent, list):
        if remove or replace:
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise AuditError("invalid array index: " + pointer)
            index = int(key)
            if index >= len(parent):
                raise AuditError("array index out of range: " + pointer)
            if remove:
                parent.pop(index)
            else:
                parent[index] = value
        elif key == "-":
            parent.append(value)
        elif re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) <= len(parent):
            parent.insert(int(key), value)
        else:
            raise AuditError("invalid array insertion: " + pointer)
    else:
        raise AuditError("cannot modify scalar parent: " + pointer)


def apply_patch(document, operations):
    if not isinstance(operations, list):
        raise AuditError("patch must be a JSON array")
    result = copy.deepcopy(document)
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or operation.get("op") not in (
                "add", "remove", "replace", "move", "copy", "test"):
            raise AuditError("invalid patch operation at index " + str(index))
        if "path" not in operation:
            raise AuditError("missing patch path at index " + str(index))
        op, path = operation["op"], operation["path"]
        prefix = "patch[" + str(index) + "]: "
        try:
            if op in ("add", "replace", "test") and "value" not in operation:
                raise AuditError("operation requires a value")
            if op == "test":
                if not equal(get_at(result, path), operation["value"]):
                    raise AuditError("patch test failed")
            elif op == "remove":
                set_at(result, path, None, remove=True)
            else:
                value = copy.deepcopy(operation["value"]) if op in (
                    "add", "replace") else copy.deepcopy(
                        get_at(result, operation.get("from", "")))
                if op == "move":
                    if path.startswith(operation["from"] + "/"):
                        raise AuditError("cannot move a location into itself")
                    set_at(result, operation["from"], None, remove=True)
                set_at(result, path, value, replace=op == "replace")
        except AuditError as error:
            raise AuditError(prefix + str(error)) from None
    return result


def schema_of(contract):
    if not isinstance(contract, dict):
        return None
    for key in ("json_schema", "schema"):
        if isinstance(contract.get(key), dict) and (
                "properties" in contract[key] or "type" in contract[key]):
            return contract[key]
    if "properties" in contract or contract.get("type") in (
            "object", "array", "string", "number", "integer", "boolean", "null"):
        schema = copy.deepcopy(contract)
        schema.pop("description", None)
        schema.pop("title", None)
        if "properties" in schema:
            schema.setdefault("type", "object")
            required = schema.get("required")
            schema["required"] = list(schema["properties"]) if not isinstance(
                required, list) else required
        return schema
    field_map = contract.get("fields") if isinstance(contract.get("fields"), dict) else contract
    if field_map and all(isinstance(value, dict) for value in field_map.values()):
        properties = {}
        for name, descriptor in field_map.items():
            if not isinstance(name, str) or name.startswith("$"):
                return None
            if "schema" in descriptor and isinstance(descriptor["schema"], dict):
                properties[name] = descriptor["schema"]
            else:
                allowed = {"type", "enum", "const", "items", "required",
                           "properties", "additionalProperties", "default",
                           "minimum", "maximum", "minItems", "minLength"}
                properties[name] = {key: value for key, value in descriptor.items()
                                     if key in allowed}
        return {"type": "object", "properties": properties,
                "required": list(properties),
                "additionalProperties": contract.get("additionalProperties", True)}
    return None


def required_fields(contract):
    if isinstance(contract, list):
        return contract, False
    if isinstance(contract, dict) and isinstance(contract.get("fields"), list):
        return contract["fields"], all(isinstance(entry, dict) for entry in contract["fields"])
    return None, False


def scalar_field(entry):
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, dict):
        name = entry.get("name", entry.get("field", entry.get("key")))
        if isinstance(name, str):
            return name, entry
    raise AuditError("unrecognized output_contract field: " + repr(entry))


def field_type(descriptor):
    kind = descriptor.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "null")
    if not kind:
        for dtype, name in ((str, "string"), (bool, "boolean"),
                            (int, "integer"), (list, "array"), (dict, "object")):
            if isinstance(descriptor.get("value"), dtype):
                return name
    return kind


def initial_value(name, schema, case_id, location="contract"):
    if not isinstance(schema, dict):
        raise AuditError("non-object schema at " + location)
    if "const" in schema:
        value = copy.deepcopy(schema["const"])
    elif "default" in schema:
        value = copy.deepcopy(schema["default"])
    else:
        kind = field_type(schema)
        if kind == "object" or "properties" in schema:
            properties = schema.get("properties", {})
            required = set(schema.get("required", properties.keys()))
            value = {key: initial_value(key, subschema, case_id,
                                        location + "." + key)
                     for key, subschema in properties.items() if key in required}
        elif kind == "array":
            value = []
        elif kind == "null" or schema.get("nullable") is True or (
                isinstance(schema.get("type"), list) and "null" in schema["type"]):
            value = None
        elif kind == "string":
            value = ""
            if name.lower() in ("status", "run_status", "state"):
                allowed = schema.get("enum", [])
                for candidate in ("partial", "in_progress", "pending", "processing"):
                    if candidate in allowed:
                        value = candidate
                        break
        else:
            raise AuditError(
                "no honest default for required field '" + location +
                "'; populate it from the case or change its schema to nullable")
    if name == "case_id":
        if "enum" in schema and case_id not in schema["enum"]:
            raise AuditError("case_id is not allowed by its enum")
        if not equal(value, case_id) and ("const" in schema or "default" in schema):
            if "const" in schema:
                raise AuditError("case_id const differs from the actual case")
        value = copy.deepcopy(case_id)
    if "enum" in schema and value not in schema["enum"]:
        raise AuditError("no honest enum default at " + location)
    return value


def make_bootstrap(case, explicit_fields=None):
    if not isinstance(case, dict):
        raise AuditError("case.json must be an object")
    if "case_id" not in case:
        raise AuditError("case.json lacks required case_id")
    if explicit_fields is not None:
        return {name: initial_value(name, descriptor, case["case_id"], name)
                for name, descriptor in explicit_fields}
    if "output_contract" not in case:
        raise AuditError("no output_contract found; supply --contract with parsed task fields")
    contract = case["output_contract"]
    schema = schema_of(contract)
    if schema is not None:
        return initial_value("result", schema, case["case_id"])
    fields, typed = required_fields(contract)
    if fields is None:
        raise AuditError("unrecognized output_contract; supply a JSON Schema or field list")
    result = {}
    for entry in fields:
        name, descriptor = scalar_field(entry)
        if not typed:
            result[name] = copy.deepcopy(case["case_id"]) if name == "case_id" else None
        else:
            result[name] = initial_value(name, descriptor, case["case_id"], name)
    return result


def resolve_ref(document, reference):
    if not isinstance(reference, str) or not reference.startswith("#"):
        raise AuditError("only local schema references are supported: " + str(reference))
    return get_at(document, reference[1:])


def valid_type(value, kind):
    return {"null": lambda: value is None,
            "boolean": lambda: isinstance(value, bool),
            "object": lambda: isinstance(value, dict),
            "array": lambda: isinstance(value, list),
            "string": lambda: isinstance(value, str),
            "number": lambda: isinstance(value, (int, float)) and
                              not isinstance(value, bool) and math.isfinite(value),
            "integer": lambda: ((isinstance(value, int) and not isinstance(value, bool)) or
                                (isinstance(value, float) and value.is_integer()))}.get(
                                    kind, lambda: True)()


def validation_errors(value, schema, root_schema, path=""):
    errors = []
    if not isinstance(schema, dict):
        return [{"path": path, "message": "schema is not an object"}]
    if "$ref" in schema:
        try:
            return validation_errors(value, resolve_ref(root_schema, schema["$ref"]),
                                     root_schema, path)
        except AuditError as error:
            return [{"path": path, "message": str(error)}]
    if "anyOf" in schema or "oneOf" in schema:
        keyword = "anyOf" if "anyOf" in schema else "oneOf"
        matches = sum(not validation_errors(value, branch, root_schema, path)
                      for branch in schema.get(keyword, []))
        if (keyword == "anyOf" and not matches) or (keyword == "oneOf" and matches != 1):
            errors.append({"path": path, "message": keyword + " alternatives do not match uniquely"})
        return errors
    if "allOf" in schema:
        for branch in schema["allOf"]:
            errors.extend(validation_errors(value, branch, root_schema, path))
    if isinstance(schema.get("type"), list):
        if not any(valid_type(value, kind) for kind in schema["type"]):
            errors.append({"path": path, "message": "type does not match " + str(schema["type"])})
            return errors
    elif "type" in schema and not valid_type(value, schema["type"]):
        errors.append({"path": path, "message": "expected " + schema["type"]})
        return errors
    if schema.get("nullable") is False and value is None:
        errors.append({"path": path, "message": "null is not allowed"})
    if "const" in schema and not equal(value, schema["const"]):
        errors.append({"path": path, "message": "value differs from const"})
    if "enum" in schema and not any(equal(value, option) for option in schema["enum"]):
        errors.append({"path": path, "message": "value is not in enum"})
    if isinstance(value, dict):
        required = schema.get("required", schema.get("properties", {}).keys())
        for name in required:
            if name not in value:
                errors.append({"path": path + "/" + str(name).replace("~", "~0").replace("/", "~1"),
                               "message": "required output_contract field is missing"})
        for key, child in value.items():
            child_path = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in schema.get("properties", {}):
                errors.extend(validation_errors(child, schema["properties"][key],
                                                root_schema, child_path))
            elif "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    errors.append({"path": child_path, "message": "additional field is forbidden"})
                elif isinstance(extra, dict):
                    errors.extend(validation_errors(child, extra, root_schema, child_path))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append({"path": path, "message": "too few array items"})
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append({"path": path, "message": "too many array items"})
        if schema.get("uniqueItems") is True:
            for index in range(len(value)):
                for previous in range(index):
                    if equal(value[index], value[previous]):
                        errors.append({"path": path + "/" + str(index),
                                       "message": "duplicate array item"})
        if isinstance(schema.get("items"), dict):
            for index, child in enumerate(value):
                errors.extend(validation_errors(child, schema["items"],
                                                root_schema, path + "/" + str(index)))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append({"path": path, "message": "string is shorter than minLength"})
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append({"path": path, "message": "string does not match pattern"})
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append({"path": path, "message": "number is below minimum"})
        if "maximum" in schema and value > schema["maximum"]:
            errors.append({"path": path, "message": "number exceeds maximum"})
    return errors


def validate_result(case, result, explicit_fields=None):
    errors = []
    warnings = []
    schema = None
    fields = None
    typed = explicit_fields is not None
    if explicit_fields is not None:
        fields = explicit_fields
    elif isinstance(case, dict) and "output_contract" in case:
        schema = schema_of(case["output_contract"])
        fields, typed = required_fields(case["output_contract"])
    if typed and schema is None:
        schema = {"type": "object", "properties": dict(fields),
                  "required": [name for name, _ in fields]}
    if schema:
        errors.extend(validation_errors(result, schema, schema))
    elif fields is not None:
        if not isinstance(result, dict):
            errors.append({"path": "", "message": "result is not an object"})
        else:
            names = [scalar_field(entry)[0] for entry in fields]
            for name in names:
                if name not in result:
                    errors.append({"path": "/" + name, "message": "required field is missing"})
                elif result[name] is None:
                    warnings.append({"path": "/" + name, "message": "untyped contract field still has a partial placeholder"})
    else:
        raise AuditError("output_contract is unsupported; export its JSON Schema explicitly")
    if not isinstance(result, dict) or "case_id" not in result:
        errors.append({"path": "/case_id", "message": "case_id is missing"})
    elif not equal(result["case_id"], case["case_id"]):
        errors.append({"path": "/case_id", "message": "case_id differs from original input"})
    return {"tool": "contract_audit.py", "tool_version": VERSION,
            "validation_scope": "mechanical schema, field presence, and case identity; not scientific completeness",
            "valid": not errors, "errors": errors, "warnings": warnings}


def field_arguments(raw):
    if raw is None:
        return None
    if isinstance(raw, list):
        return [scalar_field(entry) for entry in raw]
    if isinstance(raw, dict):
        return list(raw.items())
    raise AuditError("--fields-json expects an object of names to descriptors or a list")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("bootstrap", "validate", "patch"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--case", required=True, help="actual case.json")
        sub.add_argument("--result", required=True)
        sub.add_argument("--contract", help="separate JSON Schema or contract object")
        sub.add_argument("--fields-json", help="Python-generated JSON string, not a Python literal")
        sub.add_argument("--work-dir", required=True)
        if command == "patch":
            sub.add_argument("--patch", required=True, help="RFC 6902 JSON Patch array file")
            sub.add_argument("--reason", required=True)
            sub.add_argument("--source-refs", default="", help="comma-separated reviewed source IDs")
    args = parser.parse_args(argv)
    try:
        case = load_json(args.case)
        if not isinstance(case, dict) or "case_id" not in case:
            raise AuditError("input case must be an object with case_id")
        contract = load_json(args.contract) if args.contract else case.get("output_contract")
        if args.contract and args.command == "bootstrap":
            boot_case = dict(case, output_contract=contract)
        else:
            boot_case = case
        fields = field_arguments(json.loads(args.fields_json, object_pairs_hook=unique_object)) \
            if args.fields_json else None
        result_path = Path(args.result)
        work_dir = Path(args.work_dir)
        if args.command == "bootstrap":
            result = make_bootstrap(boot_case, fields)
            report = validate_result(boot_case, result, fields)
            state = {"schema_version": "contract-audit-state/v1", "tool_version": VERSION,
                     "case_path": str(Path(args.case).resolve()), "case_sha256": digest(args.case),
                     "case_id": case["case_id"], "created_at": utc_now(), "changes": [],
                     "bootstrap_mechanically_valid": report["valid"], "bootstrap_errors": report["errors"]}
            atomic_json(result_path, result)
            atomic_json(work_dir / "contract-state.json", state)
            atomic_json(work_dir / "validation.json", report)
            print(json.dumps({"result": str(result_path), "state": str(work_dir / "contract-state.json"),
                              "mechanically_valid": report["valid"],
                              "errors": report["errors"]}, ensure_ascii=False, indent=2))
            return 0
        result = load_json(args.result)
        validate_case = dict(case, output_contract=contract) if args.contract else case
        if args.command == "validate":
            report = validate_result(validate_case, result, fields)
            atomic_json(work_dir / "validation.json", report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["valid"] else 1
        state = load_json(work_dir / "contract-state.json")
        if state.get("case_sha256") != digest(args.case):
            raise AuditError("case input changed since bootstrap; refusing to mix versions")
        patch_document = load_json(args.patch)
        metadata = {}
        if isinstance(patch_document, dict):
            metadata = {key: patch_document[key] for key in ("reason", "source_refs")
                        if key in patch_document}
            patch_document = patch_document.get("patch")
        if "case_id" in result:
            for operation in patch_document:
                if isinstance(operation, dict) and operation.get("path") == "/case_id" and \
                        operation.get("op") in ("remove", "replace"):
                    raise AuditError("case_id is immutable")
        updated = apply_patch(result, patch_document)
        report = validate_result(validate_case, updated, fields)
        if not report["valid"]:
            atomic_json(work_dir / "validation.json", report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        before_hash = hashlib.sha256(json.dumps(result, ensure_ascii=False,
                                                sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode("utf-8")).hexdigest()
        atomic_json(result_path, updated)
        after_hash = hashlib.sha256(json.dumps(updated, ensure_ascii=False,
                                               sort_keys=True, separators=(",", ":"),
                                               allow_nan=False).encode("utf-8")).hexdigest()
        state.setdefault("changes", []).append({
            "change_id": "change-%04d" % (len(state.get("changes", [])) + 1),
            "timestamp": utc_now(), "actor": "contract_audit.py/" + VERSION,
            "reason": args.reason, "source_refs": metadata.get("source_refs",
                [part.strip() for part in args.source_refs.split(",") if part.strip()]),
            "patch": patch_document, "before_result_sha256": before_hash,
            "after_result_sha256": after_hash})
        atomic_json(work_dir / "contract-state.json", state)
        atomic_json(work_dir / "validation.json", report)
        print(json.dumps({"result": str(result_path), "valid": True,
                          "change_count": len(state["changes"]), "warnings": report["warnings"]},
                         ensure_ascii=False, indent=2))
        return 0
    except (AuditError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
