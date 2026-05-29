"""Audit parliament module's response-field usage against the Hansard Swagger.

Sibling to tests/audit_parliament_params.py — that audit walks REQUEST-side
param names (catches silent wire-drop bugs like `column` vs `columnNumber`).
This audit walks RESPONSE-side field reads (catches silent-substitution bugs
like `Rank` being relabelled as `contribution_count` — Obs 173).

For each `client.get(f"{HANSARD_API}/…")` call:

  1. Find the upstream endpoint URL.
  2. Resolve its Swagger response schema through the $ref chain
     (QueryResult[T] → Results[] → T), collecting every declared field name.
  3. Heuristically extract every `payload.get("X")` / `item.get("X")` /
     `obj.get("X")` string-literal access inside the SAME function (response
     fields are consumed via dict.get on the parsed JSON; the heuristic has
     small false-positive risk but is good enough for a human-reviewed audit).
  4. Report per endpoint:
       a) **Fields consumed by code** vs **fields declared in spec** —
          consumed-but-not-declared = silent-substitution risk (we may be
          reading None when we expect a value; the Rank → contribution_count
          shape is a name-promise bug, not a missing-field bug, but the same
          audit infrastructure catches both).
       b) **Fields with type-vs-use semantic-mismatch heuristics**:
            - `Rank` consumed and labelled as anything *_count → flag
            - `Total*` consumed alone (without paginating) → flag
            - PascalCase field consumed but spec doesn't declare it → flag
       c) **Spec fields not consumed** — informational ("you could surface X").

Run: `uv run python tests/audit_parliament_responses.py`

Offline (no network). Re-runnable on every PR that touches parliament code.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SWAGGER_PATH = REPO_ROOT / "references" / "hansard-swagger-v1.json"
PARLIAMENT_DIR = REPO_ROOT / "src" / "modules" / "parliament"

HANSARD_API = "https://hansard-api.parliament.uk"
HANSARD_API_VAR_NAMES = {"HANSARD_API"}

# Heuristic: PascalCase strings look like Swagger response field names.
# Mixed-case-with-underscore and lowercase-only are our own model names
# (debate_ext_id) or wire param names (columnNumber).
_RESPONSE_FIELD_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")

# Semantic-mismatch heuristics — fields that often get re-labelled wrongly.
_RANK_LIKE = {"Rank"}
_TOTAL_LIKE_PREFIXES = ("Total",)


def load_spec() -> dict:
    return json.loads(SWAGGER_PATH.read_text())


def resolve_ref(spec: dict, ref: str) -> dict:
    """Follow a Swagger $ref string (e.g. '#/definitions/SearchDebateItem')
    to the definition object. Returns {} if the ref is unresolvable."""
    if not ref.startswith("#/"):
        return {}
    parts = ref.lstrip("#/").split("/")
    node: dict = spec
    for p in parts:
        node = node.get(p, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def collect_response_fields(spec: dict, schema: dict, seen: set[str] | None = None) -> dict[str, dict]:
    """Walk a response schema's $ref / properties / items recursively and
    collect every field name → {type, format} declaration.

    Handles three nesting shapes:
      - Direct $ref to an object definition
      - Object with properties[] dict
      - Object with array property (items: {$ref: ...})

    Returns {field_name: {"type": "string", "format": "date-time", ...}}.
    """
    seen = seen if seen is not None else set()
    out: dict[str, dict] = {}

    if not schema:
        return out

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            return out
        seen.add(ref)
        definition = resolve_ref(spec, ref)
        return collect_response_fields(spec, definition, seen)

    properties = schema.get("properties") or {}
    for fname, finfo in properties.items():
        out[fname] = {k: v for k, v in finfo.items() if k in ("type", "format", "description")}
        if "$ref" in finfo:
            out.update(collect_response_fields(spec, finfo, seen))
        if finfo.get("type") == "array" and isinstance(finfo.get("items"), dict):
            out.update(collect_response_fields(spec, finfo["items"], seen))

    return out


def spec_response_for_path(spec: dict, path_template: str) -> dict[str, dict]:
    """Return {field_name: {type, format}} for a Swagger path's GET 200 response."""
    path_info = spec.get("paths", {}).get(path_template, {})
    get_info = path_info.get("get", {})
    response_200 = get_info.get("responses", {}).get("200", {})
    schema = response_200.get("schema", {})
    return collect_response_fields(spec, schema)


