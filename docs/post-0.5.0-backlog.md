# Post-0.5.0 backlog

Pre-existing drift and follow-up work surfaced during the hardening pass that shipped as **0.5.0** (production at `uk-legal-mcp.fly.dev`, currently untagged pending soak). "v1.2" used in earlier filenames and item IDs was project-shorthand for "the next batch of work" — it is NOT a semver promise. Actual semver targets are tagged on each item.

Semver targets in use:

- **0.5.1 (patch)** — bug fixes and small description tweaks; no API change.
- **0.6.0 (minor)** — new optional features that don't break existing behaviour (new tool, new resource, new envelope field).
- **infrastructure** — CI / tests / audit scripts; no server release.
- **plugin** — work in `uk-legal-plugins/` (separate repo, separate cadence).
- **Phase B** — the 5 new skills (deferred 1–2 weeks per main plan).

Item IDs (v1.2-N) are retained for stability of cross-references in commits, observations, and prior conversation.

Last updated: 2026-05-30 (post 0.5.0 close-off).

## Server-side drift (carried over from A5 audit work)

### v1.2-1 — `audit_parliament_responses.py` reports drift  *[⬜ OPEN — NOT done in 0.5.1; carried forward to 0.6.0]*

22 consumed-undeclared fields + 1 semantic-mismatch heuristic hit. Pre-existing in v1; not an A5 regression. Each undeclared field is a silent-substitution risk: the parser reads a `.get("Field")` whose name doesn't appear in the Swagger response schema, so an upstream rename would silently return `None` rather than fail loudly.

**Fix:** walk the audit output, classify each finding as (a) genuinely undeclared (file a downstream bug or remove the read), (b) declared under a different path (update the field name), (c) optional / nullable in spec (annotate). The semantic-mismatch heuristic hit deserves manual review — it flags a `Rank` field consumed as a count, which is the Obs 173 lie shape.

**Trigger to run:** `uv run python tests/audit_parliament_responses.py`

### v1.2-2 — `parliament_lookup_by_column` Source enum semantics  *[target: 0.6.0 (minor — new resource template)]*

In A3 the rich publication-state enum documentation (Rolling / Daily Part / Bound Volume / Historic) was trimmed to fit the 150-word cap. The trimmed description still names the four states but loses the OSCOLA citation-finality rationale.

**Fix:** move the detailed enum docs into a `parliament://source-enum` resource. Tool description stays terse; lawyers who need to understand why source matters for OSCOLA finality read the resource.

### v1.2-3 — `case_law_search` iteration count on uncertain matches  *[✅ SHIPPED 0.5.1 — commit 49b52a4]*

Smith v HMRC dogfeed trace took 13 tool calls (8 of them grep iterations on a single candidate judgment). The agent did the right thing — verifying before claiming — but the description could nudge for narrower court+year filtering first.

**Fix:** add "AFTER calling, narrow with court + year filters before grep-iterating across full judgments" to `case_law_search` description. Re-dogfeed Smith v HMRC and confirm iteration count drops.

### v1.2-4 — Add `citations_format_oscola` formatter tool  *[target: 0.6.0 (minor — new tool)]*

`citations_resolve` returns structured fields; the agent constructs the OSCOLA string from those fields itself. A discrete `citations_format_oscola` tool would gate formatting behind a successful resolve and refuse to operate on unresolved input — making the verify-then-format discipline impossible to bypass.

**Fix:** new tool in `citations/tools.py`. Input: a resolved citation dict from `citations_resolve`. Output: the formatted OSCOLA string. Refuse with `status: upstream_validation` if the input doesn't look like a resolved citation.

### v1.2-5 — Wire live tests into nightly CI  *[target: infrastructure (no server release)]*

9 tests are marked `live` and currently deselected in regular runs. They hit upstream APIs and would catch upstream-shape changes the static audit scripts miss.

**Fix:** add a GitHub Actions workflow that runs `uv run pytest -m live` on a nightly schedule. Failure opens an issue; no merge gate.

### v1.2-6 — Audit script as CI guard  *[target: infrastructure (no server release)]*

`tests/audit_descriptions.py --check` exits non-zero if any tool description exceeds the 150-word cap (shipped in 0.5.0).

**Fix:** add to GitHub Actions on every PR. Prevents future description bloat from landing without an explicit override.

