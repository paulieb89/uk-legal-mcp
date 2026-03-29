# uk-legal-mcp

> UK legal research MCP server — case law, legislation, parliament, OSCOLA citations, HMRC tax

**Stack:** Python 3.12 · FastMCP v3 · Fly.io · Streamable HTTP  
**Architecture:** Gateway + 5 mounted sub-modules (in-process, zero network hop)  
**Status:** Pre-production

---

## What it is

A single MCP server exposing 11 UK legal research tools across five domains. One connection, one endpoint, one authentication context. Built for AI-native legal research workflows.

```
MCP Client (Claude, etc.)
        │
        ▼
  uk-legal-mcp gateway  (uk-legal-mcp.fly.dev/mcp)
  ┌──────────────────────────────────────────────────┐
  │                                                  │
  │  case_law      → TNA Find Case Law API           │
  │  legislation   → legislation.gov.uk + Lex API    │
  │  parliament    → Hansard + Members API           │
  │  citations  ★  → OSCOLA parser (self-contained)  │
  │  hmrc          → HMRC APIs + GOV.UK search       │
  │                                                  │
  └──────────────────────────────────────────────────┘
```

---

## Tool Reference

### Case Law (`case_law_*`)

| Tool | Description |
|------|-------------|
| `case_law_search` | Search UK judgments by keyword, court, judge, party, date range |
| `case_law_get_judgment` | Retrieve full LegalDocML XML for a judgment by TNA URI |

**Upstream:** TNA Find Case Law API · **Rate limit:** 1,000 req/5 min · **Cache:** 1hr TTL

---

### Legislation (`legislation_*`)

| Tool | Description |
|------|-------------|
| `legislation_search` | Search Acts and SIs via i.AI Lex API (ranked, JSON) |
| `legislation_get_toc` | Get table of contents for an Act (section numbers + titles) |
| `legislation_get_section` | Retrieve a specific section with extent, in-force status, version date |

**Always surface `extent`** — a section may apply to England & Wales but not Scotland or NI.

**Upstream:** legislation.gov.uk + i.AI Lex API · **Rate limit:** 3,000 req/5 min · **Cache:** 24hr TTL

---

### Parliament (`parliament_*`)

| Tool | Description |
|------|-------------|
| `parliament_search_hansard` | Search Hansard for debates, speeches, questions |
| `parliament_vibe_check` | Assess parliamentary reception of a policy (uses LLM sampling) |
| `parliament_find_member` | Look up an MP or Lord by name (returns integer member ID) |
| `parliament_member_debates` | Retrieve contributions by a specific member |

**Upstream:** UK Parliament Members API + Hansard API · **Cache:** not cached (live data)

---

### Citations ★ (`citations_*`)

> **The differentiator.** No equivalent exists in the UK legal MCP ecosystem.

| Tool | Description |
|------|-------------|
| `citations_parse` | Extract all OSCOLA citations from free text. Resolves to URLs. Disambiguates via sampling. |
| `citations_resolve` | Parse and resolve a single citation string to its canonical URL |
| `citations_network` | Map all citations within a judgment (cases cited + legislation referenced) |

**Supported citation types:**

| Type | Example |
|------|---------|
| Neutral citation | `[2024] UKSC 12` |
| Law report | `[2024] 1 WLR 100` |
| Legislation section | `s.47 Companies Act 2006` |
| Statutory Instrument | `SI 2018/1234` |
| Retained EU law | `Regulation (EU) 2016/679` |

**No external API.** Fully self-contained — zero network dependency.  
Ambiguous citations (e.g. bare `[2024] EWHC 123`) are disambiguated via `ctx.sample()`.

---

### HMRC (`hmrc_*`)

| Tool | Description |
|------|-------------|
| `hmrc_get_vat_rate` | VAT rate for any commodity or service (standard/reduced/zero/exempt) |
| `hmrc_check_mtd_status` | Check MTD VAT mandate status for a VRN (requires HMRC OAuth) |
| `hmrc_search_guidance` | Search GOV.UK for HMRC guidance documents |

**Cache:** VAT rates 90 days, MTD status 24hr

---

## FastMCP Features Used

| Feature | Where |
|---------|-------|
| `mount()` direct | Gateway — in-process composition |
| `namespace=` / `prefix=` | Gateway — tools as `case_law_search`, `legislation_get_section` |
| `RateLimitingMiddleware` | Gateway — 50 req/min per client |
| `ResponseLimitingMiddleware` | Gateway — 80,000 char cap (LegalDocML can be 200k+) |
| `ResponseCachingMiddleware` | case_law, legislation, hmrc — per upstream stability |
| `@mcp.resource` + `{param}` | case_law, legislation, citations — stable URI-addressed docs |
| `@mcp.prompt` | legislation, parliament — reusable research workflows |
| `Depends()` | All modules (HTTP client), citations (compiled regex) |
| `ctx.sample()` | parliament_vibe_check, citations_parse (disambiguation) |
| `readOnlyHint=True` | All tools — entire server is read-only |

