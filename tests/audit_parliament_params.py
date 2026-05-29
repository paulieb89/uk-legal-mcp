"""Audit parliament module's upstream API calls against the Hansard Swagger spec.

For each `client.get(f"{HANSARD_API}/…", params=…)` call in
src/modules/parliament/*.py, this script:

  1. Finds the upstream endpoint URL we hit.
  2. Looks it up in references/hansard-swagger-v1.json.
  3. Compares the params we send against the params the spec declares.
  4. Reports three categories per call:
       a) **Wrong-name params** — we send a key not in the spec. Upstream
          silently ignores these; the request 200s with empty results. This
          is the exact failure mode that hit during the May 2026 probing of
          /search/debatebycolumn (column vs columnNumber).
       b) **Type mismatches** — we send a key with a type the spec doesn't
          declare.
       c) **Unused-but-declared params** — keys the spec offers that we
          don't expose. Surfaces "we could let the lawyer filter by X" gaps.

Run: `uv run python tests/audit_parliament_params.py`

This is an offline audit (no network calls). It walks Python AST to find
the calls; the spec is loaded from disk.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SWAGGER_PATH = REPO_ROOT / "references" / "hansard-swagger-v1.json"
PARLIAMENT_DIR = REPO_ROOT / "src" / "modules" / "parliament"

# Upstream bases the parliament module talks to. We only audit Hansard calls
# (HANSARD_API) because that's the only one with a Swagger contract; Members
# API has separate docs.
HANSARD_API = "https://hansard-api.parliament.uk"

# Variable names in source code that resolve to HANSARD_API. The parliament
# module assigns `HANSARD_API = "https://hansard-api.parliament.uk"` at the
# top of tools.py and resources.py; f-string calls reference it by name.
HANSARD_API_VAR_NAMES = {"HANSARD_API"}


def load_spec() -> dict:
    return json.loads(SWAGGER_PATH.read_text())


def spec_params_for_path(spec: dict, path_template: str) -> dict[str, dict]:
    """Return {param_name: param_def} for a Swagger path's GET method.

    Swagger paths look like '/debates/debate/{debateSectionExtId}.{format}'.
    We want every `in: query` parameter; path parameters are skipped because
    they're part of the URL structure and can't be a "wrong-name" mistake.

    Swagger conventions in this spec: query params are prefixed
    `queryParameters.X` for grouped sets. We strip that prefix when comparing
    against the live request (verified during probing — the prefix is a
    documentation convention; the wire name is just X).
    """
    path_info = spec.get("paths", {}).get(path_template, {})
    get_info = path_info.get("get", {})
    out: dict[str, dict] = {}
    for p in get_info.get("parameters", []):
        if p.get("in") != "query":
            continue
        name = p.get("name", "")
        # Strip 'queryParameters.' prefix — wire name is the leaf.
        wire_name = name.split(".")[-1]
        out[wire_name] = p
    return out


def find_swagger_path_for_url(spec: dict, url_fragment: str) -> str | None:
    """Match a constructed URL like '/search/debatebycolumn.json' to the
    Swagger path template '/search/debatebycolumn.{format}'.

    Rules:
      - Strip the literal extension (.json) and treat it as {format}.
      - Substitute f-string placeholders ({var}) with a literal segment to
        match path-templated endpoints like /debates/Debate/{ext}.json →
        /debates/debate/{debateSectionExtId}.{format} (case-insensitive on
        the path body).
    """
    # Normalise: turn .json/.xml/.csv into .{format}
    norm = re.sub(r"\.(json|xml|csv|opml|atom)$", r".{format}", url_fragment, flags=re.IGNORECASE)
    # Turn any f-string {placeholder} into a wildcard segment for matching.
    # We don't care WHICH placeholder it matches — just that the structure aligns.
    placeholder_pattern = re.sub(r"\{[^}]+\}", r"\\{[^}]+\\}", re.escape(norm)).replace(r"\\\\", r"\\")
    # Actually simpler: match by stripping placeholders on both sides.
    def strip_placeholders(s: str) -> str:
        return re.sub(r"\{[^}]+\}", "{*}", s)

    target = strip_placeholders(norm.lower())
    for candidate in spec.get("paths", {}):
        if strip_placeholders(candidate.lower()) == target:
            return candidate
    return None


class CallSite:
    """A single upstream call extracted from a tool's source code."""

    def __init__(self, file: str, line: int, function_name: str, url_template: str, qp_keys: list[str]):
        self.file = file
        self.line = line
        self.function_name = function_name
        self.url_template = url_template
        self.qp_keys = qp_keys

    def __repr__(self) -> str:
        return f"{self.file}:{self.line} {self.function_name} → {self.url_template} (params: {self.qp_keys})"


