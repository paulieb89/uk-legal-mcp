---
paths: ["src/modules/citations/**", "tests/test_citations.py"]
---
# Citations Module Rules

## OSCOLA 4th edition forms — always check these

### Legislation (OPEN TRACKED ISSUE)
- CORRECT:  `Employment Rights Act 1996, s 98`
- WRONG:    `s.98 Employment Rights Act 1996`
- Rule: Act name comes FIRST, section with space not dot: `s 98` not `s.98`
Do NOT introduce new code that outputs the wrong form.
Do NOT close this issue by patching display only — the canonical form must come from the parser.

### Neutral citations
- `[2024] UKSC 1`  — square brackets, court, number
- `[2024] EWCA Civ 100`

### Law reports
- `[2024] 1 WLR 100`  — volume before report series

## citations_resolve → citations_format_oscola ordering
These two tools have a mandatory call order:
1. `citations_resolve` — must be called first, validates and resolves the citation
2. `citations_format_oscola` — called second, formats the resolved citation
Calling `format_oscola` on an unresolved citation is a protocol violation.
Agents downstream in BOUCH enforce this; do not add shortcuts that skip resolve.

## Self-contained module — no external HTTP in pure regex paths
The citations module uses `httpx` only for TNA HEAD probes in the resolver.
Pure parsing (citations_parse) has `openWorldHint=False` and no external calls.

## Test discipline for citations
- tests/test_citations.py is the canonical guard suite
- Any regex change must have a test case demonstrating the old wrong form
  AND a test case asserting the correct form
- The legislation citation OSCOLA issue should have a failing test
  (or a test marked xfail) before the fix lands
