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

## Existence checks — never trust the caller
Only citations_parse is pure regex (`openWorldHint=False`, no external calls,
every result `verified=false`). citations_resolve and citations_format_oscola
check existence through `deps.check_document_exists`: TNA for neutral citations,
legislation.gov.uk for SIs. format_oscola re-checks itself; its `confidence` and
`resolved_url` inputs are accepted for compatibility and ignored (audit L1).
Absent means a status in `TNA_ABSENT_STATUSES` / `LEGISLATION_ABSENT_STATUSES`
(legislation.gov.uk answers a missing document with 400); 429, 5xx and transport
failures are `transient`, never absent (audit L3). `verified` reports existence;
`confidence` is parse confidence (0.0 when the source confirmed absence).

## Test discipline for citations
- tests/test_citations.py is the canonical guard suite
- Any regex change must have a test case demonstrating the old wrong form
  AND a test case asserting the correct form
- The legislation citation OSCOLA issue should have a failing test
  (or a test marked xfail) before the fix lands
