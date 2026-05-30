# Audit: current tool descriptions (snapshot)

Regenerated from `Client(gateway).list_tools()` via `tests/audit_descriptions.py`.

Per `docs/chatgpt-workflow-encoding.md`: descriptions stay ≤150 words.
Word counts shown next to each tool name.

Per Obs 217: any per-module counts here are this-audit-only — do not propagate.

## Framework-provided (transform-injected) — NOT in scope for the discipline

Owned by FastMCP (`PromptsAsTools` + `ResourcesAsTools` transforms wired in `gateway.py`).

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

### `case_law_search` (139 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching UK case law by party names, court, judge, date, or free-text query.

Returns paginated judgment summaries: neutral citation, court, dates, slug,
stable TNA URI. AFTER calling: pass slug into judgment_get_header /
judgment_get_index / judgment_get_paragraph (or the judgment:// resource
family) for content; pass the neutral citation into citations_resolve
to verify before constructing an OSCOLA citation; use
case_law_grep_judgment to find text within a single judgment. When a
party name returns several candidates, narrow with court + year filters
before grep-iterating across full judgments — targeted filtering beats
scanning every candidate.

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

### `legislation_get_section` (104 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a known Act / SI and want the parsed text of a specific section, with extent and in-force metadata.

Returns full section text, territorial extent, in-force status, and
prospective flag. Content capped per max_chars (default 10,000,
~2,500 tokens) — raise for unusually long definition sections; check
content_truncated in the response.

ALWAYS check `extent` — a section may apply to England & Wales but not
Scotland or Northern Ireland. Reciting a section without checking
extent is a recurring legal-research error.

