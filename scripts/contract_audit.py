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

VERSION = "1.4.0"
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


DESCRIPTIVE_KEYS = {
    "note": ("note", "always"),
    "schema_style": ("schema_style", "always"),
    "constraint": ("constraint", "always"),
    "rule": ("rule", "always"),
    "required_content": ("required_content", "always"),
    "required_content_when_nonnull": ("required_content", "value is not null")
}
CONTRACT_METADATA_KEYS = ("format", "required_path", "result_file")


def json_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def pointer_token(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def map_alias(schema, alias, canonical, path, conversions):
    if alias not in schema:
        return
    value = schema.pop(alias)
    action = "mapped"
    if canonical in schema:
        if not equal(schema[canonical], value):
            raise AuditError("conflicting " + alias + " and " + canonical +
                             " at " + (path or "/"))
        action = "matched_existing_canonical"
    else:
        schema[canonical] = copy.deepcopy(value)
    conversions.append({"path": path + "/" + pointer_token(alias),
                        "from": alias, "to": canonical, "action": action,
                        "value": copy.deepcopy(value)})


def extract_descriptive(schema, path, semantics):
    for keyword, (kind, applies_when) in DESCRIPTIVE_KEYS.items():
        if keyword not in schema:
            continue
        value = schema.pop(keyword)
        semantics.append({
            "path": path + "/" + pointer_token(keyword),
            "keyword": keyword, "kind": kind, "value": copy.deepcopy(value),
            "applies_when": applies_when, "mechanically_validated": False})


def descriptor_schema(descriptor, path, conversions, semantics):
    if not isinstance(descriptor, dict):
        raise AuditError("field descriptor must be an object at " + path)
    if "schema" in descriptor:
        if not isinstance(descriptor["schema"], dict):
            raise AuditError("descriptor schema must be an object at " + path)
        unsupported_siblings = sorted(set(descriptor) - {"schema"} - set(DESCRIPTIVE_KEYS))
        if unsupported_siblings:
            raise AuditError("descriptor schema cannot be combined with sibling keys at " +
                             path + ": " + ", ".join(unsupported_siblings))
        descriptive_wrapper = copy.deepcopy(descriptor)
        extract_descriptive(descriptive_wrapper, path, semantics)
        conversions.append({"path": path + "/schema", "from": "schema",
                            "to": "field schema", "action": "unwrapped"})
        schema = copy.deepcopy(descriptor["schema"])
    else:
        schema = copy.deepcopy(descriptor)
    map_alias(schema, "allowed_values", "enum", path, conversions)
    map_alias(schema, "allowed", "enum", path, conversions)
    map_alias(schema, "required_exact_value", "const", path, conversions)
    map_alias(schema, "must_equal", "const", path, conversions)
    extract_descriptive(schema, path, semantics)
    return schema


def split_required(descriptor, path, conversions):
    if not isinstance(descriptor, dict) or "required" not in descriptor:
        return descriptor, None
    stripped = copy.deepcopy(descriptor)
    required = stripped.pop("required")
    if not isinstance(required, bool):
        raise AuditError("field required must be a boolean at " + path + "/required")
    conversions.append({"path": path + "/required", "from": "required",
                        "to": "root required membership",
                        "action": "required_field" if required else "optional_field",
                        "value": required})
    return stripped, required


def extract_contract_metadata(schema, semantics):
    for keyword in [key for key in schema if key in CONTRACT_METADATA_KEYS]:
        value = schema.pop(keyword)
        semantics.append({"path": "/" + pointer_token(keyword),
                          "keyword": keyword, "kind": "contract_metadata",
                          "value": copy.deepcopy(value),
                          "applies_when": "contract processing",
                          "mechanically_validated": False})


def has_boolean_required_descriptor(contract):
    if not isinstance(contract, dict) or not isinstance(contract.get("properties"), dict):
        return False
    return any(isinstance(descriptor, dict) and
               isinstance(descriptor.get("required"), bool)
               for descriptor in contract["properties"].values())


def validate_required_names(required):
    if not isinstance(required, list) or \
            not all(isinstance(name, str) for name in required) or \
            len(set(required)) != len(required):
        raise AuditError("output_contract required fields must be an array of unique names")


def adaptation(source_form, contract, schema, conversions, semantics):
    return {
        "adapter": "descriptive-output-contract/v1",
        "source_form": source_form,
        "source_contract_sha256": json_digest(contract),
        "normalized_schema_sha256": json_digest(schema) if schema is not None else None,
        "normalized_schema": schema,
        "conversions": conversions,
        "semantic_constraints": semantics,
        "semantic_coverage": "recorded only; descriptive and conditional content requirements are not mechanically validated",
        "unknown_keywords": "retained in normalized_schema for explicit unsupported-keyword validation errors"
    }


def adapt_contract(contract):
    if not isinstance(contract, dict):
        return None, adaptation("unsupported", contract, None, [], [])
    for key in ("json_schema", "schema"):
        if isinstance(contract.get(key), dict) and (
                "properties" in contract[key] or "type" in contract[key]):
            if has_boolean_required_descriptor(contract[key]):
                schema, inner = adapt_contract(contract[key])
                conversions = inner["conversions"]
                semantics = inner["semantic_constraints"]
            else:
                schema = copy.deepcopy(contract[key])
                conversions = []
                semantics = []
            extras = {name: copy.deepcopy(value) for name, value in contract.items() if name != key}
            extract_descriptive(extras, "", semantics)
            for annotation in ("description", "title"):
                if annotation not in extras:
                    continue
                value = extras.pop(annotation)
                if annotation in schema:
                    if not equal(schema[annotation], value):
                        raise AuditError("conflicting wrapper and schema " + annotation)
                    action = "matched_existing_canonical"
                else:
                    schema[annotation] = value
                    action = "mapped"
                conversions.append({"path": "/" + annotation,
                                    "from": "wrapper " + annotation, "to": annotation,
                                    "action": action, "value": copy.deepcopy(value)})
            if extras:
                raise AuditError("unrecognized keys alongside canonical " + key + ": " +
                                 ", ".join(sorted(extras)))
            return schema, adaptation("canonical_json_schema", contract, schema,
                                      conversions, semantics)

    field_keys = [key for key in ("fields", "top_level_fields", "properties") if key in contract]
    free_requirements = "field_requirements" in contract
    if "required_fields" in contract or \
            any(key != "properties" for key in field_keys) or free_requirements or \
            "additional_top_level_fields_allowed" in contract or \
            "additional_top_level_fields" in contract or \
            has_boolean_required_descriptor(contract):
        if len(field_keys) > 1:
            raise AuditError("output_contract must use only one of fields, top_level_fields, properties")
        if free_requirements and field_keys:
            raise AuditError("field_requirements cannot be combined with another field container")
        field_key = field_keys[0] if field_keys else None
        field_map = contract[field_key] if field_key else None
        if not free_requirements and not isinstance(field_map, (dict, list)):
            raise AuditError("descriptive output_contract requires fields or properties")
        conversions = []
        semantics = []
        excluded = set(field_keys) | {"field_requirements"}
        schema = {key: copy.deepcopy(value) for key, value in contract.items()
                  if key not in excluded}
        extract_descriptive(schema, "", semantics)
        extract_contract_metadata(schema, semantics)
        properties = {}
        required_flags = {}
        if free_requirements:
            if not isinstance(contract["field_requirements"], dict):
                raise AuditError("field_requirements must be an object of descriptive strings")
            for name, requirement in contract["field_requirements"].items():
                if not isinstance(name, str) or not name or name.startswith("$"):
                    raise AuditError("invalid output_contract field name: " + repr(name))
                if not isinstance(requirement, str):
                    raise AuditError("field_requirements values must be descriptive strings at /field_requirements/" + pointer_token(name))
                properties[name] = {}
                semantics.append({"path": "/field_requirements/" + pointer_token(name),
                                  "keyword": "field_requirements", "kind": "required_content",
                                  "value": copy.deepcopy(requirement), "applies_when": "always",
                                  "mechanically_validated": False})
            required_hint = contract.get("required_fields", contract.get("required"))
            if required_hint is not None:
                validate_required_names(required_hint)
                for name in required_hint:
                    if name not in properties:
                        properties[name] = {}
                        conversions.append({"path": "/required_fields", "from": "required field name",
                                            "to": "properties/" + pointer_token(name),
                                            "action": "added_untyped_required_property", "field": name})
            conversions.append({"path": "/field_requirements", "from": "field_requirements",
                                "to": "properties", "action": "mapped_free_text_requirements",
                                "field_count": len(contract["field_requirements"]),
                                "property_count": len(properties)})
        elif isinstance(field_map, dict):
            field_path = "/" + field_key
            for name, original_descriptor in field_map.items():
                if not isinstance(name, str) or not name or name.startswith("$"):
                    raise AuditError("invalid output_contract field name: " + repr(name))
                path = field_path + "/" + pointer_token(name)
                descriptor, required_flag = split_required(original_descriptor, path, conversions)
                if required_flag is not None:
                    required_flags[name] = required_flag
                properties[name] = descriptor_schema(descriptor, path, conversions, semantics)
            conversions.append({"path": field_path, "from": field_key, "to": "properties",
                                "action": "mapped_descriptor_map",
                                "field_count": len(properties)})
        else:
            field_path = "/" + field_key
            for index, entry in enumerate(field_map):
                path = field_path + "/" + str(index)
                if isinstance(entry, str):
                    name, descriptor = entry, {}
                elif isinstance(entry, dict):
                    identities = [entry[key] for key in ("name", "field", "key") if key in entry]
                    if not identities or not isinstance(identities[0], str) or not identities[0] or \
                            any(not equal(identity, identities[0]) for identity in identities[1:]):
                        raise AuditError("field list entry needs one consistent name at " + path)
                    name = identities[0]
                    descriptor = {key: copy.deepcopy(value) for key, value in entry.items()
                                  if key not in {"name", "field", "key"}}
                else:
                    raise AuditError("field list entry must be a string or object at " + path)
                if name.startswith("$") or name in properties:
                    raise AuditError("invalid or duplicate output_contract field name: " + repr(name))
                descriptor, required_flag = split_required(descriptor, path, conversions)
                if required_flag is not None:
                    required_flags[name] = required_flag
                properties[name] = descriptor_schema(descriptor, path, conversions, semantics)
                conversions.append({"path": path, "from": "field list entry",
                                    "to": "properties/" + pointer_token(name),
                                    "action": "mapped_field_identity"})
            conversions.append({"path": field_path, "from": field_key, "to": "properties",
                                "action": "mapped_descriptor_list",
                                "field_count": len(properties)})
        schema.pop("properties", None)
        schema.setdefault("type", "object")
        schema["properties"] = properties
        map_alias(schema, "required_fields", "required", "", conversions)
        required = schema.get("required")
        flagged_required = [name for name in properties if required_flags.get(name) is True]
        if required is None:
            schema["required"] = flagged_required if required_flags else list(properties)
        else:
            validate_required_names(required)
        if required_flags and set(schema["required"]) != set(flagged_required):
            raise AuditError("field required booleans conflict with root required array")
        missing = [name for name in schema["required"] if name not in properties]
        if missing:
            raise AuditError("required fields lack descriptors: " + ", ".join(missing))
        map_alias(schema, "additional_top_level_fields_allowed", "additionalProperties",
                  "", conversions)
        map_alias(schema, "additional_top_level_fields", "additionalProperties",
                  "", conversions)
        schema.setdefault("additionalProperties", True)
        source_form = "named_field_requirements" if free_requirements else "descriptive_fields"
        return schema, adaptation(source_form, contract, schema, conversions, semantics)

    if "properties" in contract or contract.get("type") in (
            "object", "array", "string", "number", "integer", "boolean", "null"):
        schema = copy.deepcopy(contract)
        if "properties" in schema:
            schema.setdefault("type", "object")
            required = schema.get("required")
            if required is not None and not isinstance(required, list):
                raise AuditError("schema required must be an array of field names")
            schema["required"] = list(schema["properties"]) if required is None else required
        return schema, adaptation("canonical_json_schema", contract, schema, [], [])

    if contract and all(isinstance(value, dict) for value in contract.values()):
        conversions = []
        semantics = []
        properties = {}
        required_flags = {}
        for name, original_descriptor in contract.items():
            if not isinstance(name, str) or name.startswith("$"):
                return None, adaptation("unsupported", contract, None, [], [])
            path = "/" + pointer_token(name)
            descriptor, required_flag = split_required(original_descriptor, path, conversions)
            if required_flag is not None:
                required_flags[name] = required_flag
            properties[name] = descriptor_schema(descriptor, path, conversions, semantics)
        required = [name for name in properties if required_flags.get(name) is True] \
            if required_flags else list(properties)
        schema = {"type": "object", "properties": properties,
                  "required": required, "additionalProperties": True}
        return schema, adaptation("typed_field_map", contract, schema, conversions, semantics)
    return None, adaptation("unsupported", contract, None, [], [])


def schema_of(contract):
    return adapt_contract(contract)[0]


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
        if not schema:
            value = None
        elif kind == "null" or schema.get("nullable") is True or (
                isinstance(schema.get("type"), list) and "null" in schema["type"]):
            value = None
        elif kind == "object" or "properties" in schema:
            properties = schema.get("properties", {})
            required = set(schema.get("required", properties.keys()))
            value = {key: initial_value(key, subschema, case_id,
                                        location + "." + key)
                     for key, subschema in properties.items() if key in required}
        elif kind == "array":
            value = []
        elif kind == "string" or (kind is None and
                name.lower() in ("status", "run_status", "state") and
                isinstance(schema.get("enum"), list)):
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
                                    kind, lambda: False)()


