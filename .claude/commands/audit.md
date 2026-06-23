# /audit — Conformance check before starting work

Run a full conformance audit of the uk-legal-mcp server. Report PASS / WARN / FAIL per check.
Do not start any implementation work until this audit is complete.

## Step 1: Syntax check all source modules
```
python -m py_compile src/gateway.py
python -m py_compile src/deps.py
python -m py_compile src/envelope.py
python -m py_compile src/xml_safe.py
for each file in src/modules/**/tools.py:
    python -m py_compile <file>
```
Report: PASS if all compile clean, FAIL with filenames if any fail.

## Step 2: Run the non-live test suite
```
uv run pytest -m "not live" -q
```
Report: PASS with count, or FAIL with the first failure output.

## Step 3: Check tool description conformance
```
uv run python tests/audit_descriptions.py
```
Report: PASS if all descriptions follow 4-part pattern, WARN with list of deviating tools.

## Step 4: Parliament param audit (static, no network)
```
uv run python tests/audit_parliament_params.py
```
Report: PASS if no wire-name mismatches, WARN with specific param names that diverge from Swagger.

## Step 5: Parliament response field audit (static, no network)
```
uv run python tests/audit_parliament_responses.py
```
Report: PASS if no schema mismatches, WARN with field names that are consumed but undeclared.

## Step 6: Check for known antipatterns in source
Search src/ for:
- `RuntimeError` in tools — should be `ToolError` (WARN with locations)
- `lxml.etree.fromstring` directly — should use `parse_xml` from xml_safe (FAIL if found)
- `ctx.request_context.lifespan_state` — removed v2 API (FAIL if found)

## Final output
Print a summary table:
| Check                    | Status | Notes |
|--------------------------|--------|-------|
| Syntax                   | ...    | ...   |
| Non-live tests           | ...    | ...   |
| Description conformance  | ...    | ...   |
| Parliament params        | ...    | ...   |
| Parliament responses     | ...    | ...   |
| Antipattern scan         | ...    | ...   |

If any check is FAIL, do not proceed with implementation. Fix the failure first.