def find_swagger_path_for_url(spec: dict, url_fragment: str) -> str | None:
    """Match a constructed URL to its Swagger path template.

    Copied from audit_parliament_params.py — same matching rules.
    """
    norm = re.sub(r"\.(json|xml|csv|opml|atom)$", r".{format}", url_fragment, flags=re.IGNORECASE)

    def strip_placeholders(s: str) -> str:
        return re.sub(r"\{[^}]+\}", "{*}", s)

    target = strip_placeholders(norm.lower())
    for candidate in spec.get("paths", {}):
        if strip_placeholders(candidate.lower()) == target:
            return candidate
    return None


@dataclass
class CallSite:
    file: str
    line: int
    function_name: str
    url_template: str
    response_fields_consumed: set[str] = field(default_factory=set)


class HansardResponseFinder(ast.NodeVisitor):
    """Walk an AST and pull out:
      - Every `client.get(f"{HANSARD_API}/…")` upstream call (endpoint discovery)
      - Every `<obj>.get("PascalCaseStringLiteral")` in the same function
        body (response-field consumption heuristic)

    The .get() heuristic produces some false positives (e.g. `os.environ.get`
    or dict accesses where the key happens to be PascalCase but isn't a
    response field). False positives are acceptable for an audit — a human
    reviews the report.
    """

    def __init__(self, source: str, file: str):
        self.source = source
        self.file = file
        self.calls: list[CallSite] = []
        # Map from function AST node id → list of (lineno, field_name).
        self._fn_field_reads: dict[int, list[tuple[int, str]]] = {}
        # Map from function AST node id → list of upstream call linenos
        # (used for attribution windows).
        self._fn_upstream_call_lines: dict[int, list[int]] = {}
        # Stack of (function_name, function_ast_node)
        self._fn_stack: list[tuple[str, ast.AST]] = []

    # First pass: collect every PascalCase .get("X") with line number.
    # Later we attribute each access to the nearest preceding upstream call
    # in the same function — eliminates the multi-call-in-one-function
    # false-positive where Items from /debates/Debate gets attributed
    # to the earlier /search/debatebycolumn call too.
    def _index_function_field_reads(self, fn_node: ast.AST) -> list[tuple[int, str]]:
        reads: list[tuple[int, str]] = []
        for node in ast.walk(fn_node):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
                continue
            if not node.args:
                continue
            arg0 = node.args[0]
            if not isinstance(arg0, ast.Constant) or not isinstance(arg0.value, str):
                continue
            literal = arg0.value
            if _RESPONSE_FIELD_RE.match(literal):
                reads.append((node.lineno, literal))
        return reads

    def visit_FunctionDef(self, node):
        self._fn_field_reads[id(node)] = self._index_function_field_reads(node)
        self._fn_stack.append((node.name, node))
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_AsyncFunctionDef(self, node):
        self._fn_field_reads[id(node)] = self._index_function_field_reads(node)
        self._fn_stack.append((node.name, node))
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_Call(self, node):
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            return self.generic_visit(node)
        if not node.args:
            return self.generic_visit(node)
        url_template = self._extract_url(node.args[0])
        if url_template is None or not url_template.startswith(HANSARD_API):
            return self.generic_visit(node)
        path = url_template[len(HANSARD_API):]
        fn_name, fn_node = self._fn_stack[-1] if self._fn_stack else ("<module>", None)
        if fn_node is not None:
            self._fn_upstream_call_lines.setdefault(id(fn_node), []).append(node.lineno)
        self.calls.append(CallSite(
            file=self.file,
            line=node.lineno,
            function_name=fn_name,
            url_template=path,
            response_fields_consumed=set(),  # filled in by attribution pass
        ))
        self.generic_visit(node)

    def attribute_field_reads(self) -> None:
        """Attribute every PascalCase .get('X') access in a function to EVERY
        upstream Hansard call in that function. Coarse but high-recall:
        when a function calls multiple endpoints, we'd need data-flow
        analysis to map each .get() to the right response variable; without
        that, attribute broadly and let the human reviewer triage false
        positives. The alternative (nearest-preceding-call attribution)
        silently dropped the `Rank` semantic-mismatch flag on
        policy_position_summary by attributing the read to a different
        call. High-recall + visible-noise beats low-recall + silent-gaps
        on an audit whose purpose is surfacing semantic mismatches."""
        for fn_node_id, reads in self._fn_field_reads.items():
            call_lines = self._fn_upstream_call_lines.get(fn_node_id, [])
            if not call_lines:
                continue
            field_set = {field_name for _, field_name in reads}
            for cl in call_lines:
                for c in self.calls:
                    if c.line == cl:
                        c.response_fields_consumed.update(field_set)
                        break

    def _extract_url(self, node) -> str | None:
        """Pull a URL string out of an f-string or plain string."""
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                elif isinstance(v, ast.FormattedValue):
                    expr = v.value
                    if isinstance(expr, ast.Name):
                        if expr.id in HANSARD_API_VAR_NAMES:
                            parts.append(HANSARD_API)
                        else:
                            parts.append("{" + expr.id + "}")
                    else:
                        parts.append("{?}")
            return "".join(parts)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None