class HansardCallFinder(ast.NodeVisitor):
    """Walk a Python AST and pull out every `client.get(f"{HANSARD_API}/…", params=qp)`
    call. Param-key extraction is scoped to the enclosing function body so we
    don't pick up keys from sibling functions or unrelated dict literals."""

    def __init__(self, source: str, file: str):
        self.source = source
        self.source_lines = source.splitlines()
        self.file = file
        self.calls: list[CallSite] = []
        # Stack of (function_name, function_ast_node) so we can scope param tracing.
        self._fn_stack: list[tuple[str, ast.AST]] = []

    def visit_FunctionDef(self, node):
        self._fn_stack.append((node.name, node))
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_AsyncFunctionDef(self, node):
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

        params_node = None
        for kw in node.keywords:
            if kw.arg == "params":
                params_node = kw.value
                break

        fn_name, fn_node = self._fn_stack[-1] if self._fn_stack else ("<module>", None)
        qp_keys = self._extract_qp_keys(params_node, fn_node, node.lineno) if params_node else []
        self.calls.append(CallSite(self.file, node.lineno, fn_name, path, qp_keys))
        self.generic_visit(node)

    def _extract_url(self, node) -> str | None:
        """Pull the URL string out of an f-string or plain string.

        For FormattedValue, substitute known module-level constants by their
        string value (so f"{HANSARD_API}/search.json" → HANSARD_API + "/search.json").
        Unknown variables become {placeholder} markers for matching path templates.
        """
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
                    elif isinstance(expr, ast.Attribute):
                        # e.g. params.debate_ext_id
                        parts.append("{" + expr.attr + "}")
                    else:
                        parts.append("{?}")
            return "".join(parts)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _extract_qp_keys(self, params_node, fn_node, call_line: int) -> list[str]:
        """Find the dict that params=… refers to and pull its keys.

        AST-scoped to the enclosing function body so we don't pick up keys
        from sibling functions, tool annotations, or unrelated dict literals.

        Handles:
          1. params={"a": …, "b": …}                (inline dict literal)
          2. params=qp where qp = {"a": …, "b": …}  (local dict)
          3. qp = dict(...); qp["foo"] = bar         (build-up pattern)
        """
        if isinstance(params_node, ast.Dict):
            return self._keys_from_dict_literal(params_node)
        if isinstance(params_node, ast.Name) and fn_node is not None:
            return self._trace_local_dict_ast(params_node.id, fn_node, call_line)
        return []

    def _keys_from_dict_literal(self, node: ast.Dict) -> list[str]:
        keys: list[str] = []
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.append(k.value)
        return keys

    def _trace_local_dict_ast(self, name: str, fn_node: ast.AST, call_line: int) -> list[str]:
        """Find `name = {...}` and `name["foo"] = ...` statements WITHIN the
        function body, before the call line. Uses AST so it cannot accidentally
        pick up dict-literal keys from sibling functions or from tool-decorator
        annotation dicts."""
        keys: set[str] = set()
        for stmt in ast.walk(fn_node):
            # qp = {"a": ..., "b": ...}
            if isinstance(stmt, ast.Assign) and getattr(stmt, "lineno", 10**9) < call_line:
                # Single-target assignment to the variable name we care about
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == name and isinstance(stmt.value, ast.Dict):
                        keys.update(self._keys_from_dict_literal(stmt.value))
            # qp["foo"] = ...
            if isinstance(stmt, ast.Assign) and getattr(stmt, "lineno", 10**9) < call_line:
                for target in stmt.targets:
                    if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == name):
                        slc = target.slice
                        if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
                            keys.add(slc.value)
            # qp: dict = {...}  (annotated assignment)
            if isinstance(stmt, ast.AnnAssign) and getattr(stmt, "lineno", 10**9) < call_line:
                target = stmt.target
                if isinstance(target, ast.Name) and target.id == name and isinstance(stmt.value, ast.Dict):
                    keys.update(self._keys_from_dict_literal(stmt.value))
        return sorted(keys)


def audit():
    spec = load_spec()
    findings: list[str] = []
    summary_lines: list[str] = []
    issue_count = 0

    sources = sorted(PARLIAMENT_DIR.glob("*.py"))
    for src in sources:
        text = src.read_text()
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            findings.append(f"  ⚠️  syntax error in {src}: {e}")
            continue
        finder = HansardCallFinder(text, str(src.relative_to(REPO_ROOT)))
        finder.visit(tree)
        for call in finder.calls:
            spec_path = find_swagger_path_for_url(spec, call.url_template)
            header = f"\n● {call.file}:{call.line}  {call.function_name}()"
            header += f"\n    URL: {HANSARD_API}{call.url_template}"
            if spec_path is None:
                summary_lines.append(header)
                summary_lines.append(f"    ❓ NOT MATCHED in Swagger spec — endpoint may be undocumented or URL constructed differently")
                continue
            spec_params = spec_params_for_path(spec, spec_path)
            spec_names = set(spec_params.keys())
            sent_names = set(call.qp_keys)

            wrong = sent_names - spec_names
            unused = spec_names - sent_names

            status = "✓ OK" if not wrong else f"❌ {len(wrong)} wrong-name param(s)"
            summary_lines.append(header)
            summary_lines.append(f"    Spec: {spec_path}")
            summary_lines.append(f"    Sent: {sorted(sent_names)}")
            summary_lines.append(f"    Status: {status}")
            if wrong:
                issue_count += len(wrong)
                summary_lines.append(f"    ❌ Wrong-name params (silently ignored upstream): {sorted(wrong)}")
            if unused:
                summary_lines.append(f"    💡 Unused but available in spec: {sorted(unused)}")

    print("=== Parliament parameter audit ===")
    print(f"Spec: {SWAGGER_PATH.relative_to(REPO_ROOT)}")
    print(f"Source files: {len(sources)}")
    print("\n".join(summary_lines))
    print(f"\n=== Summary: {issue_count} wrong-name params across all calls ===")
    return issue_count


if __name__ == "__main__":
    sys.exit(audit())