SCHEMA_KEYS = {
    "$schema", "$id", "$ref", "$defs", "definitions", "$comment",
    "type", "enum", "const", "properties", "required", "additionalProperties",
    "items", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength",
    "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "minProperties", "maxProperties", "anyOf", "oneOf",
    "allOf", "default", "title", "description", "examples", "nullable"
}


def validation_errors(value, schema, root_schema, path=""):
    errors = []
    if not isinstance(schema, dict):
        return [{"path": path, "message": "schema is not an object"}]
    unsupported = sorted(set(schema) - SCHEMA_KEYS)
    if unsupported:
        return [{"path": path, "message": "unsupported schema keywords; make their semantics explicit before claiming validity: " + ", ".join(unsupported)}]
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
        if not schema["type"] or not all(isinstance(kind, str) for kind in schema["type"]):
            raise AuditError("schema type array must contain strings")
        if not any(valid_type(value, kind) for kind in schema["type"]):
            errors.append({"path": path, "message": "type does not match " + str(schema["type"])})
            return errors
    elif "type" in schema and not valid_type(value, schema["type"]):
        errors.append({"path": path, "message": "expected " + str(schema["type"])})
        return errors
    if schema.get("nullable") is False and value is None:
        errors.append({"path": path, "message": "null is not allowed"})
    if "const" in schema and not equal(value, schema["const"]):
        errors.append({"path": path, "message": "value differs from const"})
    if "enum" in schema and not any(equal(value, option) for option in schema["enum"]):
        errors.append({"path": path, "message": "value is not in enum"})
    if isinstance(value, dict):
        for keyword in ("required", "minProperties", "maxProperties"):
            if keyword == "required" and keyword in schema and (
                    not isinstance(schema[keyword], list) or
                    not all(isinstance(name, str) for name in schema[keyword]) or
                    len(set(schema[keyword])) != len(schema[keyword])):
                raise AuditError("required must be an array of unique field names")
            elif keyword != "required" and keyword in schema and (
                    not isinstance(schema[keyword], int) or isinstance(schema[keyword], bool) or
                    schema[keyword] < 0):
                raise AuditError(keyword + " must be a nonnegative integer")
        required = schema.get("required", schema.get("properties", {}).keys())
        for name in required:
            if name not in value:
                errors.append({"path": path + "/" + str(name).replace("~", "~0").replace("/", "~1"),
                               "message": "required output_contract field is missing"})
        for keyword in ("minProperties", "maxProperties"):
            if keyword in schema and (
                    (keyword == "minProperties" and len(value) < schema[keyword]) or
                    (keyword == "maxProperties" and len(value) > schema[keyword])):
                errors.append({"path": path, "message": "object violates " + keyword})
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
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append({"path": path, "message": "string exceeds maxLength"})
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append({"path": path, "message": "string does not match pattern"})
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append({"path": path, "message": "number is below minimum"})
        if "maximum" in schema and value > schema["maximum"]:
            errors.append({"path": path, "message": "number exceeds maximum"})
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append({"path": path, "message": "number is below or equal to exclusiveMinimum"})
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append({"path": path, "message": "number exceeds or equals exclusiveMaximum"})
        if "multipleOf" in schema:
            from decimal import Decimal, InvalidOperation
            try:
                factor = Decimal(str(schema["multipleOf"]))
                if not factor.is_finite() or factor <= 0:
                    raise AuditError("multipleOf must be a positive finite number")
                quotient = Decimal(str(value)) / factor
                if quotient != quotient.to_integral_value():
                    errors.append({"path": path, "message": "number is not a multipleOf value"})
            except (InvalidOperation, ZeroDivisionError, ValueError):
                raise AuditError("multipleOf must be a positive finite number")
    return errors


