# Tool Reference

This is the compact public reference for the uk-legal-mcp tool surface. Tool descriptions inside MCP clients carry more detailed routing guidance.

## Case Law

| Tool | What it does |
|---|---|
| `case_law_search` | Full-text search of UK judgments. Filter by court, judge, party, date range. |
| `case_law_grep_judgment` | Pattern-match within a judgment; returns `{eId, snippet, match}` per hit. |

| Resource | Returns |
|---|---|
| `judgment://{slug*}/header` | Metadata: parties, judges, neutral citation. |
| `judgment://{slug*}/index` | Paragraph eId and first-line per row. |
| `judgment://{slug*}/para/{eId}` | A single paragraph with its sub-paragraphs. |

## Legislation

| Tool | What it does |
|---|---|
| `legislation_search` | Find Acts and SIs by keyword. |
| `legislation_get_toc` | Table of contents for an Act: parts, chapters, sections, schedules. |
| `legislation_get_section` | A specific section with `extent`, `in_force`, `version_date`, and CLML XML or HTML fallback. |

| Resource | Returns |
|---|---|
| `legislation://{type}/{year}/{number}/section/{section}{?date}` | CLML XML for a section; optional point-in-time date. |
| `legislation://{type}/{year}/{number}/toc{?date}` | Flat `id: title` table of contents. |

## Parliament

| Tool | What it does |
|---|---|
| `parliament_search_hansard` | Search Hansard contributions with citation-grade metadata plus corpus totals and debate/division previews. |
| `parliament_policy_position_summary` | Deterministic facet counts on a topic: house, section, year, top debates. No LLM labels. |
| `parliament_get_debate_divisions` | Divisions held within a debate. Chain via `id` to `votes_get_division`. |
| `parliament_lookup_by_column` | Resolve a Hansard column citation to its debate across current, bound, and historic Hansard records. |
| `parliament_find_member` | Name to integer member ID. |
| `parliament_member_debates` | One member's Hansard contributions, optionally filtered by topic. |
| `parliament_member_interests` | A member's registered financial interests. |
| `parliament_search_petitions` | UK Parliament petitions by keyword. |

| Resource | Returns |
|---|---|
| `hansard://debate/{ext_id}/header` | Debate overview and ordered contribution index with citable column numbers where available. |
| `hansard://debate/{ext_id}/contribution/{ext_id}` | A single contribution's full text and metadata. |
| `hansard://member/{id}/biography` | Government, opposition, and committee posts with start/end dates. |

## Bills

| Tool | What it does |
|---|---|
| `bills_search_bills` | Search current and historical Bills by keyword, session, or type. |
| `bills_get_bill` | Full bill detail: stages, sponsors, publications. |

## Votes

| Tool | What it does |
|---|---|
| `votes_search_divisions` | Search Commons and Lords divisions by keyword or date. |
| `votes_get_division` | Full division detail: vote counts and per-member voting record. |

## Committees

| Tool | What it does |
|---|---|
| `committees_search_committees` | Select committees by keyword. |
| `committees_get_committee` | Committee detail: membership and sub-committees. |
| `committees_search_evidence` | Oral and written evidence submissions. |

## Citations

| Tool | What it does |
|---|---|
| `citations_parse` | Extract OSCOLA citations from free text. Resolves to canonical URLs where possible. |
| `citations_resolve` | Parse and resolve a single citation string. |
| `citations_network` | Fetch a judgment and map citations within it: cases, legislation, SIs, EU law. |

Supported citation formats include neutral citations, law reports, legislation sections, Statutory Instruments, and retained EU law.

## HMRC

| Tool | What it does |
|---|---|
| `hmrc_get_vat_rate` | VAT rate lookup for a commodity or service. |
| `hmrc_check_mtd_status` | Check Making Tax Digital VAT mandate status for a VRN. Requires HMRC OAuth. |
| `hmrc_search_guidance` | Search GOV.UK for HMRC guidance documents. |

`hmrc_check_mtd_status` requires `HMRC_CLIENT_ID` and `HMRC_CLIENT_SECRET`. The server defaults to sandbox; set `HMRC_API_BASE=https://api.service.hmrc.gov.uk` for production.

## Prompts

Workflow templates are exposed as tools via `PromptsAsTools` for clients that cannot consume MCP prompts natively.

| Prompt | Module | What it produces |
|---|---|---|
| `summarise_act` | legislation | Structured summary of a UK Act or SI. |
| `compare_legislation` | legislation | Comparative analysis of two pieces of legislation on a topic. |
| `policy_reception_review` | parliament | Citation-grade review of how a policy topic is being received in Parliament. |
| `member_record_on_topic` | parliament | Citable evidence pack of a named member's contributions on a topic. |
