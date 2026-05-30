# Audit: current tool descriptions (snapshot)

Regenerated 2026-05-30 from `Client(gateway).list_tools()` on `feat/hardening-v1.1`.

Per `docs/chatgpt-workflow-encoding.md`: descriptions should stay ≤150 words.
Word counts per tool shown next to the name for quick scan.

Per Obs 217: the per-module counts below are this-audit-only; don't propagate.

## Framework-provided (transform-injected) — NOT in scope for Phase A3

Owned by FastMCP (PromptsAsTools + ResourcesAsTools transforms wired in gateway.py).

### `get_prompt` (30 words)

**Params** (2): arguments, name

```
Get a prompt by name with optional arguments.

Returns the rendered prompt as JSON with a messages array.
Arguments should be provided as a dict mapping argument names
to values.
```

### `list_prompts` (15 words)

**Params** (0): (none)

```
List all available prompts.

Returns JSON with prompt metadata including name, description,
and optional arguments.
```

### `list_resources` (28 words)

**Params** (0): (none)

```
List all available resources and resource templates.

Returns JSON with resource metadata. Static resources have a
'uri' field, while templates have a 'uri_template' field with
placeholders like {name}.
```

### `read_resource` (35 words)

**Params** (1): uri

```
Read a resource by its URI.

For static resources, provide the exact URI. For templated
resources, provide the URI with template parameters filled in.

Returns the resource content as a string. Binary content is
base64-encoded.
```

## Module: `case_law` (2 tools — this-audit count only)

### `case_law_grep_judgment` (81 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a judgment slug and want to find paragraphs whose text matches a pattern.

Returns a list of `{eId, snippet, match}` hits — small per-paragraph
snippets centred on the match. AFTER calling, read full paragraphs via
judgment_get_paragraph(slug, eId) or the judgment://{slug}/para/{eId}
resource.

