# `case_law_get_judgment` refactor — pilot

**Repo:** `uk-legal-mcp`
**File:** [`src/modules/case_law/tools.py`](../../src/modules/case_law/tools.py)
**Current commit:** `a99f734` (final get_judgment fix from 2026-04-13 session)
**Target tier(s):** 2 (navigator), 3 (leaf), 4 (resource)

This is the first refactor applying the navigator + leaf pattern from
[README.md](README.md). It's the pilot — the template used to prove the
pattern works end-to-end, measured by the tests/live harness, before the
fleet-wide migration listed in the README.

## Why this tool

`case_law_get_judgment` is the biggest known context bomb in uk-legal-mcp.
It already has a partial solution (`max_chars` escape hatch) which proves
the team understands the problem, but the blunt byte-cap approach is
useless for lawyers — truncating a judgment at 50,000 characters chops
mid-clause, mid-paragraph, and strips the closing reasoning a lawyer
actually needs.

- **Current context cost:** not cleanly measured yet (the matrix run on
  2026-04-14 hit a slug-extraction bug and got a 404 instead of a real
  judgment). Pre-bug measurements during the 2026-04-13 session put it at
  ~12,500 tokens for a 50k-char slice, and the LegalDocML XML for a real
  judgment is 225,000 chars total (~55,000 tokens) if unsliced.
- **Current worst-case:** ~55k tokens if a caller sets `max_chars=400000`
  (the schema max) and fetches a big Court of Appeal judgment.
- **Symptom:** LLM can either get a useless truncated blob or a 55k-token
  full-text dump that eats 25%+ of the context window in one call. Neither
  option composes with reading multiple related judgments.

## Current shape

```python
@mcp.tool(
    name="get_judgment",
    annotations={"title": "Get Full Judgment Text", "readOnlyHint": True, ...},
)
async def case_law_get_judgment(params: CaseLawGetJudgmentInput, ctx: Context) -> dict:
    """Retrieve the full LegalDocML XML for a judgment by TNA URI slug..."""
```

With input:

```python
class CaseLawGetJudgmentInput(BaseModel):
    uri: str = Field(..., description="TNA judgment URI slug, e.g. 'uksc/2024/12'")
    max_chars: int = Field(50000, ge=1000, le=400000)
```

**Return type:** `-> dict`
**Current fields returned:** `uri`, `format` (`"legaldocml-xml"`), `content`
(the XML string), `truncated`, `original_length`
**What's unbounded:** `content` — it's the entire LegalDocML document as
a string, capped only by the blunt `max_chars` byte counter.

## Target shape

Three new tools plus a resource template. The old tool is kept as a
deprecated escape hatch with a tighter default.

### New tool 1 — tier 2 (navigator): `case_law_get_toc`

Returns the structure of one judgment — metadata, list of sections with
stable IDs, nothing from the body text. An LLM reading this can pick which
section(s) it wants to drill into without paying for the content.

```python
@mcp.tool(
    name="get_toc",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def case_law_get_toc(params: CaseLawTocInput, ctx: Context) -> dict:
    """Retrieve the table of contents for a judgment by TNA URI slug.

    Returns metadata (parties, court, judges, date, neutral citation)
    and a flat list of sections with stable IDs, titles, and approximate
    sizes. Does NOT return the body text — use case_law_get_section for
    each section the caller actually needs.

    For typical use: call this once, then case_law_get_section for the
    1-3 paragraphs that matter. Total cost ~3k tokens vs ~55k for the
    full document.
    """
```

**Input:** `uri: str` (same TNA slug as existing tool)

**Return dict fields:**

| Field | Type | Notes |
|---|---|---|
| `uri` | `str` | TNA slug, echoed back |
| `court` | `str` | Court name e.g. "UKSC" |
| `ncn` | `str` | Neutral citation e.g. "[2024] UKSC 12" |
| `parties` | `list[str]` | Claimant, defendant, etc. |
| `judges` | `list[str]` | Presiding judges |
| `date` | `str` | ISO date |
| `sections` | `list[dict]` | TOC entries — see below |
| `total_sections` | `int` | Count of sections |
| `original_length` | `int` | Characters in the full LegalDocML for reference |

