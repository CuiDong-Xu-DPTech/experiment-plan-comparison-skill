#!/usr/bin/env python3
"""Index sources, verify evidence locators, and project source-linked JSON Patches.

No text, extracted page, scientific fact, or answer is bundled with this tool.
PDF extraction uses an available listed parser/tool; OCR/rendering is only
claimed after the agent performs it with resources supplied by the environment.
"""
import argparse
import copy
import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = "1.2.0"
TEXT_SUFFIXES = {".txt", ".md", ".json", ".jsonl", ".csv", ".tsv",
                 ".yaml", ".yml", ".xml", ".html", ".htm"}
EXCLUDED_PARTS = {".git", ".ssh", ".aws", "node_modules", "__pycache__",
                  ".venv", "venv"}
EXCLUDED_NAMES = {".env", "id_rsa", "id_ed25519", "credentials.json",
                  "credentials.env", ".netrc"}


class SourceError(ValueError):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_json(path):
    def reject(value):
        raise SourceError("non-finite JSON number: " + value)

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise SourceError("non-finite JSON number: " + value)
        return number

    with open(path, encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=unique_object,
                         parse_constant=reject, parse_float=finite)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    os.replace(str(temp), str(path))


def write_new_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with open(path, "xb") as handle:
            created = True
            handle.write(data)
    except OSError:
        if created:
            path.unlink()
        raise


def sha256(path):
    result = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_files(root):
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in EXCLUDED_PARTS
                          and not Path(directory, name).is_symlink())
        for name in sorted(files):
            if name in EXCLUDED_NAMES or name.startswith(".env."):
                continue
            path = Path(directory, name)
            if not path.is_symlink() and path.is_file():
                yield path


def pdf_backend():
    executable = shutil.which("pdftotext")
    if executable:
        version = subprocess.run([executable, "-v"], text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=15, check=False)
        return "pdftotext", executable, (version.stderr or version.stdout).strip().splitlines()[0]
    for package, distribution in (("pypdf", "pypdf"), ("fitz", "PyMuPDF")):
        if importlib.util.find_spec(package) is not None:
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                version = "unknown-installed-version"
            return package, package, version
    return None, None, None


def extract_pdf(path, destination, backend):
    name = backend[0]
    try:
        return _extract_pdf(path, destination, backend)
    except SourceError:
        raise
    except Exception as error:
        detail = str(error)[:500]
        raise SourceError("PDF extraction failed with %s (%s): %s" % (
            str(name), type(error).__name__, detail)) from None