def validate_result(case, result, explicit_fields=None):
    errors = []
    warnings = []
    schema = None
    fields = None
    metadata = None
    typed = explicit_fields is not None
    if explicit_fields is not None:
        fields = explicit_fields
    elif isinstance(case, dict) and "output_contract" in case:
        schema, metadata = adapt_contract(case["output_contract"])
        fields, typed = required_fields(case["output_contract"])
    if typed and schema is None:
        schema = {"type": "object", "properties": dict(fields),
                  "required": [name for name, _ in fields]}
        metadata = adaptation("explicit_fields", dict(fields), schema, [], [])
    if schema is not None:
        errors.extend(validation_errors(result, schema, schema))
        if isinstance(result, dict):
            for name, subschema in schema.get("properties", {}).items():
                if not subschema and result.get(name, MISSING) is None:
                    warnings.append({"path": "/" + pointer_token(name),
                                     "message": "untyped contract field has an unknown partial placeholder"})
    elif fields is not None:
        if not isinstance(result, dict):
            errors.append({"path": "", "message": "result is not an object"})
        else:
            names = [scalar_field(entry)[0] for entry in fields]
            for name in names:
                if name not in result:
                    errors.append({"path": "/" + name, "message": "required field is missing"})
                elif result[name] is None:
                    warnings.append({"path": "/" + name, "message": "untyped contract field has an unknown partial placeholder"})
    else:
        raise AuditError("output_contract is unsupported; export its JSON Schema explicitly")
    if not isinstance(result, dict) or "case_id" not in result:
        errors.append({"path": "/case_id", "message": "case_id is missing"})
    elif not equal(result["case_id"], case["case_id"]):
        errors.append({"path": "/case_id", "message": "case_id differs from original input"})
    if metadata and metadata["semantic_constraints"]:
        warnings.append({"path": "", "message": str(len(metadata["semantic_constraints"])) +
                         " descriptive contract requirements are recorded but not mechanically validated"})
    return {"tool": "contract_audit.py", "tool_version": VERSION,
            "validation_scope": "mechanical schema, field presence, and case identity; descriptive semantic coverage is recorded, not scientifically validated",
            "valid": not errors, "errors": errors, "warnings": warnings,
            "contract_adaptation": metadata}


