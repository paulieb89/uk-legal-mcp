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

### Bills API
- **Base:** `https://bills-api.parliament.uk/api/v1`
- **Format:** JSON
- **Auth:** None
- **Licence:** Open Parliament Licence
- **Endpoints used:**
  - `GET /Bills?SearchTerm=...` — search bills by keyword, session, house, stage
  - `GET /Bills/{id}` — bill detail (sponsors, stages, Royal Assent date)

### Commons Votes API
- **Base:** `https://commonsvotes-api.parliament.uk`
- **Format:** JSON
- **Auth:** None
- **Licence:** Open Parliament Licence
- **Endpoints used:**
  - `GET /data/divisions.json/search` — search Commons divisions (25/page hard cap)
  - `GET /data/division/{id}.json` — division detail with voter lists

### Lords Votes API
- **Base:** `https://lordsvotes-api.parliament.uk`
- **Format:** JSON
- **Auth:** None
- **Licence:** Open Parliament Licence
- **Endpoints used:**
  - `GET /data/Divisions/search` — search Lords divisions
  - `GET /data/Divisions/{id}` — division detail with voter lists, includes `isGovernmentWin`

### Financial Interests API
- **Base:** `https://interests-api.parliament.uk/api/v1`
- **Format:** JSON
- **Auth:** None
- **Rate limit:** Unknown (20/page hard cap, paginated internally)
- **Licence:** Open Parliament Licence
- **Endpoints used:**
  - `GET /Interests?MemberId=...` — registered financial interests for a member

### Petitions API
- **Base:** `https://petition.parliament.uk`
- **Format:** JSON
- **Auth:** None
- **Licence:** Open Parliament Licence
- **Endpoints used:**
  - `GET /petitions.json?q=...` — search petitions by keyword and state

### Committees API
- **Base:** `https://committees-api.parliament.uk/api`
- **Format:** JSON
- **Auth:** None
- **Licence:** Open Parliament Licence
- **Endpoints used:**
  - `GET /Committees` — list/search select committees
  - `GET /Committees/{id}` — committee detail
  - `GET /Committees/{id}/Members` — committee membership
  - `GET /OralEvidence?CommitteeId=...` — oral evidence sessions
  - `GET /WrittenEvidence?CommitteeId=...` — written evidence submissions