def _extract_pdf(path, destination, backend):
    name, executable, version = backend
    destination.mkdir(parents=True, exist_ok=True)
    if name == "pdftotext":
        output = destination / "all-pages.txt"
        process = subprocess.run([executable, "-layout", str(path), str(output)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=180, check=False)
        if process.returncode:
            raise SourceError("pdftotext failed: " + process.stderr.decode("utf-8", "replace")[:1000])
        chunks = output.read_text(encoding="utf-8", errors="strict").split("\f")
        if chunks and not chunks[-1].strip():
            chunks.pop()
        pages = chunks
    elif name == "pypdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [page.extract_text() or "" for page in reader.pages]
    elif name == "fitz":
        import fitz
        with fitz.open(str(path)) as reader:
            if reader.needs_pass and not reader.authenticate(""):
                raise SourceError("encrypted PDF requires credentials; not supplied")
            pages = [page.get_text("text") for page in reader]
    else:
        raise SourceError("no available PDF text parser; rendering/OCR or a supplied parser must be used")
    if not pages:
        raise SourceError("PDF has zero extractable pages")
    records = []
    for page_number, text in enumerate(pages, 1):
        page_path = destination / ("page-%04d.txt" % page_number)
        page_path.write_text(text, encoding="utf-8")
        records.append({"page": page_number, "text_path": str(page_path.resolve()),
                        "characters": len(text), "sha256": text_hash(text),
                        "status": "text_extracted" if text.strip() else "no_text_layer"})
    return records, {"name": name, "executable": executable,
                     "version": version, "layout": "pdftotext -layout" if name == "pdftotext" else "library page text"}


def parse_pointer(pointer):
    if pointer == "":
        return []
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise SourceError("invalid JSON Pointer: " + str(pointer))
    result = []
    for part in pointer[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise SourceError("invalid JSON Pointer escape in: " + pointer)
        result.append(part.replace("~1", "/").replace("~0", "~"))
    return result


def json_at(document, pointer):
    current = document
    for part in parse_pointer(pointer):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", part):
            if int(part) >= len(current):
                raise SourceError("array index out of bounds: " + pointer)
            current = current[int(part)]
        else:
            raise SourceError("JSON Pointer not found: " + pointer)
    return current


def occurrence(text, quote):
    if not isinstance(quote, str) or not quote:
        raise SourceError("evidence quote must be non-empty")
    start = text.find(quote)
    if start < 0:
        raise SourceError("exact quote was not found")
    return {"start_inclusive": start, "end_exclusive": start + len(quote),
            "line_start": text.count("\n", 0, start) + 1,
            "line_end": text.count("\n", 0, start + len(quote)) + 1,
            "is_unique": text.find(quote, start + 1) < 0}


def create_index(root, work, max_files, max_text_bytes, max_pdf_bytes):
    root = Path(root).resolve()
    work = Path(work).resolve()
    if not root.is_dir():
        raise SourceError("source root is not a directory: " + str(root))
    if root == work or root in work.parents:
        raise SourceError("work directory must be outside the indexed source root")
    all_files = []
    truncated = False
    for path in safe_files(root):
        if len(all_files) >= max_files:
            truncated = True
            break
        all_files.append(path)
    backend = pdf_backend()
    records = []
    for index, path in enumerate(all_files, 1):
        source_id = "source-%04d" % index
        record = {"source_id": source_id, "path": str(path),
                  "relative_path": path.relative_to(root).as_posix(),
                  "role": "provided_input",
                  "status": "not_indexed", "evidence_locators": []}
        try:
            record["sha256"] = sha256(path)
            record["bytes"] = path.stat().st_size
            suffix = path.suffix.lower()
            if suffix in TEXT_SUFFIXES and path.stat().st_size > max_text_bytes:
                raise SourceError("text exceeds configured byte limit")
            if suffix == ".pdf":
                if path.stat().st_size > max_pdf_bytes:
                    raise SourceError("PDF exceeds configured byte limit")
                destination = work / "extracted" / source_id
                pages, tool = extract_pdf(path, destination, backend)
                record["pages"] = pages
                record["extraction"] = tool
                record["status"] = "text_extracted" if any(page["characters"] for page in pages) else "no_text_layer"
                record["evidence_locators"] = ["physical_pdf_page", "exact_quote", "text_offset_and_lines"]
            elif suffix in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="strict")
                destination = work / "extracted" / source_id
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / "content.txt"
                target.write_text(text, encoding="utf-8")
                record["pages"] = [{"page": None, "text_path": str(target),
                                    "characters": len(text), "sha256": text_hash(text),
                                    "status": "text_copied_without_content_normalization"}]
                record["status"] = "text_indexed"
                locators = ["exact_quote", "text_offset_and_lines"]
                if suffix == ".json":
                    json.loads(text, object_pairs_hook=unique_object,
                               parse_constant=lambda value: (_ for _ in ()).throw(SourceError("non-finite JSON number")))
                    record["json_pointer_supported"] = True
                    locators.append("rfc6901_json_pointer")
                record["evidence_locators"] = locators
            else:
                record["status"] = "hashed_only_requires_appropriate_reader"
                record["uncertainty"] = "file content has not been parsed by this script"
        except (OSError, UnicodeError, SourceError, ValueError, json.JSONDecodeError) as error:
            record["status"] = "source_unreadable"
            record["error"] = str(error)
        records.append(record)
    result = {"schema_version": "source-audit-index/v1", "tool_version": VERSION,
              "created_at": now(), "source_root": str(root), "max_files": max_files,
              "file_selection_truncated": truncated, "pdf_backend": backend,
              "source_count": len(records), "sources": records,
              "scope": "inventoried only actual supplied files; hashes attest bytes, not truth or peer review"}
    write_json(work / "source-index.json", result)
    return result


def verify(index_path, claims_path, output_path):
    index = load_json(index_path)
    claims = load_json(claims_path)
    if not isinstance(claims, list):
        raise SourceError("evidence input must be a list of claim-locator objects")
    sources = {source["source_id"]: source for source in index.get("sources", [])}
    results = []
    for number, claim in enumerate(claims):
        result = {"index": number,
                  "claim_id": claim.get("claim_id") if isinstance(claim, dict) else None,
                  "source_id": claim.get("source_id") if isinstance(claim, dict) else None,
                  "locator_verified": False}
        try:
            if not isinstance(claim, dict):
                raise SourceError("claim is not an object")
            source = sources.get(claim.get("source_id"))
            if source is None:
                raise SourceError("unknown source_id")
            if sha256(source["path"]) != source.get("sha256"):
                raise SourceError("source bytes changed since indexing")
            kind = claim.get("kind")
            if kind == "json_pointer":
                value = json_at(load_json(source["path"]), claim["pointer"])
                if "expected_value" not in claim:
                    raise SourceError("json_pointer citation needs expected_value")
                if value != claim["expected_value"] or type(value) is not type(claim["expected_value"]):
                    raise SourceError("value at pointer differs from the cited value")
                result["locator"] = {"json_pointer": claim["pointer"], "observed_value": value}
            elif kind in ("page_quote", "text_quote"):
                page = claim.get("page")
                pages = [entry for entry in source.get("pages", []) if entry["page"] == page]
                if len(pages) != 1:
                    raise SourceError("requested text/page was not indexed")
                page_record = pages[0]
                text = Path(page_record["text_path"]).read_text(encoding="utf-8")
                if text_hash(text) != page_record["sha256"]:
                    raise SourceError("extraction changed since indexing")
                quote = claim["exact"]
                locator = occurrence(text, quote)
                if page is not None:
                    locator["physical_pdf_page"] = page
                result["locator"] = locator
                result["exact"] = quote
                result["extraction"] = source.get("extraction", "unmodified plain-text copy")
                if not locator["is_unique"]:
                    result["warning"] = "quote is ambiguous; record section and an exact prefix/suffix in the claim"
            else:
                raise SourceError("kind must be page_quote, text_quote, or json_pointer")
            result["locator_verified"] = True
        except (KeyError, TypeError, OSError, ValueError, SourceError, json.JSONDecodeError) as error:
            result["error"] = str(error)
        result["verification"] = "only locator/value equality and byte integrity; evidence relevance and scientific support require review"
        results.append(result)
    report = {"tool_version": VERSION, "verified_at": now(),
              "status": "no_claims" if not results else "locator_checks_completed",
              "all_locators_verified": bool(results) and all(item["locator_verified"] for item in results),
              "claims": results}
    write_json(output_path, report)
    return report


def select_keys(value, keys):
    if isinstance(value, dict):
        missing = [key for key in keys if key not in value]
        if missing:
            raise SourceError("select key missing from an encountered object: " +
                              ", ".join(repr(key) for key in missing))
        return {key: select_keys(value[key], keys) for key in keys}
    if isinstance(value, list):
        return [select_keys(item, keys) for item in value]
    return copy.deepcopy(value)


def project(case_path, map_path, output_path):
    output_path = Path(output_path)
    if output_path.exists():
        raise SourceError("output already exists: " + str(output_path))
    case = load_json(case_path)
    requests = load_json(map_path)
    if not isinstance(requests, list):
        raise SourceError("projection map must be a JSON array")
    patch = []
    sources = []
    source_path = str(Path(case_path).resolve())
    source_hash = sha256(case_path)
    for index, request in enumerate(requests):
        prefix = "map[" + str(index) + "]: "
        if not isinstance(request, dict):
            raise SourceError(prefix + "entry must be an object")
        unknown = set(request) - {"result_path", "source_pointer", "select"}
        if unknown:
            raise SourceError(prefix + "unsupported keys: " + ", ".join(sorted(unknown)))
        if "result_path" not in request or "source_pointer" not in request:
            raise SourceError(prefix + "result_path and source_pointer are required")
        result_path = request["result_path"]
        source_pointer = request["source_pointer"]
        if not isinstance(result_path, str) or not result_path.startswith("/"):
            raise SourceError(prefix + "result_path must be a non-empty RFC6901 JSON Pointer")
        if parse_pointer(result_path)[0] == "case_id":
            raise SourceError(prefix + "case_id is immutable")
        if not isinstance(source_pointer, str):
            raise SourceError(prefix + "source_pointer must be an RFC6901 JSON Pointer")
        source_value = json_at(case, source_pointer)
        if "select" in request:
            keys = request["select"]
            if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
                raise SourceError(prefix + "select must be an array of strings")
            value = select_keys(source_value, keys)
        else:
            keys = None
            value = copy.deepcopy(source_value)
        patch.append({"op": "replace", "path": result_path, "value": value})
        sources.append({"source_pointer": source_pointer, "result_path": result_path,
                        "select": copy.deepcopy(keys), "source_path": source_path,
                        "sha256": source_hash})
    report = {"schema_version": "source-projection/v1", "patch": patch, "sources": sources}
    write_new_json(output_path, report)
    return {"source_count": len(patch)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    index_parser = sub.add_parser("index")
    index_parser.add_argument("--root", required=True)
    index_parser.add_argument("--work-dir", required=True)
    index_parser.add_argument("--max-files", type=int, default=500)
    index_parser.add_argument("--max-text-bytes", type=int, default=8 * 1024 * 1024)
    index_parser.add_argument("--max-pdf-bytes", type=int, default=200 * 1024 * 1024)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--index", required=True)
    verify_parser.add_argument("--claims", required=True)
    verify_parser.add_argument("--output", required=True)
    project_parser = sub.add_parser("project")
    project_parser.add_argument("--case", required=True)
    project_parser.add_argument("--map", required=True)
    project_parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "index":
            if args.max_files < 1 or args.max_text_bytes < 1 or args.max_pdf_bytes < 1:
                raise SourceError("resource limits must be positive")
            report = create_index(args.root, args.work_dir, args.max_files,
                                  args.max_text_bytes, args.max_pdf_bytes)
        elif args.command == "project":
            report = project(args.case, args.map, args.output)
        else:
            report = verify(args.index, args.claims, args.output)
        print(json.dumps({"status": "tool_completed", "records": report.get("source_count", len(report.get("claims", []))),
                          "truncated": report.get("file_selection_truncated"),
                          "all_locators_verified": report.get("all_locators_verified")},
                         ensure_ascii=False, indent=2))
        return 0 if args.command in ("index", "project") or report["all_locators_verified"] else 1
    except (SourceError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "tool_error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