def write_contract_artifacts(work_dir, report):
    metadata = report.get("contract_adaptation")
    if not metadata or metadata.get("normalized_schema") is None:
        return {}
    normalized_path = Path(work_dir) / "normalized_contract.json"
    semantic_path = Path(work_dir) / "semantic-coverage.json"
    atomic_json(normalized_path, metadata["normalized_schema"])
    atomic_json(semantic_path, {
        "schema_version": "contract-semantic-coverage/v1",
        "tool_version": VERSION,
        "source_contract_sha256": metadata["source_contract_sha256"],
        "normalized_schema_sha256": metadata["normalized_schema_sha256"],
        "status": "recorded_not_mechanically_validated",
        "constraints": metadata["semantic_constraints"]})
    return {"normalized_contract_path": str(normalized_path.resolve()),
            "semantic_coverage_path": str(semantic_path.resolve())}


def brief_validation_report(report, full_report_path, artifact_paths):
    metadata = report.get("contract_adaptation")
    constraints = metadata.get("semantic_constraints", []) if metadata else []
    brief = {"tool": report["tool"], "tool_version": report["tool_version"],
             "validation_scope": report["validation_scope"],
             "valid": report["valid"],
             "error_count": len(report["errors"]),
             "errors": copy.deepcopy(report["errors"]),
             "warning_count": len(report["warnings"]),
             "warnings": copy.deepcopy(report["warnings"]),
             "semantic_requirements_not_mechanically_validated": sum(
                 not item.get("mechanically_validated", False) for item in constraints),
             "full_contract_adaptation_present": metadata is not None,
             "full_report_path": str(Path(full_report_path).resolve())}
    if metadata:
        for key in ("source_contract_sha256", "normalized_schema_sha256"):
            if metadata.get(key) is not None:
                brief[key] = metadata[key]
    brief.update(artifact_paths)
    return brief


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
    subparsers.choices["validate"].add_argument(
        "--format", choices=("full", "brief"), default="full",
        help="stdout format; validation.json always retains the complete report")
    args = parser.parse_args(argv)
    try:
        case = load_json(args.case)
        if not isinstance(case, dict) or "case_id" not in case:
            raise AuditError("input case must be an object with case_id")
        result_path = Path(args.result)
        work_dir = Path(args.work_dir)
        state_path = work_dir / "contract-state.json"
        if args.command == "bootstrap" and (result_path.exists() or state_path.exists()):
            raise AuditError("existing result or state: bootstrap refuses overwrite; read the existing checkpoint, patch it, or use fresh result/work paths")
        contract = load_json(args.contract) if args.contract else case.get("output_contract")
        boot_case = dict(case, output_contract=contract) if args.contract else case
        fields = field_arguments(json.loads(args.fields_json, object_pairs_hook=unique_object)) \
            if args.fields_json else None
        if args.command == "bootstrap":
            result = make_bootstrap(boot_case, fields)
            report = validate_result(boot_case, result, fields)
            state = {"schema_version": "contract-audit-state/v1", "tool_version": VERSION,
                     "case_path": str(Path(args.case).resolve()), "case_sha256": digest(args.case),
                     "case_id": case["case_id"], "created_at": utc_now(), "changes": [],
                     "bootstrap_mechanically_valid": report["valid"], "bootstrap_errors": report["errors"],
                     "contract_adaptation": report["contract_adaptation"]}
            if args.contract:
                state.update(contract_path=str(Path(args.contract).resolve()),
                             contract_sha256=digest(args.contract),
                             contract_provenance={"kind": "external",
                                 "path": str(Path(args.contract).resolve()),
                                 "sha256": digest(args.contract)})
            elif contract is not None:
                state.update(contract_pointer="/output_contract",
                             contract_sha256=json_digest(contract),
                             contract_provenance={"kind": "embedded",
                                 "case_path": str(Path(args.case).resolve()),
                                 "pointer": "/output_contract",
                                 "sha256": json_digest(contract)})
            atomic_json(result_path, result)
            write_contract_artifacts(work_dir, report)
            atomic_json(state_path, state)
            atomic_json(work_dir / "validation.json", report)
            print(json.dumps({"result": str(result_path), "state": str(state_path),
                              "mechanically_valid": report["valid"],
                              "errors": report["errors"]}, ensure_ascii=False, indent=2))
            return 0
        state = load_json(state_path) if state_path.is_file() else None
        if state is not None:
            if state.get("case_sha256") != digest(args.case):
                raise AuditError("case input changed since bootstrap; refusing to mix versions")
            saved_contract = state.get("contract_path")
            provenance = state.get("contract_provenance", {})
            if not args.contract and provenance.get("kind") == "embedded" and (
                    "output_contract" not in case or
                    json_digest(case["output_contract"]) != provenance.get("sha256") or
                    json_digest(case["output_contract"]) != state.get("contract_sha256")):
                raise AuditError("embedded output_contract differs from bootstrap; refusing to mix versions")
            if args.contract:
                if str(Path(args.contract).resolve()) != saved_contract or \
                        digest(args.contract) != state.get("contract_sha256"):
                    raise AuditError("external contract differs from bootstrap; use fresh work/result paths")
            elif "output_contract" not in case and saved_contract:
                if digest(saved_contract) != state.get("contract_sha256"):
                    raise AuditError("saved external contract changed; refusing to mix versions")
                contract = load_json(saved_contract)
        result = load_json(args.result)
        validate_case = dict(case, output_contract=contract) if contract is not None else case
        if args.command == "validate":
            report = validate_result(validate_case, result, fields)
            artifact_paths = write_contract_artifacts(work_dir, report)
            full_report_path = work_dir / "validation.json"
            atomic_json(full_report_path, report)
            if args.format == "brief":
                brief = brief_validation_report(report, full_report_path, artifact_paths)
                print(json.dumps(brief, ensure_ascii=False, separators=(",", ":")))
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["valid"] else 1
        if state is None:
            raise AuditError("patch requires the checkpoint from bootstrap in --work-dir")
        patch_document = load_json(args.patch)
        metadata = {}
        if isinstance(patch_document, dict):
            metadata = {key: patch_document[key] for key in ("reason", "source_refs")
                        if key in patch_document}
            patch_document = patch_document.get("patch")
        if not isinstance(patch_document, list):
            raise AuditError("patch must be a JSON array or an object containing a patch array")
        if "case_id" in result:
            for operation in patch_document:
                if isinstance(operation, dict) and operation.get("path") == "/case_id" and \
                        operation.get("op") in ("remove", "replace"):
                    raise AuditError("case_id is immutable")
        updated = apply_patch(result, patch_document)
        report = validate_result(validate_case, updated, fields)
        if not report["valid"]:
            write_contract_artifacts(work_dir, report)
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
        state["contract_adaptation"] = report["contract_adaptation"]
        write_contract_artifacts(work_dir, report)
        atomic_json(state_path, state)
        atomic_json(work_dir / "validation.json", report)
        print(json.dumps({"result": str(result_path), "valid": True,
                          "change_count": len(state["changes"]), "warnings": report["warnings"]},
                         ensure_ascii=False, indent=2))
        return 0
    except (AuditError, OSError, json.JSONDecodeError, TypeError, re.error) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
