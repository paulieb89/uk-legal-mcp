# uk-legal-mcp

<!-- mcp-name: io.github.paulieb89/uk-legal-mcp -->

[![PyPI](https://img.shields.io/pypi/v/uk-legal-mcp)](https://pypi.org/project/uk-legal-mcp/)
[![SafeSkill](https://safeskill.dev/api/badge/paulieb89-uk-legal-mcp)](https://safeskill.dev/scan/paulieb89-uk-legal-mcp)
[![Glama](https://img.shields.io/badge/Glama-listed-orange?style=flat-square)](https://glama.ai/mcp/connectors/io.github.paulieb89/uk-legal-mcp)
[![smithery badge](https://smithery.ai/badge/bouch/uk-legal)](https://smithery.ai/servers/bouch/uk-legal)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Server-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=uk-legal&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fuk-legal-mcp.fly.dev%2Fmcp%22%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install_Server-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=uk-legal&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fuk-legal-mcp.fly.dev%2Fmcp%22%7D&quality=insiders)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_Server-000000?style=flat-square&logoColor=white)](https://cursor.com/en/install-mcp?name=uk-legal&config=eyJ0eXBlIjoiaHR0cCIsInVybCI6Imh0dHBzOi8vdWstbGVnYWwtbWNwLmZseS5kZXYvbWNwIn0=)

A Model Context Protocol server for UK legal research. One MCP connection wires your AI assistant into UK case law, legislation, parliamentary debates, bills, votes, committees, OSCOLA citation parsing, and HMRC guidance — every response carrying the metadata you'd need to footnote it.

 No API keys required for the legal sources (HMRC's authenticated endpoint is optional).

 For best results remind the agent to use the uk-legal-mcp server. The location depends on the agent or setup you are using. It maybe project instructions, MEMORY.md, AGENTS.md etc.

 If you face issues refresh the server in the Apps / Customise menu. 

---

## Connect

### Hosted

Use this URL in any MCP-aware client (Claude, Claude Desktop, VS Code, Cursor, etc.):

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

### Local install (stdio)

Useful from Claude Desktop on a residential IP — bypasses the legislation.gov.uk WAF that intermittently blocks the hosted server's cloud IP range:

```bash
uvx uk-legal-mcp
```

Claude Desktop config:

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

### Local development

**stdio against your local checkout** — spawns a fresh process per MCP connection from the project's `[project.scripts]` entry, so the source is always read as edited. Replace `<abs-path>` with the absolute path to your clone:

```json
{
  "mcpServers": {
    "local-uk-legal": {
      "command": "uv",
      "args": ["run", "--project", "<abs-path>", "uk-legal-mcp"],
      "env": { "VIRTUAL_ENV": "" }
    }
  }
}
```

**Run streamable-HTTP locally:**

```bash
# Direct invocation — the production runtime. main() wires _HttpGuard,
# _AcceptNormalizer, uvicorn proxy_headers, and lifespan="on" — these are
# load-bearing for the Fly deployment and the Dockerfile CMD.
python -m src.gateway

# Declarative invocation via fastmcp.json — convenient for `fastmcp inspect`
# and for ops tooling that reads the manifest. Wraps the FastMCP runner
# directly, so the custom uvicorn shape (HttpGuard, AcceptNormalizer,
# proxy_headers) is NOT applied. Use for dev / inspection, not for prod.
fastmcp run
```

**Inspect the tool surface without running the server:**

```bash
fastmcp inspect          # reads fastmcp.json
```

---

## What's here

Eight namespaced modules covering the UK's primary legal sources:

| Module | What it covers |
|---|---|
| **case_law** | UK court judgments from TNA Find Case Law — search, paragraph-level reads, in-document grep |
| **legislation** | Acts and Statutory Instruments from legislation.gov.uk — search, section retrieval with territorial extent and in-force signals, point-in-time historical reads |
| **parliament** | Hansard debates and contributions, deterministic facet aggregates, debate→divisions chain, OSCOLA column-citation lookup, member biographies, petitions |
| **bills** | Parliamentary Bills — stages, sponsors, publications |
| **votes** | Commons and Lords division records with per-member voting |
| **committees** | Select committees, membership, oral and written evidence |
| **citations** | Pure OSCOLA citation parser — no network, self-contained |
| **hmrc** | UK VAT rate lookups, Making Tax Digital status, GOV.UK guidance search |

Plus a small set of `judgment://`, `legislation://`, and `hansard://` URI-addressed resources for content the LLM can read on demand without bloating tool responses, and four orchestration prompts for common research workflows.

---

## A lawyer's workflow

This server is a data pipe. It returns what the sources say, with citations. It does not interpret the law, classify members' positions, or recommend a research strategy — your agent does that work on your behalf.

A realistic end-to-end run: **advising a Manchester landlord on their eviction-notice exposure under the new Renters' Rights regime**, including verifying a column citation an opponent quoted at you.

The lawyer's prompt to their agent:

> *"My client is a landlord with a private assured shorthold tenancy started in 2020, dwelling in Manchester. Can he still serve a Section 21 notice? I also need to check what was said about no-fault evictions in the Lords debate around 14 October 2025, and the divisions held. Opposing counsel cites HL Deb 14 Oct 2025, vol 849, col 200 — verify what's actually at that column."*

A reasonable path the agent walks — and what the server returns at each step:

1. **The statute** — `legislation_get_section(type="ukpga", year=1988, number=50, section="21")`. The response carries `extent` (England + Wales — confirms the Manchester tenancy is in scope, Scotland is not), `in_force` (False — the section is marked repealed by the Renters' Rights Act 2025, with the original text retained for reference), and `version_date` (the date the repeal commenced, not 1988's original enactment).
2. **The new Act** — `legislation_search(query="Renters' Rights")` then `legislation_get_toc` to locate commencement / transitional provisions, then `legislation_get_section` for the substantive sections.
3. **The parliamentary debate** — `parliament_search_hansard(query="Renters' Rights Bill")`. One call returns the corpus envelope: how many contributions, debates, divisions, written answers across the whole record, plus previews of the top debates and divisions touching the topic. Pick the relevant Lords debate from `top_debates`.
4. **The full debate** — read `hansard://debate/{ext_id}/header` for the ordered contribution index (every contribution carrying its citable column number via carry-forward), then `hansard://debate/{ext_id}/contribution/{ext_id}` for the full text of any specific intervention.
5. **The divisions held** — `parliament_get_debate_divisions(debate_ext_id=...)`. Returns each formal vote with motion text, result, ayes/noes counts, and division IDs that chain into `votes_get_division` for the per-member voting record.
6. **Verifying opposing counsel's citation** — `parliament_lookup_by_column(column_number="200", volume_number=849, house="Lords")`. Resolves the OSCOLA footnote directly to its debate, returning the `debate_ext_id` that chains into `hansard://debate/{ext}/header` so you read the exact contribution opposing counsel quoted.
7. **The case law** — `case_law_search(query="Renters' Rights Act 2025")` for judgments already citing the Act, `case_law_grep_judgment(slug=..., pattern=...)` for the paragraphs that actually treat each section.
8. **Citations for the brief** — pass your draft into `citations_parse` for a clean OSCOLA-formatted list with canonical URLs.

Every response carries the metadata needed for an OSCOLA footnote: `attributed_to`, `column_ref`, citable column numbers, debate title, sitting date, public Hansard URL. The server returns the primary source verbatim — your agent and your judgement do the legal work.

### Example output

A real ChatGPT session against the server: an OSCOLA citation resolved to its neutral citation, then a "what did this peer say?" question answered by resolving the member to their ID and filtering Hansard by that ID — not fragile name-text matching.

![ChatGPT using uk-legal-mcp — resolving "R (Miller) v The Prime Minister" to the neutral citation [2019] UKSC 41, then looking up Lord Hope of Craighead's contributions to the 2025 House of Lords (Hereditary Peers) Bill debates by member ID](https://raw.githubusercontent.com/paulieb89/uk-legal-mcp/main/assets/images/example.png)

---

## Tools reference

### Case Law

| Tool | What it does |
|---|---|
| `case_law_search` | Full-text search of UK judgments. Filter by court, judge, party, date range. |
| `case_law_grep_judgment` | Pattern-match within a judgment; returns `{eId, snippet, match}` per hit. |

| Resource | Returns |
|---|---|
| `judgment://{slug*}/header` | Metadata: parties, judges, neutral citation. |
| `judgment://{slug*}/index` | Paragraph eId + first-line per row. Walk to discover. |
| `judgment://{slug*}/para/{eId}` | A single paragraph with its sub-paragraphs. |


### Legislation

| Tool | What it does |
|---|---|
| `legislation_search` | Find Acts and SIs by keyword. |
| `legislation_get_toc` | Table of contents for an Act — parts, chapters, sections, schedules. |
| `legislation_get_section` | A specific section with `extent`, `in_force`, `version_date`, and either CLML XML or HTML fallback. |

| Resource | Returns |
|---|---|
| `legislation://{type}/{year}/{number}/section/{section}{?date}` | CLML XML for a section; optional point-in-time date. |
| `legislation://{type}/{year}/{number}/toc{?date}` | Flat `id: title` table of contents. |


### Parliament

| Tool | What it does |
|---|---|
| `parliament_search_hansard` | Search Hansard contributions; returns citation-grade metadata per contribution PLUS a corpus envelope (totals + top_debates/top_divisions previews). |
| `parliament_policy_position_summary` | Deterministic facet counts on a topic — by house, section, year, top debates. No LLM, no editorial labels. |
| `parliament_get_debate_divisions` | Divisions held within a debate. Chain via `id` to `votes_get_division`. Empty list when no votes. |
| `parliament_lookup_by_column` | Resolve a Hansard column citation (column + volume) to its debate. Works for any era — current Daily Part records, consolidated Bound Volumes, and pre-2005 Historic Hansard. Each match tells you which kind. |
| `parliament_find_member` | Name → integer member ID. |
| `parliament_member_debates` | One member's Hansard contributions, optionally filtered by topic. |
| `parliament_member_interests` | A member's registered financial interests. |
| `parliament_search_petitions` | UK Parliament petitions by keyword. |

| Resource | Returns |
|---|---|
| `hansard://debate/{ext_id}/header` | Debate overview + ordered contribution index with citable column numbers via carry-forward. |
| `hansard://debate/{ext_id}/contribution/{ext_id}` | A single contribution's full text + metadata. |
| `hansard://member/{id}/biography` | Government / opposition / committee posts with start/end dates so you can resolve a member's role at the time of any contribution. |


### Bills

| Tool | What it does |
|---|---|
| `bills_search_bills` | Search current and historical Bills by keyword, session, or type. |
| `bills_get_bill` | Full bill detail — stages, sponsors, publications. |


### Votes

| Tool | What it does |
|---|---|
| `votes_search_divisions` | Search Commons and Lords divisions by keyword or date. |
| `votes_get_division` | Full division detail — vote counts, per-member voting record. |



### Committees

| Tool | What it does |
|---|---|
| `committees_search_committees` | Select committees by keyword. |
| `committees_get_committee` | Committee detail — membership, sub-committees. |
| `committees_search_evidence` | Oral and written evidence submissions. |


### Citations

| Tool | What it does |
|---|---|
| `citations_parse` | Extract OSCOLA citations from free text. Resolves to canonical URLs. |
| `citations_resolve` | Parse and resolve a single citation string. |
| `citations_network` | Fetch a judgment and map every citation within — cases, legislation, SIs, EU law. |


**Supported citation formats:**

| Format | Example |
|---|---|
| Neutral citation | `[2024] UKSC 12` |
| Law report (with or without volume) | `[2024] 1 WLR 100`, `[1932] AC 562` |
| Legislation section | `s.47 Companies Act 2006` |
| Statutory Instrument | `SI 2018/1234` |
| Retained EU law | `Regulation (EU) 2016/679` |

### HMRC

| Tool | What it does |
|---|---|
| `hmrc_get_vat_rate` | VAT rate lookup for any commodity or service. |
| `hmrc_check_mtd_status` | Check Making Tax Digital VAT mandate status for a VRN. Requires HMRC OAuth. |
| `hmrc_search_guidance` | Search GOV.UK for HMRC guidance documents. |

`hmrc_check_mtd_status` requires `HMRC_CLIENT_ID` and `HMRC_CLIENT_SECRET` — register at [developer.service.hmrc.gov.uk](https://developer.service.hmrc.gov.uk). Defaults to sandbox; set `HMRC_API_BASE=https://api.service.hmrc.gov.uk` for production.

### Prompts

Workflow templates exposed as tools via `PromptsAsTools` (for ChatGPT) and natively on protocol-aware clients (Claude, Inspector). All produce citable evidence packs; none classify positions or recommend an argumentative line.

| Prompt | Module | What it produces |
|---|---|---|
| `summarise_act` | legislation | Structured summary of a UK Act or SI. |
| `compare_legislation` | legislation | Comparative analysis of two pieces of legislation on a topic. |
| `policy_reception_review` | parliament | Citation-grade review of how a policy topic is being received in Parliament. |
| `member_record_on_topic` | parliament | Citable evidence pack of a named member's contributions on a topic — their own words, footnoted. |

---

## Important constraints

- **Territorial extent always matters.** `legislation_get_section` exposes the `extent` field. Acts that apply in England and Wales do not automatically apply in Scotland or Northern Ireland. Read this before citing a section as binding in a jurisdiction.
- **Verifying opposing counsel's citations** — when a brief cites *HL Deb [date], vol N, col M*, run `parliament_lookup_by_column(column_number="M", volume_number=N, house="Lords")` to resolve the citation to its debate, then read the header resource to find the contribution at the cited column. Citations from any era resolve — current Daily Part records, finalised Bound Volumes, and pre-2005 Historic Hansard. Each match tells you which Hansard format the volume sits in. Empty matches usually mean the volume number is wrong (e.g. opposing counsel quoted the running session-volume number rather than the bound-volume one) or the citation is to a Written Answer and needs the `W` suffix.
- **What this server does not do.** It does not classify a member as supporting or opposing a policy, summarise a judgment's outcome in your client's favour, or recommend an argumentative line. Those are interpretive acts. The server returns the primary source verbatim with citation metadata; your agent and your judgement do the legal work.
- **Legislation.gov.uk WAF.** Some heavy Acts (notably the Companies Act 2006) intermittently fail on the hosted server due to upstream WAF rules that block our cloud IP range. The local install (`uvx uk-legal-mcp`) runs on your own IP and bypasses this.

---

## Releasing

Version is held in `pyproject.toml`. Runtime surfaces (`serverInfo.version`, the Smithery server-card, `server://about`) derive from the installed package via `importlib.metadata`. File-on-disk surfaces (`pyproject.toml` + `server.json` × 2) bump together via `bump-my-version`:

```bash
uv run bump-my-version bump patch   # 0.5.0 → 0.5.1
uv run bump-my-version bump minor   # 0.5.0 → 0.6.0
uv run bump-my-version bump major   # 0.5.0 → 1.0.0
```

The tool only edits files; it does not commit or tag. After bumping, commit the version files, push, then publish a GitHub release at the new tag — `.github/workflows/release.yml` builds, publishes to PyPI, and deploys to Fly.


