# Audit: current tool descriptions (snapshot)

Generated 2026-05-30 from `Client(gateway).list_tools()` on `feat/hardening-v1.1` (post A1 / A1.5 / A2).

This file captures the **starting state** of all tool descriptions before Phase A3's description-authority pass.
Use it to:

- See which tools already follow the 4-part pattern (USE WHEN / what it returns / AFTER calling / authoritative-source clause)
- Identify the gap per tool (what needs to change in A3)
- Compare post-A3 output to confirm changes landed

Per Obs 217, do **not** propagate any count from this audit into other docs as a literal integer — the inventory will change as A3 lands.

## Framework-provided (transform-injected) — NOT in scope for Phase A3

These come from `PromptsAsTools` and `ResourcesAsTools` (gateway.py:210-216). Their descriptions are owned by FastMCP, not by us.

### `get_prompt`

**Params** (2): arguments, name

```
Get a prompt by name with optional arguments.

Returns the rendered prompt as JSON with a messages array.
Arguments should be provided as a dict mapping argument names
to values.
```

### `list_prompts`

**Params** (0): (none)

```
List all available prompts.

Returns JSON with prompt metadata including name, description,
and optional arguments.
```

### `list_resources`

**Params** (0): (none)

```
List all available resources and resource templates.

Returns JSON with resource metadata. Static resources have a
'uri' field, while templates have a 'uri_template' field with
placeholders like {name}.
```

### `read_resource`

**Params** (1): uri

```
Read a resource by its URI.

For static resources, provide the exact URI. For templated
resources, provide the URI with template parameters filled in.

Returns the resource content as a string. Binary content is
base64-encoded.
```

## Module: `case_law` (2 tools — count for this audit only, do not propagate)

### `case_law_grep_judgment`

**Params** (1): params

```
Find paragraphs in a single judgment whose text matches a pattern.

Returns a list of `{eId, snippet, match}` hits — small per-paragraph
snippets centred on the match — so the LLM can decide which full
paragraphs to read via judgment://{slug}/para/{eId}.

Content-based search within one judgment (e.g. "negligence", "test for
foreseeability", "Donoghue"). For paragraph-number navigation, read
judgment://{slug}/index instead.

Pattern is regex; if it doesn't compile, falls back to literal
substring search.
```

### `case_law_search`

**Params** (1): params

```
Search UK case law via the TNA Find Case Law API.

Returns paginated judgment summaries: neutral citations, court, dates, stable URIs.
Use the judgment://{slug}/header resource to inspect a result, then
judgment://{slug}/index to discover paragraphs and judgment://{slug}/para/{eId}
to read individual paragraphs. For content-based discovery within a
judgment, use case_law_grep_judgment.

Coverage: TNA Find Case Law indexes UK judgments from roughly the early 2000s
onwards. For older authorities, search for a modern judgment that quotes them
and read that paragraph instead of expecting the original judgment in this index.
```

## Module: `judgment` (3 tools — count for this audit only, do not propagate)

### `judgment_get_header`

**Params** (1): slug

```
Get metadata for a UK court judgment: parties, judges, neutral citation, court, dates.

Use case_law_search to find the slug, then call this for orientation before
reading specific paragraphs via judgment_get_paragraph.
```

### `judgment_get_index`

**Params** (1): slug

```
Get the paragraph navigation index for a UK court judgment.

Returns eId: first_line pairs for every paragraph. Use this to discover
paragraph identifiers, then call judgment_get_paragraph to read specific ones.
```

### `judgment_get_paragraph`

**Params** (2): eId, slug

```
Get a single paragraph from a UK court judgment by its LegalDocML eId.

Use judgment_get_index first to discover available eIds. Returns the paragraph
XML content (400–1,700 tokens typical).
```

## Module: `legislation` (3 tools — count for this audit only, do not propagate)

### `legislation_get_section`

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

### `legislation_get_toc`

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

### `legislation_search`

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

## Module: `parliament` (9 tools — count for this audit only, do not propagate)

### `parliament_find_member`

**Params** (1): params

```
Search for a current or former MP or Lord by name.

Returns all members matching the name query, each with the integer
`id` required by parliament_member_debates and parliament_member_interests,
plus party, constituency, house, and current-sitting status.
```

### `parliament_get_debate_contributions`

**Params** (1): params