### v1.2-8 — Resource URI mentions ambiguous for tool-only clients  *[✅ baseline SHIPPED 0.5.1 — commit 49b52a4; rich workflow tuning still Phase B]*

Several tool descriptions point at resource URIs with phrasing like *"read `hansard://debate/{debate_ext_id}/header`"* or *"drill into the `hansard://` resource family"*. Native-resource clients (Claude / Codex / Inspector) parse this correctly. ChatGPT (tool-only via `ResourcesAsTools`) is ambiguous — does "read X://" mean the resources protocol it doesn't speak, or a tool call shape? When the agent tries the native form, ChatGPT can't execute it and falls back to a generic block message (looks like a safety check; it's actually "I can't process this MCP primitive").

**Fix:** rewrite every resource URI mention in tool descriptions to be paired with a tool-call alternative explicitly named:

> AFTER calling, drill into full content via `read_resource(uri="hansard://debate/{debate_ext_id}/header")` — or, equivalently, call `parliament_get_debate_contributions(debate_ext_id)` which returns the same content as a structured tool response.

Two pointers in one. Native clients keep the protocol; tool-only clients see both the wrapped form AND the named companion tool. For case_law this is already clean (`judgment_get_header/_index/_paragraph` mirror `judgment://`); the gap is parliament + legislation.

**Sites:** `parliament_search_hansard`, `parliament_get_debate_contributions`, `parliament_lookup_by_column`, `legislation_get_section`, `legislation_get_toc`.

**Two-tier fix — split between description layer and skill layer:**

The 30-min description rewrite is the *baseline fix* for the ChatGPT cohort (which has no skills layer to lean on). The *real workflow tuning* belongs in Phase B's 5 new skills (deferred 1–2 weeks per main plan). Skills can carry the verbose "if user supplies a Hansard URI, call read_resource directly; otherwise compose find_member → get_debate_contributions and never construct a hansard:// URI yourself" guidance that tool descriptions cannot — no 150-word cap, no neutrality constraint on procedural specificity.

| Layer | Carries | Word budget |
|---|---|---|
| Tool descriptions | terse 4-part pattern, USE WHEN clauses, AFTER call chain hints | ≤150 words |
| Module instructions blobs | named tools + workflow chain + resource URIs + envelope shape | no hard cap, but ChatGPT loads them on every initialize so terse wins |
| **Skills (uk-legal-plugins)** | full workflow logic, lawyer phrasing, anti-fabrication, both-sides discipline, when-to-use-which-tool decision trees | **no cap; cohort has opted into the workflow** |

The implication for v1.2 prioritisation: rather than expanding tool descriptions to cover every ChatGPT edge case (descriptions get bloated and stale), invest the workflow-tuning effort in Phase B skills. Tool descriptions stay at the "good enough for the least-capability client" tier; skills are where the rich, opinionated procedural guidance lives.

### v1.2-9 — `bills_search_bills` session_id Field description  *[✅ SHIPPED 0.5.1 — commit 49b52a4 (note: actual param is `session`, not `session_id`)]*

Dogfeed trace showed the agent attempting `session_id="2025"` (year string) and failing, then recovering with `session_id=40` (numeric session). The Field description should be explicit:

> Numeric session identifier (e.g. `40` for the 2024–25 session, `39` for 2023–24). NOT a year string. If you only know the year, omit this parameter and filter on the result instead.

~5 minute fix in `bills/models.py` (or wherever the input model lives).

### v1.2-11 — `list_resources` empty on ChatGPT (ResourcesAsTools double-encoding)  *[retargeted 0.6.0 — design choice, NOT a 0.5.1 blocker; see 2026-05-30 update below]*

Test 5 dogfeed trace shows tool calls 2 and 3 both call `list_resources` and receive `"No tool response"` (1 token output). The agent retries once, gets nothing again, and proceeds via named tools (`legislation_get_*`, `parliament_search_*`). Working around it doesn't fix it — the ResourcesAsTools transform wired in A1.5 (commit ac3e565) isn't surfacing the resource catalog at runtime for ChatGPT-via-MCP.

**Probe:** verify `Client(gateway).list_resources()` returns the expected `judgment://`, `hansard://`, `legislation://`, `server://about` templates against production. If it does, the transform is broken on ChatGPT's side. If it doesn't, the transform isn't registering the resources correctly.

**Why it matters:** without `list_resources` returning the catalog, the agent has no discoverable way to learn that resource URIs exist. It falls back to named tools (which is fine, often better), but the resources-as-tools surface is dead weight if it can't be discovered. Either fix the discovery or remove the transform.

**Pairs with v1.2-8:** if list_resources doesn't discover the catalog, the URI-mention rewrite in v1.2-8 is the only path agents have to learn about resources. So v1.2-11 makes v1.2-8 more urgent for the ChatGPT cohort.

**UPDATE 2026-05-30 (0.5.1 staging dogfeed) — DIAGNOSED, retargeted 0.6.0, NOT a 0.5.1 blocker.** Root cause confirmed end-to-end:

- A FastMCP `Client` probe (local + production + staging) returns the full **3635-char** catalog — so the original "close as not reproduced" was wrong: it tested the WRONG client. The symptom is ChatGPT-transport-specific.
- ChatGPT staging dogfeed (3 separate traces) shows `list_resources` returning **1 token of output** — empty to the agent.
- Live staging Fly logs + `/metrics` during a real call prove the **server is healthy**: `Tool 'list_resources' completed`, `List resources completed in 4.23ms`, `POST /mcp 200 OK`, `status=ok`, sends 3635 chars with `structured_content = {"result": "<stringified-JSON>"}`.
- **Conclusion:** not a server bug. It's the `ResourcesAsTools` **double-encoding** (`{result: "<stringified json>"}`) — the exact shape the gateway's own comment flags as why the named companion tools return clean dicts. ChatGPT's MCP client can't consume the double-encoded structured output; native/FastMCP clients can.
- **Not blocking:** all 5 staging dogfeed prompts succeeded via named tools (v1.2-8 working as designed — agents reach content via `parliament_get_debate_contributions`, `legislation_get_section`, `judgment_get_header`, not via `list_resources` discovery).
- **Fix is a design choice (target 0.6.0):** either (A) remove the dead `ResourcesAsTools` `list_resources`/`read_resource` bridge (it's dead weight on ChatGPT; native clients have native resources), or (B) add a clean NAMED catalog tool (e.g. `legal_resource_catalog`) returning a plain dict — mirroring the existing companion pattern — so ChatGPT CAN discover resources. Decide A vs B against whether resource discovery (vs the already-working named-tool path) is worth a tool slot.
- N2's annotation quartet on the bridge tools is confirmed visible on the ChatGPT wire (every bridge call shows READ + OPEN WORLD), so if the bridge is KEPT, it's at least properly annotated; if removed, N2's scope shrinks to whatever bridges remain.

**UPDATE 2026-05-30 (cross-client test — REVERSES option A).** Tested the bridge as consumed by **Claude Code** (a native+tool MCP client) against production, directly:
- **Claude Code consumes the `{result: "<stringified-JSON>"}` bridge output PERFECTLY** — both `list_resources` (full 9-entry catalog with descriptions) and `read_resource` (full `server://about`) come through readable. The double-wrap is **a ChatGPT-specific consumption limitation, NOT universal.**
- **Native `resources/list` returns ONLY static resources** (just `server://about`) — the **8 templates are NOT listed** by the native protocol on any client. So the bridge `list_resources` tool is the **only surface that discovers the 8 templated resources with their descriptions** — and Claude Code/Codex can use it.
- **Therefore option A (remove the bridges) is WRONG** — it would delete the only template-discovery surface for native-tool clients (Claude Code, Codex, Cowork) that actually consume it, while helping ChatGPT nothing (ChatGPT already ignores it and succeeds via twin tools).
- **Revised recommendation: KEEP the bridges (and N2's annotations stay load-bearing).** v1.2-11 resolves to: ChatGPT cannot consume the double-wrapped discovery surface — a known ChatGPT MCP-client limitation, non-blocking (twins carry ChatGPT). N2 was the right call (it correctly annotates a surface Claude/Codex genuinely use).
- **Optional additive 0.6.0 (NOT removal):** a clean NAMED `legal_resource_catalog` tool returning a plain dict would give ChatGPT a consumable discovery path too — additive, keeps the bridge for native clients. Low priority: ChatGPT already succeeds via twins, so resource discovery adds marginal value for that cohort.
- **Secondary finding (all clients):** the raw resources return **unparsed CLML/XML** (a section read = ~30KB raw XML); the domain twins (`legislation_get_section` etc.) return *parsed* text + extent + in-force. So for CONTENT the domain tools beat raw resources for every client; the resources/bridge are a DISCOVERY + raw-access affordance, not the primary content path.

### v1.2-10 — Pre-merge dogfeed/description grep  *[target: infrastructure (no server release)]*

Per Obs 224, the dogfeed test cases (Pannick, R v Brown, Renters' Rights Bill, 14 Oct 2025) overlapped with description examples — the tests were partly pedagogy, not validation. Add a `tests/audit_dogfeed_contamination.py` script that takes the active dogfeed prompt list and greps src/ for each named entity. Fail-loud if any test name appears in any LLM-visible surface.

Pair with `audit_descriptions.py --check` as the close-off gate before declaring a hardening pass done.

## Plugin-side gap (surfaced during close-off)

### v1.2-7 — Only 4 of 11 plugins reference `uk-legal-mcp`  *[target: plugin (uk-legal-plugins repo, separate cadence)]*

Plan assumed all 11 plugins in `uk-legal-plugins/` point at `uk-legal-mcp.fly.dev/mcp`. Reality:

| Reference uk-legal-mcp | Don't reference uk-legal-mcp |
|---|---|
| ip-legal-uk | ai-governance-legal-uk |
| law-student-uk | commercial-legal-uk |
| employment-legal-uk | legal-clinic-uk |
| corporate-legal-uk | litigation-legal-uk |
|   | privacy-legal-uk |
|   | product-legal-uk |
|   | regulatory-legal-uk |

This is significant because **regulatory-legal-uk is the reference template skill** per the v1.1 hardening plan (`regulatory-legal-uk:reg-feed-watcher`). Skills in that plugin name `uk-legal-mcp` tools directly in their prose, but the plugin's `.mcp.json` doesn't surface the server — so an agent loading the plugin wouldn't see those tools registered.

**Possible explanations:**
1. Deliberate scope choice — those plugins focus on domains served by other MCPs (govuk, due-diligence, etc.) and uk-legal isn't needed.
2. Authoring drift — uk-legal-mcp WAS in the .mcp.json and got dropped.
3. Plugin-author misunderstanding of how skills + MCP plumbing combine.

**Fix:** audit each of the 7 plugins:
- For each plugin's skills, does any skill reference a `uk-legal-mcp` tool by name (e.g. `parliament_search_hansard`, `case_law_search`, `legislation_get_section`, `citations_resolve`, `bills_search_bills`)?
- If yes: the plugin SHOULD reference `uk-legal-mcp` in `.mcp.json`. Add it.
- If no: confirm by reading the plugin's CLAUDE.md or plugin.json description that uk-legal-mcp is genuinely out of scope.

**Priority:** high if any skill in those 7 plugins names a uk-legal-mcp tool — that means the skill silently fails when the plugin loads alone.

## Deferred upgrades (surfaced during 0.5.1)

### NEW — FastMCP 3.2.4 → 3.3.1 upgrade evaluation  *[⬜ OPEN — 0.6.0 candidate]*

A Devin-generated PR (#16, now closed) proposed bumping `fastmcp==3.2.4 → >=3.3.1,<4`. The PR was closed and its valuable half — `tests/test_gateway.py` — was salvaged onto main (commit `ddbf383`) WITHOUT the bump. The bump itself is deferred: 3.3.x may shift the transform internals that N2 relies on (the custom `Transform.list_tools` subclass that stamps bridge-tool annotations — see `gateway.py`).

**Before adopting:** re-run the Phase-0 introspection (confirm `Transform`/`ToolTransformConfig` API unchanged), run the full non-live suite + `fastmcp inspect`, and re-dogfeed the resource-bridge surface on ChatGPT + Claude Code. Do NOT blind-merge a minor FastMCP bump.

## Notes on close-off discipline

These all surfaced during v1.1 close-off. Per Obs 223, the discipline going forward: when a release is being scoped, deliberately sweep adjacent surfaces (plugins, downstream consumers, sibling MCPs) for gaps, not just the surface where the headline failure manifested.

The 0.5.0 hardening + 0.5.1 patch are shipped to production `uk-legal-mcp.fly.dev/mcp`, tagged `v0.5.1`, and on PyPI. ChatGPT staging dogfeed + Claude Code cross-client checks confirm it landed cleanly. The 0.5.1-shipped items above (v1.2-3/8/9, keep-bridges v1.2-11) are done; the rest are carried forward.