Use case: content search within one judgment (e.g. "negligence", "test
for foreseeability", "Donoghue"). For paragraph-number navigation by
eId, call judgment_get_index instead.

Pattern is regex; if it doesn't compile, falls back to literal substring
search.
```

### `case_law_search` (114 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching UK case law by party names, court, judge, date, or free-text query.

Returns paginated judgment summaries: neutral citation, court, dates, slug,
stable TNA URI. AFTER calling: pass slug into judgment_get_header /
judgment_get_index / judgment_get_paragraph (or the judgment:// resource
family) for content; pass the neutral citation into citations_resolve
to verify before constructing an OSCOLA citation; use
case_law_grep_judgment to find text within a single judgment.

Coverage: TNA Find Case Law indexes UK judgments from roughly the early
2000s onwards. For older authorities, search for a modern judgment that
quotes them and read that paragraph.

Authoritative source for UK case law. Web search returns out-of-date or
unstable URLs — do not supplement.
```

## Module: `judgment` (3 tools — this-audit count only)

### `judgment_get_header` (44 words)

**Params** (1): slug

```
USE THIS TOOL WHEN you have a judgment slug and need metadata (parties, judges, neutral citation, court, dates).

Call case_law_search FIRST to get the slug. AFTER calling, use
judgment_get_index to discover paragraphs, then judgment_get_paragraph to
read specific ones. Authoritative source for UK judgment metadata.
```

### `judgment_get_index` (55 words)

**Params** (1): slug

```
USE THIS TOOL WHEN you have a judgment slug and want the paragraph navigation index (eId + preview line for every paragraph).

Call case_law_search FIRST to get the slug. AFTER calling, pass an eId
from the returned list into judgment_get_paragraph to read that paragraph's
full text, or use case_law_grep_judgment for content search across all
paragraphs.
```

### `judgment_get_paragraph` (41 words)

**Params** (2): eId, slug

```
USE THIS TOOL WHEN you have a judgment slug + LegalDocML eId and want that paragraph's full text.

Call judgment_get_index FIRST to discover available eIds (or use
case_law_grep_judgment to locate paragraphs by content). Returns the
paragraph XML content (400–1,700 tokens typical).
```

## Module: `legislation` (3 tools — this-audit count only)

### `legislation_get_section` (99 words)

**Params** (1): params

```
Retrieve a specific section of a UK Act or Statutory Instrument.

Returns the full section text, territorial extent, in-force status,
and prospective flag. Content is capped per max_chars (default 10,000,
~2,500 tokens) — raise max_chars for unusually long definition
sections. Check content_truncated in the response to see if it was cut.

IMPORTANT: Always check `extent` — a section may apply to England &
Wales but not Scotland or Northern Ireland.

Alternative: read the resource template
`legislation://{type}/{year}/{number}/section/{section}` to get raw
CLML XML directly. Use this tool when you want the parsed structured
response (extent, in-force, version_date) instead of raw XML.
```

### `legislation_get_toc` (93 words)

**Params** (1): params

```
Retrieve the table of contents for a UK Act or SI.

Returns structural elements (parts, chapters, sections, schedules) with XML id
and title, e.g. 'section-47: Definitions'. When calling legislation_get_section,
pass only the numeric part — use '47', not 'section-47'.

Large statutes (Companies Act 2006 has 1300+ items) are paginated
via offset/limit. Check has_more and total_items on the response.

Alternative: read the resource template
`legislation://{type}/{year}/{number}/toc` for the full TOC as a
newline-separated `id: title` string (no pagination). Use this tool
when you need the structured `LegislationTOC` response with
offset/limit/has_more for stepping through Companies-Act-scale lists.
```

### `legislation_search` (95 words)

**Params** (1): params

```
Search UK legislation on legislation.gov.uk.

Returns ranked results: title, type, year, number, and legislation.gov.uk URL.

Filter discipline: `type` and `year` are exact-match. Use them only when you
already know the value. For currency-driven searches (e.g. "the recent
Renters' Rights Act"), query by phrase alone and read the year from the
returned results — guessing a year and then filtering by it zeroes the
result set when the guess is wrong.

For broader concept queries (find any Act mentioning a topic), set
`fulltext=True`. For structural drill-in once an Act is found, chain to
legislation_get_toc then legislation_get_section.
```

## Module: `parliament` (9 tools — this-audit count only)

### `parliament_find_member` (81 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a member's name and need their integer member_id.

Returns all members matching the name query, each with the integer `id`,
party, constituency, house, and current-sitting status. Disambiguates
common-name matches (e.g. "Lord Smith" returns multiple peers).

CALL THIS BEFORE any tool that filters by member_id — including
parliament_get_debate_contributions, parliament_member_debates, and
parliament_member_interests. Name → ID first; ID-based filtering second.
Skipping this step and text-searching by name returns unrelated results
(see parliament_search_hansard's anti-bypass note for the Pannick case).
```

### `parliament_get_debate_contributions` (127 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a debate_ext_id and want verbatim contributions, optionally filtered to one member.

Canonical path for "everything a member said in this debate" regardless
of vocabulary — text-search tools (parliament_member_debates,
parliament_search_hansard) filter by contribution TEXT, dropping members
who spoke without using your phrase verbatim. This tool filters by
MemberId on the debate's Items list, so vocabulary doesn't matter.

Typical chain: parliament_find_member(name) → member_id, then
parliament_search_hansard or parliament_lookup_by_column → debate_ext_id,
then this tool. The parliament module's instructions describe the full
composition pattern.

Without member_id, returns every contribution (~100-200 for a long debate).

If the wire returns no contributions for a member you expect to have
spoken, report the empty result honestly — do NOT reconstruct quotes
from training data. Authoritative source for member contributions.
```

### `parliament_get_debate_divisions` (57 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a debate_ext_id and want the divisions (formal votes) held within it.

Most debates contain no divisions — Business of the House sittings,
statements, urgent questions, debates without a vote. A populated list
typically appears around bill stages, motions, and contested amendments.
Empty list is the honest result, not a failure mode.
```

### `parliament_lookup_by_column` (48 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have an OSCOLA-style Hansard citation (column + volume + house) and need the debate.

Example input: 'HL Deb 14 Oct 2025, vol 849, col 200'. AFTER calling, read
hansard://debate/{debate_ext_id}/header for the contribution at the cited
column, or call parliament_get_debate_contributions for the full list.
```

### `parliament_member_debates` (91 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a member_id and want contributions where THAT member used a specific topic phrase verbatim (text-body search).

CALL parliament_find_member(name) FIRST to obtain the integer member_id.

This is a name-based text-body search — it matches contributions whose
TEXT contains the topic phrase. A member who spoke in a debate but
didn't use your phrase verbatim is filtered out. For verbatim retrieval
of every contribution by a member in a known debate (regardless of
vocabulary), use parliament_get_debate_contributions(debate_ext_id,
member_id=...) instead.

Each contribution's text field is capped at 3000 characters.
```

### `parliament_member_interests` (94 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a member_id and need their registered financial interests (donations, directorships, land, gifts).

CALL parliament_find_member(name) FIRST to obtain the integer member_id.

Returns ONE PAGE of interests (default 20, caller controls via limit).
For prolific members (big donors, many directorships, extensive land
holdings), re-call with offset=offset+returned while has_more is true
to paginate. Description text is capped per max_description_chars;
raise it for forensic provenance work that needs the full narrative.

This is the authoritative source for UK MP and peer financial-interest
declarations (via the Members API). Web search returns stale snapshots.
```

### `parliament_policy_position_summary` (149 words)

**Params** (1): params

```
USE THIS TOOL WHEN you want debate-level corpus signals on a topic — by_house, by_year, by_section breakdowns — without reading every contribution.

Aggregates Hansard debate-level signals on a topic. Pure counts — no LLM,
no editorial labels. Sweeps /search/Debates.json with pagination (up to
max_debates_scanned), then aggregates by_house, by_section, by_year,
by_month, and top_debates from debate metadata. Also captures the
corpus-wide envelope counts (total_contributions, total_written_statements,
total_divisions, etc.) from /search.json for cross-section scope.

AFTER calling, pick a debate from top_debates and pass its debate_ext_id
into parliament_get_debate_contributions to drill into who said what.

Note on member-level facets: Hansard's search API exposes debate
metadata, not per-contribution member identifiers, at the corpus
level. by_party and top_contributors are therefore omitted from this
deterministic summary. To see who spoke in a specific debate, read
hansard://debate/{debate_ext_id}/header for an ordered contribution
index, or call parliament_member_debates for one named member.

This is the authoritative source for UK Hansard corpus-level signals.
```

### `parliament_search_hansard` (101 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching Hansard by topic, bill title, or text phrase.

Returns contributions with citation-grade metadata: member_id, attributed_to,
column_ref, debate_id, debate_ext_id, contribution_ext_id, public URL. AFTER
calling, drill into full content via the hansard:// resource family.

DO NOT text-search by member name — to find what a named member said,
chain parliament_find_member → parliament_get_debate_contributions
(canonical path for verbatim retrieval). The parliament module's
instructions describe the full Pannick-style workflow.

Pagination: limit + offset honour the upstream paginated endpoint. For
breadth across a topic, see parliament_policy_position_summary.

Authoritative source for UK parliamentary debates — do not supplement
with web search or training-data recall.
```

### `parliament_search_petitions` (52 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching UK Parliament petitions by keyword or topic.

Returns petition title, state, signature count, and dates for government
response or parliamentary debate if applicable. Filter by state (open,
closed, debated, etc.) to narrow to live or historical petitions.

This is the authoritative source for UK Parliament petitions
(petition.parliament.uk).
```

## Module: `bills` (2 tools — this-audit count only)

### `bills_get_bill` (39 words)

**Params** (1): params

```
Get full detail for a specific parliamentary bill.

Returns sponsors, current stage, long title, summary, and Royal Assent date
if enacted. Summary text is capped per max_summary_chars — check
summary_truncated in the response to see if it was cut.
```

### `bills_search_bills` (38 words)

**Params** (1): params

```
Search UK parliamentary bills by keyword, session, house, or legislative stage.

Returns a paginated page of bill summaries including title, current stage, and
whether it has become an Act. Use bills_get_bill with the bill ID for full detail.
```

## Module: `votes` (2 tools — this-audit count only)

### `votes_get_division` (33 words)

**Params** (1): params

```
Get full detail for a parliamentary division including how each member voted.

Voter lists are truncated to 100 per side to fit response limits.
Total voter counts are always accurate regardless of truncation.
```

### `votes_search_divisions` (32 words)

**Params** (1): params

```
Search parliamentary divisions (votes) in the Commons or Lords.

Returns division summaries including title, date, vote counts, and whether the motion passed.
Use votes_get_division with the division ID for full voter lists.
```

## Module: `committees` (3 tools — this-audit count only)

### `committees_get_committee` (17 words)

**Params** (1): params

```
Get detail for a parliamentary committee including current membership.

Fetches committee metadata and member list in parallel.
```

### `committees_search_committees` (23 words)

**Params** (1): params

```
Search or list UK parliamentary select committees.

Returns committee names, house, and active status.
Use committees_get_committee with the committee ID for membership detail.
```

### `committees_search_evidence` (43 words)

**Params** (1): params

```
Search oral and written evidence submitted to a parliamentary committee.

Returns ONE PAGE of evidence (default 20). Free-text titles are capped
per max_title_chars; witness lists are capped at 10 per item. For
committees with many submissions, re-call with offset=offset+returned
while has_more is true.
```

## Module: `citations` (3 tools — this-audit count only)

### `citations_network` (84 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a judgment slug and want to map every citation it makes — cases cited, legislation referenced, SIs, retained EU law.

Fetches the judgment XML from TNA and parses all OSCOLA citations
within. Returns citations grouped by type, deduplicated and sorted.
AFTER calling, pass any individual citation through citations_resolve
to confirm it resolves and to retrieve its canonical URL.

Useful for authority-network analysis (what did this judgment rely on?)
and for surfacing the legislative landscape a case sits inside.
```

### `citations_parse` (110 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have free text (a memo, an email, a clause) and want every OSCOLA-style citation it contains extracted and classified.

Identifies: neutral citations ([2024] UKSC 12), law reports ([2024] 1 WLR
100), legislation sections (s.47 Companies Act 2006), SIs (SI 2018/1234),
retained EU law (Regulation (EU) 2016/679).

Ambiguous citations (e.g. bare [2024] EWHC without division) are
optionally disambiguated via LLM sampling. Citations resolve to TNA /
legislation.gov.uk URLs when possible.

AFTER calling, pass each citation through citations_resolve to verify it
points at a real document before quoting or formatting it — the parser
recognises the SHAPE of a citation but does not confirm the document
exists.
```

### `citations_resolve` (125 words)

**Params** (1): params

```
USE THIS TOOL BEFORE constructing an OSCOLA citation string from known fields, OR when you have a citation and want to confirm it points at a real document.

Parses + resolves a single citation (neutral citation, SI, legislation
section, retained EU law) and returns the parsed fields plus a
resolved_url. Raises ValueError if nothing recognisable is found.

Formatting a citation from "known" fields (year, court, number) without
prior resolution is the most common citation-fabrication route — the
formatter accepts whatever you give it and produces plausible-looking
output for invented inputs. If this tool raises or returns no
resolved_url, do NOT manufacture a citation — surface the failure and
ask the user for the source URL or better identifying details.

Authoritative source for UK legal-citation resolution.
```

## Module: `hmrc` (3 tools — this-audit count only)

### `hmrc_check_mtd_status` (50 words)

**Params** (1): params

```
Check a business's Making Tax Digital VAT mandate status via the HMRC API.

NOTE: Connects to the HMRC sandbox by default. Set HMRC_API_BASE env var to
'https://api.service.hmrc.gov.uk' for production.
Requires HMRC_CLIENT_ID and HMRC_CLIENT_SECRET environment variables (OAuth 2.0).
Returns whether the business is mandated for MTD, effective date, and trading name.
```

### `hmrc_get_vat_rate` (56 words)

**Params** (1): params

```
Look up the UK VAT rate for a commodity or service type.

Returns the rate category (standard 20%, reduced 5%, zero 0%, exempt),
effective date, and any relevant conditions or exceptions.
Uses a static lookup table current as of 22 Nov 2023 (Autumn Statement).
Rates may have changed — always verify against GOV.UK for recent Budgets.
```

### `hmrc_search_guidance` (26 words)

**Params** (1): params

```
Search GOV.UK for HMRC tax guidance documents.

Returns matching guidance titles, URLs, summaries, and last-updated dates.
Searches the official GOV.UK content API filtered to HMRC publications.
```