```
Drill into a debate to retrieve contributions, optionally filtered by member.

This is the canonical path when you want "everything a member said in this
debate" regardless of which words they used — the text-search-based tools
(parliament_member_debates, parliament_search_hansard) match contribution
TEXT BODIES, so a member who spoke in a debate but didn't say your topic
phrase verbatim is filtered out. This tool fetches the debate's full Items
list and filters by MemberId, so it returns every contribution by that
member in the debate regardless of vocabulary.

Composition pattern — "what did <peer> say about <topic> in the Lords?":
  1. parliament_find_member(name) → member_id
  2. Find the debate by ANY path:
       - parliament_search_hansard(query=<distinctive phrase or title fragment>)
         → top_debates[].debate_ext_id
       - parliament_lookup_by_column(column, volume, house) → matches[].debate_ext_id
  3. parliament_get_debate_contributions(debate_ext_id, member_id=<member_id>)
     → the member's actual contributions in that debate. Quotes are retrieved
     verbatim from the wire; no fallback to training-data reconstruction.

Without `member_id`, returns every contribution in the debate (typical:
100-200 items) — useful for "what was discussed in this debate?" sweeps.
```

### `parliament_get_debate_divisions`

**Params** (1): params

```
Return the divisions (formal votes) held within a specific debate.

Most debates contain no divisions — Business of the House sittings,
statements, urgent questions, debates without a vote. A populated list
typically appears around bill stages, motions, and contested amendments.
```

### `parliament_lookup_by_column`

**Params** (1): params

```
Resolve an OSCOLA-style Hansard citation to a debate.

Use case: you have a citation like 'HL Deb 14 Oct 2025, vol 849, col 200'
and need to verify what was said at that column. This tool calls
/search/debatebycolumn and returns the matching debate section(s); you
then read hansard://debate/{debate_ext_id}/header to find the
contribution at the cited column.

Each match carries `contribution_count` — the real number of
contributions in the debate (populated by a secondary fetch of the
debate's Items list, filtered to ItemType == "Contribution"). A
non-zero value confirms the debate exists with content; zero or null
means the column resolved but no contributions were retrievable.
`relevance_rank` is always null on column-lookup matches (the
column-search endpoint does not compute relevance scores).

Each match also carries `source`/`source_code` — the citation's Hansard
publication state (1=RollingHansard, 2=DailyHansard, 3=BoundVolume,
4=Historic). This tells the lawyer the citation's *finality*, not whether
it resolves: resolution is NOT gated on publication state. Daily Part
(verified live 2026-05-29: vol 849 / col 200 / Lords), Bound Volume, and
Historic (vol 415 / col 200 / Commons, 2003) columns all resolve.

Empty `matches` typically means:
  - The volume_number is wrong (sometimes opposing counsel cites the
    running-volume number rather than the bound-volume number).
  - The column is in a Written Statement or Written Answer (the
    citation usually has a 'W' suffix like '1162W' — pass it as-is).
  - The column is very recent and not yet indexed into the upstream
    column→debate map (rare; retry after consolidation).
```

### `parliament_member_debates`

**Params** (1): params

```
Retrieve Hansard contributions by a specific member, optionally filtered by topic.

Use parliament_find_member first to obtain the integer member ID. Each
contribution's text field is capped at 3000 characters.
```

### `parliament_member_interests`

**Params** (1): params

```
Look up registered financial interests for a member of Parliament.

Returns ONE PAGE of interests (default 20, caller controls via limit).
For prolific members (big donors, many directorships, extensive land
holdings), re-call with offset=offset+returned while has_more is true
to paginate. Description text is capped per max_description_chars;
raise it for forensic provenance work that needs the full narrative.

Use parliament_find_member first to obtain the integer member_id.
```

### `parliament_policy_position_summary`

**Params** (1): params

```
Aggregate Hansard debate-level signals on a topic. Pure counts — no LLM, no editorial labels.

Sweeps /search/Debates.json with pagination (up to max_debates_scanned),
then aggregates by_house, by_section, by_year, by_month, and top_debates
from debate metadata. Also captures the corpus-wide envelope counts
(total_contributions, total_written_statements, total_divisions, etc.)
from /search.json for cross-section scope.

Note on member-level facets: Hansard's search API exposes debate
metadata, not per-contribution member identifiers, at the corpus
level. by_party and top_contributors are therefore omitted from this
deterministic summary. To see who spoke in a specific debate, read
hansard://debate/{debate_ext_id}/header for an ordered contribution
index, or call parliament_member_debates for one named member.
```

### `parliament_search_hansard`

**Params** (1): params

```
Search Hansard for parliamentary debates, questions, and speeches.

Returns contributions with citation-grade metadata: member_id, attributed_to
(the citable form), column_ref, debate_id, debate_ext_id, contribution_ext_id,
and a synthesised public hansard.parliament.uk URL. Use the returned
debate_ext_id and contribution_ext_id to drill into full content via the
hansard:// resource family.

Pagination: limit + offset honour the upstream `/search/contributions/{type}.json`
endpoint, which actually paginates (verified live 2026-05-29). For breadth
across a topic without reading every contribution, see
parliament_policy_position_summary; for one named member's contributions, see
parliament_member_debates.
```

