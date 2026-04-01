# uk-legal-mcp

[![SafeSkill 92/100](https://img.shields.io/badge/SafeSkill-92%2F100_Verified%20Safe-brightgreen)](https://safeskill.dev/scan/paulieb89-uk-legal-mcp)

A Model Context Protocol server for UK legal research. Connects AI assistants to case law, legislation, parliamentary debates, OSCOLA citation parsing, and HMRC tax data through a single endpoint.

**15 tools across 5 modules.** One connection. Read-only. No API keys required for 14 of 15 tools.

```
MCP Client (Claude, Cursor, etc.)
        |
        v
  uk-legal-mcp gateway  (Streamable HTTP)
  +----------------------------------------------------+
  |                                                    |
  |  case_law      TNA Find Case Law API               |
  |  legislation   legislation.gov.uk + i.AI Lex API   |
  |  parliament    Hansard API + Members API            |
  |  citations     OSCOLA regex parser (no network)     |
  |  hmrc          HMRC sandbox/prod + GOV.UK search    |
  |                                                    |
  +----------------------------------------------------+
```

## Quickstart

### Connect to the hosted server

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "uk-legal": {
      "type": "streamable-http",
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

### Run locally

```bash
pip install -e .
python -m src.gateway
# Server starts on http://localhost:8000/mcp
```

Inspect with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

---

## Tools

### Case Law

| Tool | What it does |
|------|-------------|
| `case_law_search` | Full-text search of UK judgments. Filter by court, judge, party, date range. |
| `case_law_get_judgment` | Retrieve full LegalDocML XML for a judgment by TNA URI slug. |

Upstream: [TNA Find Case Law](https://caselaw.nationalarchives.gov.uk/) (Atom/XML). Rate limit: 1,000 req/5 min. Cached 1 hour.

### Legislation

| Tool | What it does |
|------|-------------|
| `legislation_search` | Search Acts of Parliament and Statutory Instruments via the i.AI Lex API. |
| `legislation_get_toc` | Table of contents for an Act — parts, chapters, sections, schedules. |
| `legislation_get_section` | Retrieve a specific section with territorial extent, in-force status, and version date. |

Upstream: [legislation.gov.uk](https://www.legislation.gov.uk/) (CLML XML) + [i.AI Lex API](https://lex.lab.i.ai.gov.uk) (JSON). Cached 24 hours.

**Note:** Always check the `extent` field. A section may apply to England and Wales but not Scotland or Northern Ireland.

### Parliament

| Tool | What it does |
|------|-------------|
| `parliament_search_hansard` | Search Hansard debate contributions by exact phrase. |
| `parliament_vibe_check` | Assess parliamentary reception of a policy topic. Searches Hansard, then uses LLM sampling to classify sentiment, supporters, opponents, and concerns. |
| `parliament_find_member` | Look up an MP or Lord by name. Returns member ID for use with `member_debates`. |
| `parliament_member_debates` | Retrieve a specific member's Hansard contributions, optionally filtered by topic. |

Upstream: [hansard-api.parliament.uk](https://hansard-api.parliament.uk) + [members-api.parliament.uk](https://members-api.parliament.uk). Not cached (live data).

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

## Architecture

```
src/
  gateway.py            FastMCP gateway — mounts all modules, applies middleware
  deps.py               Shared httpx clients (lifespan-managed) + error formatting
  modules/
    case_law/           TNA Find Case Law (Atom/XML parsing)
    legislation/        legislation.gov.uk (CLML XML) + Lex API (JSON)
    parliament/         Hansard API + Members API (JSON)
    citations/          OSCOLA regex engine (compiled once, lru_cache)
    hmrc/               HMRC OAuth + GOV.UK search (JSON)
tests/
  test_citations.py     35 unit tests — regex patterns, resolution, disambiguation
```

Each module is a standalone `FastMCP` instance mounted into the gateway with a namespace prefix (`case_law_`, `legislation_`, etc.). All modules share a single httpx client pool via the gateway's lifespan context.

**Middleware stack (gateway level):**

| Middleware | Purpose |
|-----------|---------|
| `RateLimitingMiddleware` | 50 req/min per client across all modules |
| `ResponseLimitingMiddleware` | 80,000 char cap (LegalDocML XML can exceed 200k) |
| `ResponseCachingMiddleware` | Per-module TTLs on case_law (1hr), legislation (24hr), hmrc (90 days) |

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
docker run -p 8000:8000 uk-legal-mcp
```

---

## Testing

```bash
pip install -e '.[test]'  # or: pip install pytest
pytest tests/test_citations.py -v
```

All 35 citation tests run offline with no API credentials.

---

## Upstream APIs and Licences

| Source | API | Licence | Auth |
|--------|-----|---------|------|
| TNA Find Case Law | `caselaw.nationalarchives.gov.uk` | [Open Justice Licence](https://caselaw.nationalarchives.gov.uk/open-justice-licence) | None |
| legislation.gov.uk | `legislation.gov.uk` | [OGL v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | None |
| i.AI Lex API | `lex.lab.i.ai.gov.uk` | OGL v3 | None |
| UK Parliament Hansard | `hansard-api.parliament.uk` | [Open Parliament Licence](https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/) | None |
| UK Parliament Members | `members-api.parliament.uk` | Open Parliament Licence | None |
| HMRC | `test-api.service.hmrc.gov.uk` | OGL / commercial terms | OAuth 2.0 |
| GOV.UK Search | `www.gov.uk/api/search.json` | OGL v3 | None |

---

## Stack

- Python 3.12
- [FastMCP](https://gofastmcp.com) v3 (streamable HTTP transport)
- [httpx](https://www.python-httpx.org/) (async HTTP with connection pooling)
- [lxml](https://lxml.de/) (LegalDocML and CLML XML parsing)
- [Pydantic](https://docs.pydantic.dev/) v2 (input validation, output serialisation)
- [Fly.io](https://fly.io/) (London region, auto-stop/start)
