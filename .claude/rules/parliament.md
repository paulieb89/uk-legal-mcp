---
paths: ["src/modules/parliament/**", "tests/test_parliament*.py", "tests/audit_parliament*.py"]
---
# Parliament Module Rules

## API endpoint — CRITICAL
- USE:    `hansard-api.parliament.uk`  (the data API)
- NEVER:  `hansard.parliament.uk`      (the website — Cloudflare JS challenge → 403)
This distinction has caused production failures. Every new parliament tool must use the API endpoint.

## Known hard caps (do NOT paginate past these silently)
| API                              | Cap         |
|----------------------------------|-------------|
| interests-api.parliament.uk      | 20/page     |
| commonsvotes-api.parliament.uk   | 25/page     |
| /search.json (Hansard)           | 4 rows only |

If a tool hits these caps, the response must document the cap in `next_steps` or metadata,
not silently return a partial set as if it were complete.

## Wire param names — check the Swagger (references/hansard-swagger-v1.json)
Silent-200 wire-name lies are the #1 bug class here. The audit catches them:
- `audit_parliament_params.py` — validates request param keys against the Swagger contract
- Example past bug: sending `column` when spec declares `columnNumber`
Run the param audit after ANY change to `client.get(...)` calls in parliament tools.

## Response field access — check the Swagger schema
- `audit_parliament_responses.py` — validates field consumption against declared schema
- Known risk: `Rank` consumed as a count (Obs 173 lie shape) — use semantic heuristic check
- PascalCase `.get("Field")` access must match the Swagger `$ref` chain

## Cartography chain honesty
- `Use as {x} in <consumer>` promises in Field descriptions are tested by `audit_cartography_chains.py`
- Known footgun: Hansard division `id` vs Lords Votes `divisionId` — different ID spaces
- Decision tree for failed chains: drop the cartography claim OR add `_populate_votes_ids` cross-resolve
- See `parliament_get_debate_divisions._populate_votes_ids` for the cross-resolve pattern

## Column-number carry-forward (OSCOLA Hansard citation)
Column numbers carry forward in Hansard transcripts — a contribution may not have its own column header.
The parser must carry the last seen column forward, not leave it null.
This is required for OSCOLA Hansard citations: `HC Deb 15 January 2024, col 123`.