### `parliament_search_petitions`

**Params** (1): params

```
Search UK Parliament petitions by keyword.

Returns petition title, state, signature count, and dates for government response
or parliamentary debate if applicable.
```

## Module: `bills` (2 tools — count for this audit only, do not propagate)

### `bills_get_bill`

**Params** (1): params

```
Get full detail for a specific parliamentary bill.

Returns sponsors, current stage, long title, summary, and Royal Assent date
if enacted. Summary text is capped per max_summary_chars — check
summary_truncated in the response to see if it was cut.
```

### `bills_search_bills`

**Params** (1): params

```
Search UK parliamentary bills by keyword, session, house, or legislative stage.

Returns a paginated page of bill summaries including title, current stage, and
whether it has become an Act. Use bills_get_bill with the bill ID for full detail.
```

## Module: `votes` (2 tools — count for this audit only, do not propagate)

### `votes_get_division`

**Params** (1): params

```
Get full detail for a parliamentary division including how each member voted.

Voter lists are truncated to 100 per side to fit response limits.
Total voter counts are always accurate regardless of truncation.
```

### `votes_search_divisions`

**Params** (1): params

```
Search parliamentary divisions (votes) in the Commons or Lords.

Returns division summaries including title, date, vote counts, and whether the motion passed.
Use votes_get_division with the division ID for full voter lists.
```

## Module: `committees` (3 tools — count for this audit only, do not propagate)

### `committees_get_committee`

**Params** (1): params

```
Get detail for a parliamentary committee including current membership.

Fetches committee metadata and member list in parallel.
```

### `committees_search_committees`

**Params** (1): params

```
Search or list UK parliamentary select committees.

Returns committee names, house, and active status.
Use committees_get_committee with the committee ID for membership detail.
```

### `committees_search_evidence`

**Params** (1): params

```
Search oral and written evidence submitted to a parliamentary committee.

Returns ONE PAGE of evidence (default 20). Free-text titles are capped
per max_title_chars; witness lists are capped at 10 per item. For
committees with many submissions, re-call with offset=offset+returned
while has_more is true.
```

## Module: `citations` (3 tools — count for this audit only, do not propagate)

### `citations_network`

**Params** (1): params

```
Map all citations within a judgment — cases cited, legislation referenced, SIs, EU law.

Fetches the judgment XML from TNA and parses all OSCOLA citations within it.
Returns citations grouped by type for easy analysis. Each bucket is
de-duplicated and sorted.
```

### `citations_parse`

**Params** (1): params

```
Extract and classify all OSCOLA legal citations from free text.

Identifies: neutral citations ([2024] UKSC 12), law reports ([2024] 1 WLR 100),
legislation sections (s.47 Companies Act 2006), SIs (SI 2018/1234),
and retained EU law (Regulation (EU) 2016/679).

Ambiguous citations (e.g. bare [2024] EWHC without division) are optionally
disambiguated via LLM sampling. Resolves citations to TNA / legislation.gov.uk URLs.
```

### `citations_resolve`

**Params** (1): params

```
Parse and resolve a single OSCOLA citation to its canonical URL.

Supports: neutral citations, SIs, legislation sections, retained EU law.
Returns parsed fields and resolved_url if resolvable. Raises ValueError
if no recognised citation is found in the input.
```

## Module: `hmrc` (3 tools — count for this audit only, do not propagate)

### `hmrc_check_mtd_status`

**Params** (1): params

```
Check a business's Making Tax Digital VAT mandate status via the HMRC API.

NOTE: Connects to the HMRC sandbox by default. Set HMRC_API_BASE env var to
'https://api.service.hmrc.gov.uk' for production.
Requires HMRC_CLIENT_ID and HMRC_CLIENT_SECRET environment variables (OAuth 2.0).
Returns whether the business is mandated for MTD, effective date, and trading name.
```

### `hmrc_get_vat_rate`

**Params** (1): params

```
Look up the UK VAT rate for a commodity or service type.

Returns the rate category (standard 20%, reduced 5%, zero 0%, exempt),
effective date, and any relevant conditions or exceptions.
Uses a static lookup table current as of 22 Nov 2023 (Autumn Statement).
Rates may have changed — always verify against GOV.UK for recent Budgets.
```

### `hmrc_search_guidance`

**Params** (1): params

```
Search GOV.UK for HMRC tax guidance documents.

Returns matching guidance titles, URLs, summaries, and last-updated dates.
Searches the official GOV.UK content API filtered to HMRC publications.
```

