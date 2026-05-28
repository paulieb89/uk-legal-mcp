# uk-legal-mcp

<!-- mcp-name: io.github.paulieb89/uk-legal-mcp -->

[![PyPI](https://img.shields.io/pypi/v/uk-legal-mcp)](https://pypi.org/project/uk-legal-mcp/)
[![SafeSkill](https://safeskill.dev/api/badge/paulieb89-uk-legal-mcp)](https://safeskill.dev/scan/paulieb89-uk-legal-mcp)
[![Glama](https://img.shields.io/badge/Glama-listed-orange?style=flat-square)](https://glama.ai/mcp/connectors/io.github.paulieb89/uk-legal-mcp)
[![smithery badge](https://smithery.ai/badge/bouch/uk-legal)](https://smithery.ai/servers/bouch/uk-legal)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Server-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=uk-legal&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fuk-legal-mcp.fly.dev%2Fmcp%22%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install_Server-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=uk-legal&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fuk-legal-mcp.fly.dev%2Fmcp%22%7D&quality=insiders)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_Server-000000?style=flat-square&logoColor=white)](https://cursor.com/en/install-mcp?name=uk-legal&config=eyJ0eXBlIjoiaHR0cCIsInVybCI6Imh0dHBzOi8vdWstbGVnYWwtbWNwLmZseS5kZXYvbWNwIn0=)

A Model Context Protocol server for UK legal research. Connects AI assistants to case law, legislation, parliamentary debates, bills, votes, committees, OSCOLA citation parsing, and HMRC tax data through a single endpoint.

**24 tools across 8 modules.** One connection. Read-only. No API keys required for 23 of 24 tools.

```
MCP Client (Claude, Cursor, etc.)
        |
        v
  uk-legal-mcp gateway  (Streamable HTTP)
  +----------------------------------------------------+
  |                                                    |
  |  case_law      TNA Find Case Law API               |
  |  legislation   legislation.gov.uk Atom feed         |
  |  parliament    Hansard API + Members API            |
  |  bills         Parliamentary Bills API              |
  |  votes         Commons + Lords division records     |
  |  committees    Select committees + evidence         |
  |  citations     OSCOLA regex parser (no network)     |
  |  hmrc          HMRC sandbox/prod + GOV.UK search    |
  |                                                    |
  +----------------------------------------------------+
```

## Quickstart

### Connect to the hosted server

Use this URL when adding a remote/custom MCP connector in Claude, Claude Desktop, VS Code, Cursor, or another MCP client:

```text
https://uk-legal-mcp.fly.dev/mcp
```

For clients that use `mcpServers` JSON:

```json
{
  "mcpServers": {
    "uk-legal": {
      "type": "http",
      "url": "https://uk-legal-mcp.fly.dev/mcp"
    }
  }
}
```

For VS Code workspace config, use `.vscode/mcp.json`:

```json
{
  "servers": {
    "uk-legal": {
      "type": "http",
      "url": "https://uk-legal-mcp.fly.dev/mcp"
    }
  }
}
```

Then try:

- *"Search for case law about cycling accidents"*
- *"Get section 172 of the Companies Act 2006"*
- *"Parse the citations in: The court applied Donoghue v Stevenson [1932] AC 562 and s.2 Occupiers' Liability Act 1957"*
- *"What is parliament saying about short selling?"*

### Claude Desktop (local install via uvx)

Install and run the server locally in stdio mode — useful if you want to use the server from Claude Desktop on a residential IP (which bypasses legislation.gov.uk WAF blocks that affect the hosted server):

```bash
uvx uk-legal-mcp
```

Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "uk-legal": {
      "command": "uvx",
      "args": ["uk-legal-mcp"]
    }
  }
}
```

### Local HTTP server (advanced)

To run the full HTTP gateway locally (e.g. for development):

```bash
PORT=8765 python -m src.gateway
```

```json
{
  "mcpServers": {
    "uk-legal": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

---

## A lawyer's guide

This server is a data pipe between your AI assistant and the UK's primary legal sources. It returns what the sources say, with citations. It does not interpret the law, classify members' positions, or recommend a research strategy — that is your work, and your agent's, on your behalf.

### What you can ask for

| If you need... | Use these surfaces |
|---|---|
| **A judgment** by neutral citation, court, judge, party, or full-text search | `case_law_search`, then read `judgment://{slug}/header` for parties / judges / citations, `judgment://{slug}/index` for the paragraph list, `judgment://{slug}/para/{eId}` for a single paragraph in LegalDocML |
| **An Act, SI, or section** at a point in time, with territorial extent | `legislation_search`, `legislation_get_toc`, `legislation_get_section` |
| **What MPs and Lords have said** on a topic | `parliament_search_hansard` (top 4 ranked contributions; see caveat below), then `hansard://debate/{ext_id}/header` for the full debate around any hit |
| **The scope of parliamentary attention** on a topic — how many debates, in which chambers, when, which top debates | `parliament_policy_position_summary` |
| **A specific MP / Lord's contributions** on a topic, with their role at the time | `parliament_find_member`, then `parliament_member_debates`, then `hansard://member/{id}/biography` for posts with start / end dates |
| **A member's registered financial interests** | `parliament_member_interests` |
| **A Bill's stages, sponsors, publications** | `bills_search_bills`, `bills_get_bill` |
| **How members voted on a division** | `votes_search_divisions`, `votes_get_division` |
| **Select committee membership and evidence submissions** | `committees_search_committees`, `committees_get_committee`, `committees_search_evidence` |
| **OSCOLA citations** parsed from a brief or judgment, with canonical URLs | `citations_parse`, `citations_resolve`, `citations_network` |
| **VAT rate, MTD status, HMRC guidance** | `hmrc_get_vat_rate`, `hmrc_check_mtd_status`, `hmrc_search_guidance` |

Four prompts (`/legislation_summarise_act`, `/legislation_compare_legislation`, `/parliament_policy_reception_review`, `/parliament_member_record_on_topic`) bundle multi-step workflows for common research tasks. Invoke them by name in any MCP-aware client. Each prompt produces a citable evidence pack; none classify positions or recommend an argumentative line.

### How to use it, in practice

Ask your agent in plain English what you need. The agent picks the right tools. A few patterns:

- **"Pull *Donoghue v Stevenson* [1932] AC 562 and tell me which paragraphs cite *Heaven v Pender*."** → `citations_resolve` + `case_law_grep_judgment`.
- **"What does the Renters' Rights Act 2025 say about no-fault evictions?"** → `legislation_search` to find the Act, `legislation_get_toc` to locate the section, `legislation_get_section` to read it with extent.
- **"How was the Renters' Rights Bill debated in committee?"** → `parliament_policy_position_summary` with `topic="Renters' Rights Bill"` to see all debates, then `hansard://debate/{ext_id}/header` on the top debate to read the contribution sequence in order.
- **"Has the Supreme Court considered s.21 Housing Act 1988?"** → `case_law_search` with the section reference, then `case_law_grep_judgment` within each hit for the actual treatment.
- **"Build me an OSCOLA citation table for the cases this judgment cites."** → `citations_network` against the judgment's slug.

### Worked example — the Renters' Rights Act 2025

You're advising on a landlord's eviction-notice exposure under the new regime. A reasonable research path your agent might take, with the tools at each step:

1. **The statute.** `legislation_search(query="Renters' Rights")` returns the Act. `legislation_get_toc` lists its parts and sections. `legislation_get_section` returns the relevant section's text with its extent (England-only, in most cases — important to confirm).
2. **The legislative history.** `bills_search_bills(query="Renters' Rights")` returns the Bill record. `bills_get_bill` returns its stages, sponsors, and committee publications.
3. **What was argued in Parliament.** `parliament_policy_position_summary(topic="Renters' Rights Bill")` returns corpus totals (how many contributions, debates, divisions) and the top debates with their `debate_ext_id`. For any debate of interest, read `hansard://debate/{ext_id}/header` to see the ordered contribution index — speaker, role, column reference, preview — and pull individual contributions via `hansard://debate/{ext_id}/contribution/{contribution_ext_id}` for the full text with citation metadata.
4. **The votes.** `votes_search_divisions(query="Renters' Rights")` lists the division records. `votes_get_division` returns the per-member vote breakdown — useful to see who broke whip.
5. **The case law (where any).** `case_law_search(query="Renters' Rights Act 2025")` for any judgments already citing the Act. `case_law_grep_judgment` for the exact paragraphs that treat each section.
6. **Citations for your brief.** Pass your draft text into `citations_parse` to get a clean OSCOLA-formatted citation list with canonical URLs.

Every Hansard contribution returns the `attributed_to` string ("The Minister of State, ... (Lord X) (Lab)"), the `column_ref`, the date, the debate title, and a public hansard.parliament.uk URL — everything you need to footnote in a brief.

### Important constraints to know

- **Territorial extent always matters.** `legislation_get_section` exposes the `extent` field. Acts that apply in England and Wales do not automatically apply in Scotland or Northern Ireland. Always read this before citing a section as binding in a jurisdiction.
- **Hansard's `/search.json` caps at 4 contributions per query, regardless of the `limit` parameter.** This is an upstream API limitation. The four results returned are the top-ranked across the corpus, and `total_corpus` on the response tells you how many matches exist overall. For breadth, escalate to `parliament_policy_position_summary` for debate-level scope, then drill into specific debates via the `hansard://debate/{ext_id}/header` resource which lists *all* contributions in that debate in order.
- **What this server does not do.** It does not classify a member as supporting or opposing a policy, summarise a judgment's outcome in your client's favour, or recommend an argumentative line. Those are interpretive acts. The server returns the primary source verbatim with citation metadata; your agent and your judgement do the legal work.
- **Caching.** Judgments, statutes, debates, and member biographies are cached at the gateway for one hour. Two lawyers connecting to the hosted server share the same cache — if one of you fetches a heavy debate, the next reader gets it instantly. The cache does not affect tool-call results from search endpoints, only resource reads.
- **Legislation.gov.uk WAF.** Some heavy Acts (notably the Companies Act 2006) intermittently fail on the hosted server due to upstream WAF rules that block our cloud IP range. The local install (`uvx uk-legal-mcp`) runs on your own IP and bypasses this. If a section fetch fails on the hosted server, try the local install or use `legislation_search(fulltext=True)` for a workaround.

---

## Tools

### Case Law

| Tool | What it does |
|------|-------------|
| `case_law_search` | Full-text search of UK judgments. Filter by court, judge, party, date range. |
| `case_law_grep_judgment` | Find paragraphs in a judgment matching a pattern. Returns `{eId, snippet, match}` per hit. |

Resource templates (read via `resources/read` or the `read_resource` tool generated by `ResourcesAsTools`):

| URI template | Returns |
|---|---|
| `judgment://{slug*}/header` | Metadata header (parties, judges, citation). ~1k tokens. |
| `judgment://{slug*}/index` | Paragraph eId + first-line per row. ~4k tokens. Walk this to discover paragraphs. |
| `judgment://{slug*}/para/{eId}` | A single paragraph including its sub-paragraphs. 400–1700 tokens. |

Upstream: [TNA Find Case Law](https://caselaw.nationalarchives.gov.uk/) (Atom/XML). Rate limit: 1,000 req/5 min. Cached 1 hour.

### Legislation

| Tool | What it does |
|------|-------------|
| `legislation_search` | Search Acts of Parliament and Statutory Instruments on legislation.gov.uk. |
| `legislation_get_toc` | Table of contents for an Act — parts, chapters, sections, schedules. |
| `legislation_get_section` | Retrieve a specific section with territorial extent, in-force status, and version date. |

Resource templates (alternative to the tools above for clients that prefer URI-addressed reads):

| URI template | Returns |
|---|---|
| `legislation://{type}/{year}/{number}` | Full Act/SI as CLML XML. |
| `legislation://{type}/{year}/{number}/section/{section}` | A specific section as CLML XML. |
| `legislation://{type}/{year}/{number}/toc` | Flat `id: title` lines for the table of contents. |
| `legislation://{type}/{year}/{number}/{date}` | Point-in-time CLML for a YYYY-MM-DD date. |

Upstream: [legislation.gov.uk](https://www.legislation.gov.uk/) (CLML XML + Atom feed). Cached 24 hours. Uses curl_cffi with Chrome impersonation to bypass WAF challenges. When the XML API is blocked, `legislation_get_section` falls back to the public HTML page and sets `source_format: "html_fallback"` — metadata fields (`extent`, `in_force`, `version_date`) will be null/empty in that case. WAF blocking primarily affects the hosted server's IP ranges; the local stdio install (`uvx uk-legal-mcp`) runs on your own IP and is rarely affected.

**Note:** Always check the `extent` field. A section may apply to England and Wales but not Scotland or Northern Ireland.

### Parliament

| Tool | What it does |
|------|-------------|
| `parliament_search_hansard` | Search Hansard contributions by exact phrase. Returns citation-grade metadata per contribution: `attributed_to`, `column_ref`, `debate_id`, `debate_ext_id`, `contribution_ext_id`, public Hansard URL, plus party / house breakdown and `total_corpus` on the result. Supports `from_date`, `to_date`, `house`, `text_mode=preview\|full`. |
| `parliament_policy_position_summary` | Deterministic facet counts on a topic — no LLM, no editorial labels. Returns by-party / by-house / by-section / by-year / by-month breakdowns, top contributors, and top debates (each with `debate_ext_id` for resource lookup). Use to scope the conversation before drilling into specific contributions. |
| `parliament_find_member` | Look up an MP or Lord by name. Returns member ID for use with `member_debates`. |
| `parliament_member_debates` | Retrieve a specific member's Hansard contributions, optionally filtered by topic. |
| `parliament_member_interests` | Get a member's registered financial interests (donations, shareholdings, etc.). |
| `parliament_search_petitions` | Search UK Parliament petitions by keyword. |

| Resource | What it returns |
|----------|-----------------|
| `hansard://debate/{debate_ext_id}/header` | Debate overview + ordered contribution index (`order`, `contribution_ext_id`, `attributed_to`, `column_ref`, preview). ~3–8k tokens. |
| `hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}` | A single contribution's full text + metadata + column reference, extracted from the cached parent debate. ~200–2000 tokens. |
| `hansard://member/{member_id}/biography` | Members API biography: government posts, opposition posts, committee memberships, party affiliations, each with start/end dates so callers can resolve a member's role at the time of any contribution. ~2–5k tokens. |

Upstream: [hansard-api.parliament.uk](https://hansard-api.parliament.uk) + [members-api.parliament.uk](https://members-api.parliament.uk) + [petition.parliament.uk](https://petition.parliament.uk). Debate / contribution / biography resources cached 1 hour at the gateway.

**Breaking change in v0.5.0:** The previous `parliament_vibe_check` tool (LLM-sampled sentiment classifier that labelled named MPs as supporters / opponents from short snippets) has been removed. Its editorial framing now lives in the `policy_reception_review` prompt, executed by the *client's* LLM with explicit instructions never to label members as for/against on snippet evidence. Its evidence layer is absorbed into the enriched `parliament_search_hansard` and the new `hansard://` resources.

### Bills

| Tool | What it does |
|------|-------------|
| `bills_search_bills` | Search current and historical parliamentary bills by keyword, session, or type. |
| `bills_get_bill` | Get full bill detail — stages, sponsors, publications. |

Upstream: [bills-api.parliament.uk](https://bills-api.parliament.uk). Cached 1 hour.

### Votes

| Tool | What it does |
|------|-------------|
| `votes_search_divisions` | Search Commons and Lords division records by keyword or date. |
| `votes_get_division` | Get full division detail — vote counts, how each member voted. |

Upstream: [commonsvotes-api.parliament.uk](https://commonsvotes-api.parliament.uk) + [lordsvotes-api.parliament.uk](https://lordsvotes-api.parliament.uk). Cached 24 hours.

### Committees

| Tool | What it does |
|------|-------------|
| `committees_search_committees` | Search parliamentary select committees by keyword. |
| `committees_get_committee` | Get committee detail — membership, sub-committees. |
| `committees_search_evidence` | Search oral and written evidence submissions to committees. |

Upstream: [committees-api.parliament.uk](https://committees-api.parliament.uk). Cached 1 hour.

### Citations

| Tool | What it does |
|------|-------------|
| `citations_parse` | Extract all OSCOLA citations from free text. Resolves to canonical URLs. Disambiguates bare court codes via LLM sampling. |
| `citations_resolve` | Parse and resolve a single citation string to its canonical URL. |
| `citations_network` | Fetch a judgment from TNA and map every citation within it — cases, legislation, SIs, EU law. |

Self-contained. No external API. Zero network dependency (except `citations_network` which fetches the judgment XML).

**Supported citation formats:**

| Format | Example |
|--------|---------|
| Neutral citation | `[2024] UKSC 12` |
| Law report (with or without volume) | `[2024] 1 WLR 100`, `[1932] AC 562` |
| Legislation section | `s.47 Companies Act 2006` |
| Statutory Instrument | `SI 2018/1234` |
| Retained EU law | `Regulation (EU) 2016/679` |

### HMRC

| Tool | What it does |
|------|-------------|
| `hmrc_get_vat_rate` | VAT rate lookup for any commodity or service. Static table current as of Autumn Statement 2023. |
| `hmrc_check_mtd_status` | Check Making Tax Digital VAT mandate status for a VRN. Requires HMRC OAuth credentials. |
| `hmrc_search_guidance` | Search GOV.UK for HMRC guidance documents. |

`hmrc_get_vat_rate` and `hmrc_search_guidance` require no credentials. `hmrc_check_mtd_status` requires `HMRC_CLIENT_ID` and `HMRC_CLIENT_SECRET` — register at [developer.service.hmrc.gov.uk](https://developer.service.hmrc.gov.uk). Defaults to sandbox; set `HMRC_API_BASE=https://api.service.hmrc.gov.uk` for production.

---

## Prompts

Four workflow prompts are available for multi-step legal research. Exposed as tools via `PromptsAsTools` for ChatGPT; accessible natively on protocol-aware clients (Claude, Inspector).

| Prompt | Module | Description |
|--------|--------|-------------|
| `summarise_act` | legislation | Structured summary of a UK Act or SI |
| `compare_legislation` | legislation | Comparative analysis of two pieces of legislation on a topic |
| `policy_reception_review` | parliament | Citation-grade review of how a policy topic is being received in Parliament. Orchestrates the deterministic tools + `hansard://` resources, never labels named members as supporters / opponents from snippets. |
| `member_record_on_topic` | parliament | Citable evidence pack of a named member's contributions on a topic — their own words, with attributed_to / date / column_ref / role-at-time. Does not classify their position. |

---

## Architecture

```
src/
  gateway.py            FastMCP gateway — mounts all modules, applies middleware
  deps.py               Shared httpx clients (lifespan-managed) + error formatting
  modules/
    case_law/           TNA Find Case Law (Atom/XML parsing)
    legislation/        legislation.gov.uk (CLML XML + Atom feed)
    parliament/         Hansard API + Members API + Petitions (JSON)
    bills/              Parliamentary Bills API (JSON)
    votes/              Commons + Lords division records (JSON)
    committees/         Select committees + evidence (JSON)
    citations/          OSCOLA regex engine (compiled once, lru_cache)
    hmrc/               HMRC OAuth + GOV.UK search (JSON)
tests/
  test_citations.py     35 unit tests — regex patterns, resolution, disambiguation
```

Each module is a standalone `FastMCP` instance mounted into the gateway with a namespace prefix (`case_law_`, `legislation_`, etc.). All modules share a single httpx client pool via the gateway's lifespan context.

**Middleware stack (gateway level):**

| Middleware | Purpose |
|-----------|---------|
| `ErrorHandlingMiddleware` | Catches unhandled exceptions |
| `StructuredLoggingMiddleware` | JSON logging with duration and payload size |
| `PrometheusMiddleware` | Tool call counters + latency histograms (`/metrics`) |
| `DetailedTimingMiddleware` | Per-tool timing logs |
| `ResponseCachingMiddleware` | Gateway-level 1hr cache for tools and resources |

**Per-module caching:** `ResponseCachingMiddleware` with TTLs — case_law (1hr), legislation (24hr), bills (1hr), votes (24hr), committees (1hr), hmrc (90 days). Parliament and citations are not cached.

---

## Deployment

### Fly.io

```bash
fly auth login
fly launch --name uk-legal-mcp --region lhr
fly deploy
```

Optional secrets:

```bash
fly secrets set HMRC_CLIENT_ID=your_id HMRC_CLIENT_SECRET=your_secret
# For production HMRC (default is sandbox):
fly secrets set HMRC_API_BASE=https://api.service.hmrc.gov.uk
```

### Docker

```bash
docker build -t uk-legal-mcp .
docker run -p 8080:8080 uk-legal-mcp
```

---

## Testing

```bash
pip install -e '.[test]'  # or: pip install pytest tiktoken
pytest tests/ -v -k "not live"
```

62 tests run offline with no API credentials: 35 citation tests (regex patterns, resolution, disambiguation) and 27 legislation parser tests (CLML XML + HTML fallback parsers, section ID normalisation).

---

## Upstream APIs and Licences

| Source | API | Licence | Auth |
|--------|-----|---------|------|
| TNA Find Case Law | `caselaw.nationalarchives.gov.uk` | [Open Justice Licence](https://caselaw.nationalarchives.gov.uk/open-justice-licence) | None |
| legislation.gov.uk | `legislation.gov.uk` | [OGL v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | None |
| UK Parliament Hansard | `hansard-api.parliament.uk` | [Open Parliament Licence](https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/) | None |
| UK Parliament Members | `members-api.parliament.uk` | Open Parliament Licence | None |
| UK Parliament Petitions | `petition.parliament.uk` | Open Parliament Licence | None |
| UK Parliament Bills | `bills-api.parliament.uk` | Open Parliament Licence | None |
| UK Parliament Votes | `commonsvotes-api.parliament.uk` | Open Parliament Licence | None |
| UK Parliament Committees | `committees-api.parliament.uk` | Open Parliament Licence | None |
| HMRC | `test-api.service.hmrc.gov.uk` | OGL / commercial terms | OAuth 2.0 |
| GOV.UK Search | `www.gov.uk/api/search.json` | OGL v3 | None |

---

## Stack

- Python 3.10+
- [FastMCP](https://gofastmcp.com) v3 (streamable HTTP transport)
- [httpx](https://www.python-httpx.org/) (async HTTP with connection pooling)
- [lxml](https://lxml.de/) (LegalDocML and CLML XML parsing)
- [Pydantic](https://docs.pydantic.dev/) v2 (input validation, output serialisation)
- [Fly.io](https://fly.io/) (London region, auto-stop/start)