Each entry in `sections` is:

```python
{"id": "para-42", "title": "Conclusion", "level": 1, "char_count": 1820}
```

**Estimated context cost:** 500–1,500 tokens. A UKSC judgment typically has
30-80 top-level paragraphs; each TOC entry is ~30 characters, so the whole
TOC is ~3,000 characters (~750 tokens).

### New tool 2 — tier 3 (leaf): `case_law_get_section`

Returns ONE section of one judgment, verbatim, unchopped.

```python
@mcp.tool(
    name="get_section",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def case_law_get_section(params: CaseLawSectionInput, ctx: Context) -> dict:
    """Retrieve one section of a judgment by URI and section ID.

    Use case_law_get_toc first to discover section IDs. Returns the
    full verbatim text of the requested section — no truncation,
    no summary. A single section is typically 500-3,000 tokens.

    If a caller needs multiple sections, make multiple calls. This is
    cheaper overall than fetching the whole document because most
    callers only need 1-3 sections, not all of them.
    """
```

**Input:**

```python
class CaseLawSectionInput(BaseModel):
    uri: str = Field(..., description="TNA judgment URI slug")
    section_id: str = Field(..., description="Section ID from case_law_get_toc, e.g. 'para-42'")
    format: Literal["text", "xml"] = Field("text", description=(
        "Return format. 'text' is plain-text (LLM-friendly). 'xml' is "
        "the raw LegalDocML fragment (for programmatic consumers)."
    ))
```

**Return dict fields:**

| Field | Type | Notes |
|---|---|---|
| `uri` | `str` | TNA slug, echoed back |
| `section_id` | `str` | Section ID, echoed back |
| `title` | `str` | Section title if present |
| `content` | `str` | The section text verbatim (plain text or XML depending on `format`) |
| `char_count` | `int` | Length of `content` |

**Estimated context cost:** 1,000–3,000 tokens for a typical paragraph.
A dense UKSC conclusion paragraph might be 4,000 tokens; a short procedural
paragraph 200 tokens. The tool never truncates — whatever the paragraph
is, it's returned whole. If a caller accidentally asks for a 50k-token
section, that's their budget; the tool trusts the navigator's `char_count`
hint.

### Resource template — tier 4: `judgment://{court}/{year}/{number}`

```python
@mcp.resource("judgment://{court}/{year}/{number}")
async def judgment_resource(
    court: str, year: str, number: str, ctx: Context
) -> str:
    """A UK case law judgment as an MCP resource.

    Reading this resource returns the judgment's table of contents
    (same shape as case_law_get_toc). The LLM can hold the resource
    URI without triggering a fetch; the client only reads the content
    when the LLM explicitly requests it.
    """
```

**URI scheme:** `judgment://{court}/{year}/{number}`
**Example URIs:**
- `judgment://uksc/2024/12` — Miller II
- `judgment://ewca/civ/2023/450`
- `judgment://ewhc/ch/2022/1234`

Content: same as `case_law_get_toc`. The resource is the navigator; the
two tools above are how the LLM reads leaves.

## Deprecation of the old tool

- [x] **Kept as escape hatch.** `case_law_get_judgment` stays in the
      registry with:
    - Tool description rewritten to say: "For structured drill-down, use
      `case_law_get_toc` + `case_law_get_section`. This tool is retained
      for cases where the caller genuinely wants the entire LegalDocML
      XML blob (e.g. downstream legal NLP pipelines)."
    - `max_chars` default lowered from `50000` to `10000` so a typical
      accidental call costs ~2,500 tokens instead of ~12,500.
    - `max_chars` hard max stays at `400000` so deliberate callers can
      still request the whole thing.
    - The field description on `max_chars` adds: "Prefer `case_law_get_section`
      for reading specific paragraphs — this parameter is a last resort."

## Migration steps

1. **Add input models** — `CaseLawTocInput`, `CaseLawSectionInput` in
   [`src/modules/case_law/tools.py`](../../src/modules/case_law/tools.py).
   Keep the existing `CaseLawGetJudgmentInput` unchanged.
