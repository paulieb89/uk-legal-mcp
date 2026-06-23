---
paths: ["src/modules/**/*.py"]
---
# Tool Authoring Standards

## The 4-part description pattern (MANDATORY — ChatGPT has no skills layer)
Every tool description must follow this structure:
```
USE WHEN <specific trigger condition>.
Returns <what the data actually is>.
AFTER calling, use <downstream tool> if <condition>.
Source: <upstream authority>.
```
Deviation from this pattern means ChatGPT workflow encoding breaks — tool descriptions
are the ONLY layer that cohort sees. See `docs/internal/chatgpt-workflow-encoding.md`.

## Content discipline
- NEUTRAL PROCEDURAL: "USE WHEN searching tenancy case law"
- NOT OPINIONATED ADVOCACY: "USE WHEN defending a tenant"
ChatGPT's broad audience may misread advocacy framing as legal advice.

## Input models
- Use Pydantic `BaseModel` with `ConfigDict(extra="forbid")` — no extra params silently pass through
- Field descriptions are load-bearing: they control LLM param-filling behaviour
- When you change a parameter's behaviour, update the Field description to match

## Tool function structure
```python
@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": True})
async def tool_name(params: InputModel, ctx: Context) -> str:
    client = ctx.lifespan_context["http"]   # or xml_http / legislation_http
    try:
        resp = await client.get(...)
        resp.raise_for_status()
    except Exception as e:
        raise ToolError(format_http_error(e))
    return json.dumps(result, indent=2)
```

## Return type discipline
- Tools: return JSON strings (`model.model_dump_json(indent=2)` or `json.dumps(...)`)
- Do NOT return raw dicts — FastMCP serialises them differently across versions
- `openWorldHint=False` ONLY for self-contained tools with no upstream (e.g. citations_parse)

## HTTP client selection
| Client key        | Use for                                           |
|-------------------|---------------------------------------------------|
| `http`            | JSON APIs (Hansard, Members, Bills, Votes, etc.)  |
| `xml_http`        | XML/Atom APIs (TNA case law)                      |
| `legislation_http`| legislation.gov.uk (CloudFront WAF, curl_cffi)   |

## XML safety
External XML MUST go through `src/xml_safe.py:parse_xml`.
NEVER call `lxml.etree.fromstring` directly — XXE / billion-laughs / external-DTD risk.

## After adding or modifying a tool
1. `python -m py_compile src/modules/<module>/tools.py`
2. `uv run pytest -m "not live" -q`
3. For descriptions: `uv run python tests/audit_descriptions.py`
4. For parliament tools: `uv run python tests/audit_parliament_params.py`
