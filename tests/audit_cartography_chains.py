"""Live cartography-chain audit.

Sibling to `tests/audit_parliament_params.py`:

  - that script catches *wire-name* lies that the Swagger spec can prove
    (e.g. sending `member=…` when the spec only declares `memberId`).
  - this script catches *runtime* lies that only surface when you actually
    run the chain end-to-end (e.g. `DivisionMatchLite.id` advertising
    "chain via `id` to votes_get_division", where the Hansard ID is a
    different ID-space from the Lords/Commons Votes API).

What it does:

  1. Walks every BaseModel `Field(description=…)` and every `@mcp.tool`
     docstring across `src/modules/*/{models,tools}.py`.
  2. Regex-extracts chain-shaped promises: `Use as {x} in <consumer>`,
     `chain via X to Y`, `Use as the X input to Y`.
  3. For each chain, looks up a seed from `tests/cartography_seeds.json`
     (or derives one via the source-side tool when the chain is producer→
     consumer), constructs the consumer call, dispatches against the
     live local gateway via HTTP MCP.
  4. Reports PASS / FAIL / EMPTY / SKIPPED per chain with evidence.

Coverage scope:
  IN  — BaseModel Field descriptions in src/modules/*/models.py
        @mcp.tool docstrings in src/modules/*/tools.py
  OUT — instructions blocks, README, prompt bodies (looser prose; lower
        signal-to-noise). The high-value targets are the structured field
        promises the LLM reads in tools/list.

Run discipline:
  - Default: skipped (live calls + upstream rate cost).
    Trigger: RUN_LIVE_AUDIT=1 uv run python tests/audit_cartography_chains.py
  - Local gateway must be running. Override with GATEWAY_URL env var.
  - Re-run before merging any PR that touches model field descriptions
    or adds/removes tools.

Exit code: 1 on any FAIL or EMPTY; 0 otherwise. CI-runnable when
RUN_LIVE_AUDIT=1.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "src" / "modules"
SEEDS_PATH = ROOT / "tests" / "cartography_seeds.json"
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080/mcp")
HEALTH_URL = GATEWAY_URL.replace("/mcp", "/health")


# ── 1. Chain discovery via AST + regex ─────────────────────────────────

CHAIN_PATTERNS = [
    # "Use as {placeholder} in <target>" — covers URI templates + named tools.
    re.compile(
        r"[Uu]se as\s+`?\{?(?P<placeholder>[A-Za-z_][A-Za-z0-9_]*)\}?`?\s+in\s+"
        r"`?(?P<target>(?:[a-z]+://[^`\s.]+)|(?:[A-Za-z_][A-Za-z0-9_]*))`?"
    ),
    # "Use as the `placeholder` input to <target>"
    re.compile(
        r"[Uu]se as\s+the\s+`?(?P<placeholder>[A-Za-z_][A-Za-z0-9_]*)`?\s+"
        r"input\s+to\s+`?(?P<target>[A-Za-z_][A-Za-z0-9_]*)`?"
    ),
    # "chain via ... to <target>"
    re.compile(
        r"[Cc]hain\s+via\s+[`A-Za-z_0-9{} ]+\s+to\s+`?(?P<target>[A-Za-z_][A-Za-z0-9_]*)`?"
    ),
]


@dataclass
class Chain:
    source_file: str
    source_symbol: str  # ClassName.field_name OR tool_name
    description_excerpt: str
    target_kind: Literal["tool", "resource_uri"]
    target_name: str  # tool name like "votes_get_division" OR full URI template
    placeholder: str  # the field-shape variable name used in the chain

    @property
    def short_id(self) -> str:
        return f"{Path(self.source_file).name}:{self.source_symbol} → {self.target_name}"


def _description_from_field_call(call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg != "description":
            continue
        v = kw.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
        if isinstance(v, ast.JoinedStr) or isinstance(v, ast.BinOp):
            try:
                return ast.unparse(v).strip("()")
            except Exception:
                return ""
        if isinstance(v, ast.Tuple):
            parts = [e.value for e in v.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            return " ".join(parts)
        # Concatenated implicit-string-literal (Field(..., description=("a" "b")))
        if isinstance(v, ast.Constant):
            return str(v.value)
        # description=("multi" "line" "concat")  ← parenthesized tuple-shaped expr
        if hasattr(v, "values"):  # rare; defensive
            try:
                return ast.unparse(v)
            except Exception:
                return ""
    return ""


def _extract_chains_from_text(
    text: str, source_file: str, source_symbol: str
) -> list[Chain]:
    out: list[Chain] = []
    for pat in CHAIN_PATTERNS:
        for m in pat.finditer(text):
            placeholder = m.groupdict().get("placeholder", "") or ""
            target = m.group("target")
            kind: Literal["tool", "resource_uri"] = (
                "resource_uri" if "://" in target else "tool"
            )
            # excerpt: 80 chars surrounding the match
            start = max(0, m.start() - 25)
            end = min(len(text), m.end() + 60)
            excerpt = text[start:end].replace("\n", " ").strip()
            out.append(
                Chain(
                    source_file=source_file,
                    source_symbol=source_symbol,
                    description_excerpt=excerpt,
                    target_kind=kind,
                    target_name=target,
                    placeholder=placeholder,
                )
            )
    return out


def collect_chains() -> list[Chain]:
    chains: list[Chain] = []
    for py in sorted(MODULES.rglob("*.py")):
        if py.name not in ("models.py", "tools.py"):
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        rel = str(py.relative_to(ROOT))
        for node in ast.walk(tree):
            # BaseModel Field(description=...) entries
            if isinstance(node, ast.ClassDef):
                is_basemodel = any(
                    (isinstance(b, ast.Name) and b.id == "BaseModel") for b in node.bases
                )
                if not is_basemodel:
                    continue
                for stmt in node.body:
                    if not isinstance(stmt, ast.AnnAssign):
                        continue
                    if not isinstance(stmt.value, ast.Call):
                        continue
                    if not (isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "Field"):
                        continue
                    desc = _description_from_field_call(stmt.value)
                    if not desc:
                        continue
                    fn = stmt.target.id if isinstance(stmt.target, ast.Name) else "?"
                    sym = f"{node.name}.{fn}"
                    chains.extend(_extract_chains_from_text(desc, rel, sym))
            # @mcp.tool / @mcp.prompt decorated function docstrings
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_tool = False
                for dec in node.decorator_list:
                    name = ""
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                        name = dec.func.attr
                    elif isinstance(dec, ast.Attribute):
                        name = dec.attr
                    if name == "tool":
                        is_tool = True
                        break
                if not is_tool:
                    continue
                doc = ast.get_docstring(node) or ""
                if not doc:
                    continue
                chains.extend(_extract_chains_from_text(doc, rel, node.name))
    return chains


# ── 2. Seeds + producer-derived inputs ─────────────────────────────────


def load_seeds() -> dict[str, Any]:
    with open(SEEDS_PATH) as f:
        return json.load(f)


# Maps placeholder → (seed-key, value-shape) so we know which seed feeds which chain.
PLACEHOLDER_SEED_MAP: dict[str, str] = {
    "slug": "judgment_slugs",
    "eId": "_derived_judgment_eid",  # derived from judgment index
    "debate_ext_id": "debate_ext_ids",
    "contribution_ext_id": "_derived_contribution_ext_id",  # derived from seeded debate's header
    "member_id": "member_ids",
    "division_id": "_derived_division_id",  # from parliament_get_debate_divisions
    "type": "_legislation_type",  # from triples
    "year": "_legislation_year",
    "number": "_legislation_number",
    "section": "_legislation_section",
}


# ── 3. MCP HTTP dispatcher ─────────────────────────────────────────────


class MCPClient:
    def __init__(self, base_url: str) -> None:
        self.base = base_url
        self.client = httpx.Client(timeout=30.0)
        # Init session (some gateways are stateless; we set the header for safety)
        self._sid: str | None = None

    def _post(self, body: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._sid:
            headers["Mcp-Session-Id"] = self._sid
        r = self.client.post(self.base, json=body, headers=headers)
        r.raise_for_status()
        # Some streamable HTTP responses come back as SSE; tolerate both.
        text = r.text.strip()
        if text.startswith("data:"):
            text = text.split("data:", 1)[1].strip()
        return json.loads(text)

    def initialize(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "cartography-audit", "version": "1"},
            },
        }
        # First, a raw call to grab any session header
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        r = self.client.post(self.base, json=body, headers=headers)
        r.raise_for_status()
        self._sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if self._sid:
            # notify initialized
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, params: dict) -> dict:
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": params},
            }
        )

    def read_resource(self, uri: str) -> dict:
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "resources/read",
                "params": {"uri": uri},
            }
        )


# ── 4. Chain execution ─────────────────────────────────────────────────


@dataclass
class ChainResult:
    chain: Chain
    status: Literal["PASS", "FAIL", "EMPTY", "SKIPPED"]
    detail: str
    evidence: dict = field(default_factory=dict)


def _evaluate_tool_response(name: str, resp: dict) -> tuple[str, str, dict]:
    """Return (status, detail, evidence) for a tools/call response."""
    if "error" in resp:
        return "FAIL", f"JSON-RPC error: {resp['error']!r}", {"resp": resp}
    result = resp.get("result", {})
    if result.get("isError"):
        # Pull the text error
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        return "FAIL", f"isError=true: {text[:200]}", {"content": text[:500]}
    sc = result.get("structuredContent")
    if sc is None:
        content = result.get("content", [])
        if content and isinstance(content, list):
            txt = content[0].get("text", "") if isinstance(content[0], dict) else ""
            try:
                sc = json.loads(txt) if txt else {}
            except json.JSONDecodeError:
                sc = {"_raw": txt[:300]}
    # Heuristics for EMPTY: tool-specific primary-collection fields
    primary_lists = ("results", "contributions", "divisions", "matches", "items", "interests")
    if isinstance(sc, dict):
        for key in primary_lists:
            v = sc.get(key)
            if isinstance(v, list) and len(v) == 0:
                return "EMPTY", f"primary collection '{key}' is empty list", {"sc_keys": list(sc.keys())}
    return "PASS", "ok", {"sc_keys": list(sc.keys()) if isinstance(sc, dict) else "<not dict>"}


def _evaluate_resource_response(uri: str, resp: dict) -> tuple[str, str, dict]:
    if "error" in resp:
        return "FAIL", f"JSON-RPC error: {resp['error']!r}", {"resp": resp}
    result = resp.get("result", {})
    contents = result.get("contents", [])
    if not contents:
        return "FAIL", "no contents in resource response", {"result": result}
    first = contents[0] if isinstance(contents, list) else {}
    text = first.get("text", "") if isinstance(first, dict) else ""
    if not text:
        return "FAIL", "empty resource text", {"first": first}
    # Try to parse as JSON; if it's XML/HTML accept non-empty as PASS.
    try:
        body = json.loads(text)
        if isinstance(body, dict) and body.get("error"):
            return "FAIL", f"resource returned error body: {body['error'][:200]}", {"body": body}
        return "PASS", f"resource read OK ({len(text)} bytes)", {"json_keys": list(body.keys()) if isinstance(body, dict) else "<non-dict>"}
    except json.JSONDecodeError:
        return "PASS", f"resource read OK ({len(text)} bytes, non-JSON)", {"len": len(text)}


def _substitute_uri(template: str, values: dict[str, str]) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
        out = out.replace("{" + k + "*}", str(v))
    return out


def run_chain(chain: Chain, mcp: MCPClient, seeds: dict, derived: dict) -> ChainResult:
    """Execute one cartography chain and classify the result."""
    placeholder = chain.placeholder

    # Decide where to draw the seed value from.
    if chain.target_kind == "resource_uri":
        # Resource URIs may have multiple placeholders; substitute all known.
        uri = chain.target_name
        # Find all placeholder names in the URI template (including wildcards like {slug*})
        placeholders_in_uri = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\*?\}", uri)
        substitutions: dict[str, str] = {}
        for ph in placeholders_in_uri:
            seed_key = PLACEHOLDER_SEED_MAP.get(ph)
            if not seed_key:
                return ChainResult(chain, "SKIPPED", f"no seed mapping for URI placeholder {ph!r}")
            if seed_key.startswith("_derived"):
                val = derived.get(seed_key)
                if val is None:
                    return ChainResult(chain, "SKIPPED", f"derived seed {seed_key!r} not available")
                substitutions[ph] = str(val)
            else:
                arr = seeds.get(seed_key) or []
                if not arr:
                    return ChainResult(chain, "SKIPPED", f"empty seed list {seed_key!r}")
                # arr may be a list of strings/ints OR a list of dicts (legislation_triples).
                # Take the first; for dicts, leave to per-tool handlers — URI templates
                # don't need triple-shape values, only string-shape ones.
                first = arr[0]
                if isinstance(first, (str, int)):
                    substitutions[ph] = str(first)
                else:
                    return ChainResult(chain, "SKIPPED", f"seed shape unsupported for URI substitution: {type(first).__name__}")
        resolved_uri = _substitute_uri(uri, substitutions)
        try:
            resp = mcp.read_resource(resolved_uri)
        except httpx.HTTPError as e:
            return ChainResult(chain, "FAIL", f"HTTP {e!r}")
        status, detail, evidence = _evaluate_resource_response(resolved_uri, resp)
        evidence["uri"] = resolved_uri
        return ChainResult(chain, status, detail, evidence)

    # Tool target.
    tool_name = chain.target_name
    # Build the params dict. Use a fixture for known consumers.
    # Many tools accept `params: <Model>` so we pass the placeholder as the
    # tool's expected input field. We hand-curate a few special cases:
    params: dict[str, Any]
    if tool_name == "votes_get_division":
        # Needs division_id (int) and house (Lords|Commons). Use the derived seed.
        div_id = derived.get("_derived_division_id")
        house = derived.get("_derived_division_house", "Lords")
        if div_id is None:
            return ChainResult(chain, "SKIPPED", "no derived division_id available (producer not run yet)")
        params = {"division_id": int(div_id), "house": house}
    elif tool_name in ("parliament_member_debates", "parliament_member_interests"):
        mids = seeds.get("member_ids") or []
        if not mids:
            return ChainResult(chain, "SKIPPED", "no member_ids seed")
        params = {"member_id": mids[0]}
    elif tool_name == "case_law_grep_judgment":
        slugs = seeds.get("judgment_slugs") or []
        if not slugs:
            return ChainResult(chain, "SKIPPED", "no judgment_slugs seed")
        params = {"slug": slugs[0], "pattern": "court"}
    elif tool_name == "legislation_get_section":
        triples = seeds.get("legislation_triples") or []
        if not triples:
            return ChainResult(chain, "SKIPPED", "no legislation_triples seed")
        t = triples[0]
        params = {"type": t["type"], "year": t["year"], "number": t["number"], "section": t["section"]}
    elif tool_name == "legislation_get_toc":
        triples = seeds.get("legislation_triples") or []
        if not triples:
            return ChainResult(chain, "SKIPPED", "no legislation_triples seed")
        t = triples[0]
        params = {"type": t["type"], "year": t["year"], "number": t["number"]}
    else:
        # Generic: try to use the placeholder as the field name on a Model wrapper.
        seed_key = PLACEHOLDER_SEED_MAP.get(placeholder)
        if not seed_key:
            return ChainResult(chain, "SKIPPED", f"no fixture for tool {tool_name!r} and placeholder {placeholder!r}")
        arr = seeds.get(seed_key) or []
        if not arr:
            return ChainResult(chain, "SKIPPED", f"empty seed list {seed_key!r}")
        params = {placeholder: arr[0]}

    try:
        resp = mcp.call_tool(tool_name, params)
    except httpx.HTTPError as e:
        return ChainResult(chain, "FAIL", f"HTTP error {e!r}", {"tool": tool_name, "params": params})
    status, detail, evidence = _evaluate_tool_response(tool_name, resp)
    evidence["tool"] = tool_name
    evidence["params"] = params
    return ChainResult(chain, status, detail, evidence)


def derive_seeds(mcp: MCPClient, seeds: dict) -> dict:
    """Pre-run producer tools to derive seed values for consumer chains.

    Currently derives:
      - _derived_division_id + _derived_division_house from parliament_get_debate_divisions
      - _derived_contribution_ext_id from hansard://debate/{ext}/header — overrides any
        hard-coded contribution_ext_ids seed so that the contribution matches the seeded debate
        (otherwise the resource read fails with "contribution not found in debate")
      - _derived_judgment_eid from judgment://{slug}/index
    """
    out: dict[str, Any] = {}

    ext_ids = seeds.get("debate_ext_ids") or []

    # Division IDs from the seeded debate.
    # Note: divisions carry TWO ID-space values. `id` is Hansard-side and is NOT
    # what votes_get_division accepts (different API). `votes_id` is the
    # cross-resolved Lords/Commons Votes API ID and IS what votes_get_division
    # wants. The cartography on DivisionMatchLite.votes_id explicitly promises
    # "Use as `division_id` input to votes_get_division" — so the audit must
    # source from votes_id, not id, when testing that chain. If votes_id is
    # None (cross-resolve found no match), fall through and let the audit
    # report SKIPPED rather than FAIL on a None.
    if ext_ids:
        try:
            resp = mcp.call_tool("parliament_get_debate_divisions", {"debate_ext_id": ext_ids[0]})
            sc = (resp.get("result", {}).get("structuredContent")
                  or json.loads(resp["result"]["content"][0]["text"]))
            divs = sc.get("divisions") or []
            for d in divs:
                if d.get("votes_id") is not None:
                    out["_derived_division_id"] = d["votes_id"]
                    out["_derived_division_house"] = d.get("house", "Lords")
                    break
        except Exception as e:
            print(f"  (warning: could not derive division_id: {e})", file=sys.stderr)

    # Contribution ext_id from the SAME seeded debate — the resource is debate-scoped, so
    # a hand-curated contribution from a different debate will fail the read. Always pick
    # the first substantive contribution in the seeded debate's header.
    if ext_ids:
        try:
            resp = mcp.read_resource(f"hansard://debate/{ext_ids[0]}/header")
            contents = resp.get("result", {}).get("contents", [])
            if contents:
                txt = contents[0].get("text", "")
                body = json.loads(txt) if txt else {}
                idx = body.get("contributions_index") or []
                # Pick the first contribution that has a non-null ext_id and a member_id
                # (skips procedural / column-marker items)
                for entry in idx:
                    cid = entry.get("contribution_ext_id")
                    mid = entry.get("member_id")
                    if cid and mid:
                        out["_derived_contribution_ext_id"] = cid
                        break
        except Exception as e:
            print(f"  (warning: could not derive contribution_ext_id: {e})", file=sys.stderr)

    # eId from a judgment index resource. Best effort; skipped if not seeded.
    slugs = seeds.get("judgment_slugs") or []
    if slugs:
        try:
            resp = mcp.read_resource(f"judgment://{slugs[0]}/index")
            contents = resp.get("result", {}).get("contents", [])
            if contents:
                txt = contents[0].get("text", "")
                # The index format is "para_N: first line" — grab the first eId.
                m = re.match(r"\s*(\S+):", txt)
                if m:
                    out["_derived_judgment_eid"] = m.group(1)
        except Exception as e:
            print(f"  (warning: could not derive judgment eId: {e})", file=sys.stderr)

    return out


# ── 5. Main ─────────────────────────────────────────────────────────────


def main() -> int:
    if not os.environ.get("RUN_LIVE_AUDIT"):
        print("RUN_LIVE_AUDIT not set — skipping live audit.")
        print("Run with:  RUN_LIVE_AUDIT=1 uv run python tests/audit_cartography_chains.py")
        return 0

    # Gateway up?
    try:
        r = httpx.get(HEALTH_URL, timeout=5.0)
        if r.status_code != 200:
            print(f"❌ Gateway at {HEALTH_URL} returned {r.status_code}. Start it first.")
            return 2
    except httpx.HTTPError as e:
        print(f"❌ Gateway unreachable at {HEALTH_URL}: {e!r}")
        return 2

    seeds = load_seeds()
    chains = collect_chains()

    # Deduplicate exact-shape chains; many models repeat the same description.
    seen: set[tuple[str, str, str]] = set()
    unique: list[Chain] = []
    for c in chains:
        key = (c.source_symbol, c.target_kind, c.target_name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    mcp = MCPClient(GATEWAY_URL)
    mcp.initialize()
    derived = derive_seeds(mcp, seeds)

    results: list[ChainResult] = [run_chain(c, mcp, seeds, derived) for c in unique]

    # Report
    print(f"\n=== Live cartography-chain audit ===")
    print(f"Gateway:     {GATEWAY_URL}")
    print(f"Seeds:       {SEEDS_PATH.relative_to(ROOT)}")
    print(f"Chains discovered (deduped): {len(unique)} of {len(chains)} raw matches")
    print()

    by_status: dict[str, list[ChainResult]] = {"PASS": [], "FAIL": [], "EMPTY": [], "SKIPPED": []}
    for r in results:
        by_status[r.status].append(r)

    print(f"  ✓ PASS:    {len(by_status['PASS'])}")
    print(f"  ❌ FAIL:   {len(by_status['FAIL'])}")
    print(f"  ⚠️ EMPTY:  {len(by_status['EMPTY'])}")
    print(f"  ⏭ SKIPPED: {len(by_status['SKIPPED'])}")
    print()

    if by_status["FAIL"]:
        print("=== FAIL ===")
        for r in by_status["FAIL"]:
            print(f"  ❌ {r.chain.short_id}")
            print(f"     Symbol:   {r.chain.source_symbol}  in  {r.chain.source_file}")
            print(f"     Excerpt:  '{r.chain.description_excerpt}'")
            print(f"     Detail:   {r.detail}")
            if r.evidence:
                evd = {k: v for k, v in r.evidence.items() if k != "resp"}
                print(f"     Evidence: {evd}")
            print()

    if by_status["EMPTY"]:
        print("=== EMPTY (passed call, empty result vs known-populated seed) ===")
        for r in by_status["EMPTY"]:
            print(f"  ⚠️ {r.chain.short_id}")
            print(f"     Symbol:   {r.chain.source_symbol}")
            print(f"     Detail:   {r.detail}")
            print()

    if by_status["SKIPPED"]:
        print("=== SKIPPED (no seed available) ===")
        for r in by_status["SKIPPED"]:
            print(f"  ⏭ {r.chain.short_id}  ({r.detail})")
        print()

    if by_status["PASS"]:
        print(f"=== PASS ({len(by_status['PASS'])}) ===")
        for r in by_status["PASS"]:
            print(f"  ✓ {r.chain.short_id}")

    print()
    if by_status["FAIL"] or by_status["EMPTY"]:
        print("RESULT: BROKEN — see FAIL/EMPTY above. Either fix the chain or drop the cartography.")
        return 1
    print("RESULT: clean — all discovered cartography chains pass against the live gateway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
