# API Reference — Upstream Services

## APIs in use

### TNA Find Case Law
- **Base:** `https://caselaw.nationalarchives.gov.uk`
- **Format:** Atom/XML (search), LegalDocML XML (judgments)
- **Auth:** None
- **Rate limit:** 1,000 req / 5 min
- **Licence:** Open Justice Licence
- **Endpoints used:**
  - `GET /atom.xml?query=...` — search judgments
  - `GET /{uri}/data.xml` — retrieve judgment XML

### legislation.gov.uk
- **Base:** `https://www.legislation.gov.uk`
- **Format:** CLML XML
- **Auth:** None
- **Rate limit:** 3,000 req / 5 min
- **Licence:** OGL v3
- **Endpoints used:**
  - `GET /{type}/{year}/{number}/data.xml` — full Act XML (for ToC)
  - `GET /{type}/{year}/{number}/section/{section}/data.xml` — single section

### i.AI Lex API
- **Base:** `https://lex.lab.i.ai.gov.uk`
- **Format:** JSON
- **Auth:** None
- **Rate limit:** Undocumented (treat as 60 req/min)
- **Licence:** OGL v3
- **Endpoints used:**
  - `GET /api/search?q=...` — ranked legislation search

### Hansard API
- **Base:** `https://hansard-api.parliament.uk`
- **Format:** JSON
- **Auth:** None
- **Rate limit:** Unknown (observed stable at moderate load)
- **Licence:** Open Parliament Licence
- **Endpoints used:**
  - `GET /search.json?searchTerm=...` — search debate contributions
- **Warning:** Do NOT use `hansard.parliament.uk` — it is behind Cloudflare JS challenge (403).

### Members API
- **Base:** `https://members-api.parliament.uk/api`
- **Format:** JSON
- **Auth:** None
- **Licence:** Open Parliament Licence
- **Endpoints used:**
  - `GET /Members/Search?Name=...` — search members by name

### HMRC API
- **Base:** `https://test-api.service.hmrc.gov.uk` (sandbox) or `https://api.service.hmrc.gov.uk` (production)
- **Format:** JSON
- **Auth:** OAuth 2.0 (client credentials flow, `read:vat` scope)
- **Register:** https://developer.service.hmrc.gov.uk
- **Endpoints used:**
  - `POST /oauth/token` — obtain access token
  - `GET /organisations/vat/{vrn}/obligations` — MTD VAT obligations

### GOV.UK Search
- **Base:** `https://www.gov.uk/api/search.json`
- **Format:** JSON
- **Auth:** None
- **Licence:** OGL v3
- **Endpoints used:**
  - `GET /api/search.json?q=...&filter_organisations=hm-revenue-customs` — HMRC guidance search

## APIs not yet integrated

These are public UK Parliament APIs that could extend the parliament module:

| API | Base URL | What it provides |
|-----|----------|-----------------|
| Bills | `bills-api.parliament.uk` | Bill search, stages, sponsors, amendments, progress tracking |
| Commons Votes | `commonsvotes-api.parliament.uk` | Division records, how each MP voted, rebel flags |
| Lords Votes | `lordsvotes-api.parliament.uk` | Lords division records |
| Financial Interests | `interests-api.parliament.uk` | Register of Members' Financial Interests |
| Petitions | `petition.parliament.uk` | Petitions, signature counts, government responses |
| Committees | `committees-api.parliament.uk` | Select committees, evidence, publications |

All are public, JSON format, no authentication required. See the [i.AI Parliament MCP](https://github.com/i-dot-ai/parliament-mcp) for reference implementations using these APIs.