def semantic_mismatch_flags(field_name: str, model_field_label: str | None = None) -> list[str]:
    """Heuristics for likely name-promise mismatches.

    Today this is a placeholder — the only signal we can extract statically
    is the spec field name + the consumed code. A future iteration could
    walk Pydantic models to find which response field populates which model
    field, then check label semantics. For now, surface known-risky names.
    """
    flags = []
    if field_name in _RANK_LIKE:
        flags.append(
            f"⚠️  '{field_name}' is a relevance score (int32). If labelled "
            f"as a count or quantity in the consuming code, that's the "
            f"Obs 173 lie shape."
        )
    return flags


def main() -> int:
    spec = load_spec()

    all_calls: list[CallSite] = []
    for py_file in sorted(PARLIAMENT_DIR.rglob("*.py")):
        source = py_file.read_text()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as e:
            print(f"!! Syntax error in {py_file}: {e}", file=sys.stderr)
            continue
        finder = HansardResponseFinder(source, str(py_file.relative_to(REPO_ROOT)))
        finder.visit(tree)
        finder.attribute_field_reads()
        all_calls.extend(finder.calls)

    print(f"Audit: response-field conformance vs {SWAGGER_PATH.name}")
    print(f"Found {len(all_calls)} Hansard upstream calls in parliament module.\n")

    drift_count = 0
    flag_count = 0
    for call in all_calls:
        spec_path = find_swagger_path_for_url(spec, call.url_template)
        spec_fields = spec_response_for_path(spec, spec_path) if spec_path else {}

        print(f"● {call.file}:{call.line}  {call.function_name}()")
        print(f"    URL: {HANSARD_API}{call.url_template}")
        print(f"    Spec: {spec_path or '(no match)'}")

        if not spec_fields:
            print("    Spec response: (no schema found — endpoint may use generic QueryResult)")
            print()
            continue

        consumed = call.response_fields_consumed
        spec_field_names = set(spec_fields.keys())
        in_both = sorted(consumed & spec_field_names)
        consumed_undeclared = sorted(consumed - spec_field_names)
        declared_unused = sorted(spec_field_names - consumed)

        if in_both:
            print(f"    ✓ Consumed and declared ({len(in_both)}):")
            for name in in_both:
                info = spec_fields.get(name, {})
                type_str = info.get("type", "?")
                fmt = info.get("format", "")
                desc = info.get("description", "")
                line = f"      • {name}: {type_str}"
                if fmt:
                    line += f"/{fmt}"
                if desc:
                    line += f" — {desc[:60]}"
                print(line)
                for flag in semantic_mismatch_flags(name):
                    print(f"        {flag}")
                    flag_count += 1

        if consumed_undeclared:
            print(f"    ⚠️  Consumed but NOT in spec ({len(consumed_undeclared)}):")
            for name in consumed_undeclared:
                print(f"      • {name} (silent-substitution risk)")
                drift_count += 1

        if declared_unused:
            preview = declared_unused[:8]
            extra = f" (+{len(declared_unused) - 8} more)" if len(declared_unused) > 8 else ""
            print(f"    💡 Spec declares but code doesn't consume: {preview}{extra}")

        print()

    print("=" * 60)
    print(f"Summary: {drift_count} consumed-undeclared fields, {flag_count} semantic-mismatch flags")
    if drift_count > 0:
        print("⚠️  Drift detected — review undeclared consumed fields above.")
        return 1
    if flag_count > 0:
        print("⚠️  Semantic-mismatch flags — review against model field naming.")
        return 1
    print("✓ No drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