---

## Repository Structure

```
uk-legal-mcp/
├── src/
│   ├── gateway.py              # FastMCP gateway — mounts all modules
│   ├── deps.py                 # Shared httpx clients + error formatting
│   └── modules/
│       ├── case_law/           # TNA Find Case Law
│       │   ├── __init__.py
│       │   ├── tools.py
│       │   ├── resources.py
│       │   └── models.py
│       ├── legislation/        # legislation.gov.uk + i.AI Lex API
│       │   ├── __init__.py
│       │   ├── tools.py
│       │   ├── resources.py
│       │   ├── prompts.py
│       │   └── models.py
│       ├── parliament/         # Hansard + Members API
│       │   ├── __init__.py
│       │   ├── tools.py
│       │   ├── prompts.py
│       │   └── models.py
│       ├── citations/          # OSCOLA parser ★
│       │   ├── __init__.py
│       │   ├── tools.py
│       │   ├── patterns.py     # Compiled regex engine (lru_cache)
│       │   └── models.py
│       └── hmrc/               # VAT, MTD, GOV.UK guidance
│           ├── __init__.py
│           ├── tools.py
│           └── models.py
├── tests/
│   └── test_citations.py       # Unit tests — citations module (no API needed)
├── pyproject.toml
├── fly.toml
└── Dockerfile
```

---

## Deployment

### Prerequisites

```bash
pip install -e .
fly auth login
```

### Environment Variables

```bash
fly secrets set HMRC_CLIENT_ID=your_client_id
fly secrets set HMRC_CLIENT_SECRET=your_client_secret
# Optional: TNA computational analysis licence reference
fly secrets set COMPUTATIONAL_ANALYSIS=your_tna_licence_ref
```

### Deploy

```bash
fly launch --name uk-legal-mcp --region lhr
fly deploy
```

### Local Development

```bash
# Install dependencies
pip install -e .

# Run locally
python -m src.gateway

# Test with MCP Inspector
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

---

## Testing

```bash
# Run citations unit tests (no API or credentials needed)
pytest tests/test_citations.py -v

# Syntax check all modules
python -m py_compile src/gateway.py
python -m py_compile src/modules/citations/tools.py
python -m py_compile src/modules/citations/patterns.py
```

---

## Open Questions

- [ ] **TNA Computational Analysis Licence** — apply before bulk judgment fetching; required for large-scale programmatic access
- [ ] **Lex API rate limits** — undocumented; measure empirically during early build. Treat as 60 req/min until confirmed.
- [ ] **OSCOLA court code enumeration** — compile complete list of UKFTT divisions and UKUT chamber codes for patterns.py
- [ ] **`citations_network` cross-ref data** — TNA may not expose outbound citation links via API. BAILII scrape may be needed as fallback; evaluate during integration testing.
- [ ] **`ctx.sample()` in production** — confirm Claude client implements sampling handler. parliament_vibe_check and citations_parse both degrade gracefully if sampling is unavailable.
- [ ] **Lex API JSON schema** — undocumented; verify response field names against live API before shipping legislation_search.
- [ ] **HMRC MTD endpoint scope** — current implementation uses `read:vat` scope; verify this covers obligations lookup or if `write:vat` is also needed.

---

## Licences

| Source | Licence | Notes |
|--------|---------|-------|
| TNA Find Case Law | Open Justice Licence | Computational analysis requires separate application |
| legislation.gov.uk | Open Government Licence v3 | Attribution required |
| Hansard | Open Parliament Licence | Attribution required |
| HMRC APIs | OGL / commercial terms vary | Sandbox vs production environments differ |

---

## Build Order (from spec)

1. **`citations`** — no external API, pure Python, highest differentiating value. Establishes `Depends()` and `ctx.sample()` patterns.
2. **`case_law`** — TNA API is well-documented. Establishes resource template + XML parsing layer.
3. **`legislation`** — builds on case_law patterns. Adds Lex API JSON path + `@mcp.prompt`.
4. **`parliament`** — adds sampling-based sentiment analysis.
5. **`hmrc`** — simplest module, JSON tools with TTL caching.
6. **Gateway** — wire up mounts, middleware, integration test.