Alternative: call read_resource(uri="legislation://{type}/{year}/{number}/
section/{section}") for raw CLML XML; use this tool when you want the
parsed structured response instead.
```

### `legislation_get_toc` (103 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a known Act / SI and want the structural table of contents (parts, chapters, sections, schedules).

Returns structural elements with XML id and title, e.g. 'section-47:
Definitions'. AFTER calling, pass the numeric section identifier (use
'47', NOT 'section-47') into legislation_get_section for full text.

Large statutes (Companies Act 2006 has many hundreds of items) are
paginated via offset/limit. Check has_more and total_items.

Alternative: call read_resource(uri="legislation://{type}/{year}/{number}/
toc") for the full TOC as a newline-separated `id: title` string (no
pagination). Use this tool when you need the structured response with
offset / limit / has_more for stepping through large statutes.
```

### `legislation_search` (104 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching UK Acts and Statutory Instruments by title, phrase, or full-text.

Returns ranked results: title, type, year, number, legislation.gov.uk URL,
and next_steps hints (toc URI, section template). AFTER calling, chain
to legislation_get_toc then legislation_get_section for structural drill-in.

Filter discipline: `type` and `year` are exact-match. Use only when you
already know the value. For currency-driven searches ("the recent
Renters' Rights Act"), query by phrase alone and read the year from the
results — guessing a year and filtering by it zeroes results when wrong.
For broader concept queries across content, set `fulltext=True`.

Authoritative source for UK primary and secondary legislation
(legislation.gov.uk).
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

### `parliament_lookup_by_column` (55 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have an OSCOLA-style Hansard citation (column + volume + house) and need the debate.

Example input: 'HL Deb 14 Oct 2025, vol 849, col 200'. AFTER calling, read
the contribution at the cited column via
read_resource(uri="hansard://debate/{debate_ext_id}/header") — or,
equivalently, call parliament_get_debate_contributions(debate_ext_id) for
the full list as a structured tool response.
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

### `parliament_search_hansard` (113 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching Hansard by topic, bill title, or text phrase.

Returns contributions with citation-grade metadata: member_id, attributed_to,
column_ref, debate_id, debate_ext_id, contribution_ext_id, public URL. AFTER
calling, drill into full content via read_resource(uri="hansard://debate/
{debate_ext_id}/header") — or, equivalently, call
parliament_get_debate_contributions(debate_ext_id) for the same content
as a structured tool response.

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

### `bills_get_bill` (59 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a bill_id (from bills_search_bills) and want the full detail.

Returns sponsors, current stage, long title, summary, and Royal Assent
date if enacted. Summary text is capped per max_summary_chars — check
summary_truncated in the response.

AFTER calling, use parliament_search_hansard(query=bill_short_title) to
find the bill's parliamentary debates, or bills_search_bills with a
related keyword for adjacent bills.
```

### `bills_search_bills` (53 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching UK parliamentary bills by keyword, session, house, or legislative stage.

Returns a paginated page of bill summaries (title, current stage, whether
it became an Act). AFTER calling, pass a bill_id into bills_get_bill for
full detail (sponsors, long title, Royal Assent date).

Authoritative source for UK parliamentary bill status.
```

## Module: `votes` (2 tools — this-audit count only)

### `votes_get_division` (51 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a division_id + house and want the full member-by-member voting record.

Voter lists are truncated to 100 per side to fit response limits; total
voter counts are always accurate regardless of truncation. Chain from
votes_search_divisions or parliament_get_debate_divisions (which
cross-resolves Hansard division refs into votes-API division_ids).
```

### `votes_search_divisions` (44 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching Commons or Lords formal votes by topic, date, or member.

Returns division summaries (title, date, vote counts, pass/fail). AFTER
calling, pass division_id + house into votes_get_division for the full
member-by-member voter lists.

Authoritative source for UK parliamentary vote records.
```

## Module: `committees` (3 tools — this-audit count only)

### `committees_get_committee` (42 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a committee_id and want the metadata + current membership.

Fetches committee detail and member list in parallel. AFTER calling,
pass committee_id into committees_search_evidence to see what evidence
has been submitted to this committee on what topics.
```

### `committees_search_committees` (47 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching or listing UK parliamentary select committees by name, house, or active status.

Returns committee summaries (name, house, active status, ID). AFTER
calling, pass committee_id into committees_get_committee for current
membership, or into committees_search_evidence to retrieve oral and
written evidence submitted to that committee.
```

### `committees_search_evidence` (57 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a committee_id and want the oral and written evidence submitted to it.

Returns ONE PAGE of evidence (default 20). Free-text titles are capped
per max_title_chars; witness lists are capped at 10 per item. For
committees with many submissions, re-call with offset=offset+returned
while has_more is true.

Authoritative source for parliamentary committee evidence.
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

### `citations_parse` (138 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have free text (a memo, an email, a clause) and want every OSCOLA-style citation it contains extracted and classified.

Identifies: neutral citations ([2024] UKSC 12), law reports ([2024] 1 WLR
100), legislation sections (s.47 Companies Act 2006), SIs (SI 2018/1234),
retained EU law (Regulation (EU) 2016/679).

Parsing is pure regex by default. Ambiguous citations (e.g. bare [2024]
EWHC without division) can OPTIONALLY be disambiguated by setting
disambiguate=True, which asks the CONNECTED CLIENT's own model (not this
server) to resolve the division via MCP sampling — off by default.
Citations resolve to TNA / legislation.gov.uk URLs when possible.

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

### `hmrc_check_mtd_status` (66 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a 9-digit VAT Registration Number and need that business's Making Tax Digital VAT mandate status.

Returns whether the business is mandated for MTD, effective date, and
trading name.

Connects to the HMRC sandbox by default. Set HMRC_API_BASE to
'https://api.service.hmrc.gov.uk' for production. Requires
HMRC_CLIENT_ID + HMRC_CLIENT_SECRET environment variables (OAuth 2.0).
Raises if credentials are not configured — do not infer status.
```

### `hmrc_get_vat_rate` (66 words)

**Params** (1): params

```
USE THIS TOOL WHEN you have a UK commodity or service description and want its VAT rate category.

Returns the rate (standard 20%, reduced 5%, zero 0%, exempt), effective
date, and any relevant conditions or exceptions.

IMPORTANT: Uses a static lookup table current as of 22 Nov 2023 (Autumn
Statement). Rates may have changed in subsequent Budgets — for
time-sensitive advice, verify against GOV.UK via hmrc_search_guidance.
```

### `hmrc_search_guidance` (56 words)

**Params** (1): params

```
USE THIS TOOL WHEN searching GOV.UK for HMRC tax guidance on a topic (VAT, income tax, corporation tax, etc.).

Returns matching guidance titles, URLs, summaries, and last-updated dates.
Searches the official GOV.UK content API filtered to HMRC publications.

Authoritative source for current HMRC tax guidance. Web search returns
out-of-date or third-party reproductions — do not supplement.
```