2. **Parse LegalDocML structure** — add a helper
   `_parse_judgment_toc(xml_bytes) -> TocResult` using `lxml` that walks
   `akomaNtoso/judgment/judgmentBody` and extracts the section tree with
   stable IDs from the `eId` attribute. Test against 3 real judgments
   (UKSC, EWCA Civ, EWHC Ch) to handle the structural variation.
3. **Implement `case_law_get_toc`** using the helper. Fetch from TNA,
   parse, return the dict. Cache the full XML in memory keyed by URI so
   the subsequent `get_section` call doesn't refetch.
4. **Implement `case_law_get_section`** — uses the cached XML from step 3
   (or refetches if not cached), walks to the requested `eId`, extracts
   the subtree, converts to plain text or returns XML based on `format`.
5. **Register the `judgment://` resource template** in
   [`src/modules/case_law/resources.py`](../../src/modules/case_law/resources.py).
6. **Update `case_law_get_judgment` tool description** to point at the
   new tools, and lower `max_chars` default to 10,000.
7. **Add scenarios** to [`tests/live/run_matrix.py`](../../tests/live/run_matrix.py)
   for `case_law_get_toc(uri)` and `case_law_get_section(uri, "para-1")`.
8. **Run the harness before** the refactor on main:
   `.venv/bin/python -m tests.live.run_matrix | tee before.txt`
9. **Commit** the implementation on a branch.
10. **Run the harness after** on the branch, paste both outputs into the
    Validation section below.
11. **Merge** only if the numbers pass the validation rule.

## Validation

Fill in after test implementation.

| Scenario | Before (tokens) | After (tokens) | Delta |
|---|---:|---:|---:|
| `case_law_search` ("negligence duty of care") | 12,249 | _(unchanged)_ | 0 |
| `case_law_get_judgment` (legacy, max_chars=50000) | ~12,500 | _(with new default max_chars=10000)_ ~2,500 | -80% |
| `case_law_get_toc` (new) | n/a | _(target <1,500)_ | n/a |
| `case_law_get_section` (new, single paragraph) | n/a | _(target <3,000)_ | n/a |
| **Drill-down path** (search → toc → 2 sections) | ~24,500 (search + legacy full) | _(target <18,000)_ | -27% |

The drill-down row is the one that matters. If a realistic usage pattern
(find a judgment, navigate its structure, read the two paragraphs that
answer the question) costs less than today's search + legacy fetch, the
refactor is validated.

Passes the validation rule? **Pending implementation.**

## Open questions

- **LegalDocML variation across courts.** Does the `eId` attribute exist
  consistently on all top-level sections across UKSC, EWCA, EWHC, UKUT, and
  FTT judgments? Step 2 of the migration plan needs to answer this with
  real test data before the navigator is reliable.
- **Section ID stability.** TNA occasionally republishes judgments with
  corrected typography. Do the `eId`s stay stable across republish, or do
  they shift? If they shift, cached section IDs go stale — mitigation is
  to include `eId` in the TOC response as-is rather than computing our own.
- **Caching strategy for chained calls.** Step 3-4 assumes an in-memory
  cache keyed by URI so `get_toc` → `get_section` doesn't double-fetch.
  FastMCP's `lifespan` already owns the httpx client; the cache should
  live alongside it. TTL: 10 minutes (TNA updates infrequently).
- **Resource template vs navigator tool — overlap.** Both `case_law_get_toc`
  and the `judgment://` resource return the same data. Is that duplication
  a problem, or a feature (different access patterns for different clients)?
  Lean towards feature: tools are invoked explicitly, resources are held
  by reference — they serve different LLM patterns.

## References

- Pattern spec: [README.md](README.md)
- Refactor template: [TEMPLATE.md](TEMPLATE.md)
- Current implementation: [`src/modules/case_law/tools.py:162`](../../src/modules/case_law/tools.py#L162)
- Harness: [`tests/live/run_matrix.py`](../../tests/live/run_matrix.py)
- Lesson 0 and 33: `bouch-pages/docs/mcp-server-lessons.md`
- FastMCP resources docs: https://gofastmcp.com/servers/resources
- TNA LegalDocML schema reference: http://docs.oasis-open.org/legaldocml/akn-core/v1.0/os/
