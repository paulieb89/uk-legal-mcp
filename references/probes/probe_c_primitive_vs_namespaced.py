"""Probe C — primitive-vs-namespaced routing A/B.

For each golden prompt, send to OpenAI with two tool surfaces and capture which
tool the model picks (or whether it refuses to use the tools at all).

Surface A: v1-style namespaced (mirrors the live uk-legal MCP server)
Surface B: primitive (`legal.*` collapsed surface)

Score: does the model pick a tool from THIS server? Did it pick the SAME tool
across two runs (stability)? Does the primitive surface improve or hurt routing?

This is the single highest-leverage probe before committing to the v2 primitive
architecture.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI


# ---------------------------------------------------------------------------
# Golden prompts — the 5 we've discussed plus 1 simpler control case
# ---------------------------------------------------------------------------

GOLDEN_PROMPTS = [
    {
        "id": "pannick_rra",
        "prompt": (
            "What did Lord Pannick say about the Renters' Rights Bill in the Lords debate "
            "on 14 October 2025? Quote his exact words."
        ),
        "expected_a_any": [
            "parliament_find_member", "parliament_search_hansard",
            "parliament_get_debate_contributions",
        ],
        "expected_b_any": ["legal_search", "legal_get"],
    },
    {
        "id": "hale_online_safety",
        "prompt": (
            "Find Baroness Hale's contribution to the Online Safety Bill debate in the Lords."
        ),
        "expected_a_any": [
            "parliament_find_member", "parliament_search_hansard",
            "parliament_get_debate_contributions",
        ],
        "expected_b_any": ["legal_search", "legal_get"],
    },
    {
        "id": "smith_v_hmrc",
        "prompt": (
            "Find the case Smith v HMRC dealing with VAT recovery and give me the neutral citation."
        ),
        "expected_a_any": ["case_law_search", "citations_parse", "citations_resolve"],
        "expected_b_any": ["legal_search", "legal_verify_citation"],
    },
    {
        "id": "manchester_landlord",
        "prompt": (
            "A landlord in Manchester wants to evict a tenant under section 21 of the "
            "Housing Act 1988. What's the current status of the Renters' Rights Bill and "
            "how does it affect section 21 notices?"
        ),
        "expected_a_any": [
            "legislation_search", "legislation_get_section", "legislation_get_toc",
            "bills_search_bills", "bills_get_bill",
        ],
        "expected_b_any": ["legal_search", "legal_get"],
    },
    {
        "id": "pet_abduction_act",
        "prompt": "What does the Pet Abduction Act 2024 say about sentencing for cat theft?",
        "expected_a_any": [
            "legislation_search", "legislation_get_toc", "legislation_get_section",
        ],
        "expected_b_any": ["legal_search", "legal_get"],
    },
    {
        "id": "oscola_citation",
        "prompt": (
            "Format this as an OSCOLA citation: R v Brown, 1993, House of Lords, [1994] 1 AC 212."
        ),
        "expected_a_any": ["citations_parse", "citations_resolve"],
        "expected_b_any": ["legal_format_citation", "legal_parse_citations"],
    },
]


# ---------------------------------------------------------------------------
# Surface A — v1 namespaced (lifted from the live uk-legal MCP server)
# Subset chosen for the prompt set's coverage; full server has ~25 tools.
# ---------------------------------------------------------------------------

SURFACE_A_NAMESPACED = [
    {
        "type": "function",
        "function": {
            "name": "case_law_search",
            "description": (
                "Search UK court judgments from TNA Find Case Law. Returns judgments by "
                "title/party/court/date with neutral citations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "free-text search query"},
                    "court": {"type": "string"},
                    "year": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "case_law_grep_judgment",
            "description": "Grep within a specific judgment XML for a regex.",
            "parameters": {
                "type": "object",
                "properties": {
                    "judgment_uri": {"type": "string"},
                    "pattern": {"type": "string"},
                },
                "required": ["judgment_uri", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legislation_search",
            "description": (
                "Search UK Acts of Parliament and Statutory Instruments on legislation.gov.uk. "
                "Returns titles and URIs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "year": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legislation_get_section",
            "description": "Get the text of a specific section of an Act or SI by URI + section number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string"},
                    "section": {"type": "string"},
                },
                "required": ["uri", "section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legislation_get_toc",
            "description": "Get the table of contents for an Act or SI.",
            "parameters": {
                "type": "object",
                "properties": {"uri": {"type": "string"}},
                "required": ["uri"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parliament_search_hansard",
            "description": (
                "Search Hansard for contributions matching a query. Returns top contributions "
                "with citation metadata. Note: Hansard caps results at 4 per query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "house": {"type": "string", "enum": ["Commons", "Lords"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parliament_find_member",
            "description": "Find a Parliament member by name. Returns member ID.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parliament_get_debate_contributions",
            "description": (
                "Drill into a specific debate to retrieve contributions, optionally filtered "
                "by member_id. Canonical path when the member spoke in a debate but didn't say "
                "your topic phrase verbatim."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "debate_ext_id": {"type": "string"},
                    "member_id": {"type": "integer"},
                },
                "required": ["debate_ext_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parliament_member_debates",
            "description": (
                "Find a member's contributions where they USED a specific phrase (text-body search). "
                "Returns empty if the member didn't say the phrase verbatim."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "member_id": {"type": "integer"},
                    "topic": {"type": "string"},
                },
                "required": ["member_id", "topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bills_search_bills",
            "description": "Search UK parliamentary bills.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bills_get_bill",
            "description": "Get full details of a UK parliamentary bill by ID.",
            "parameters": {
                "type": "object",
                "properties": {"bill_id": {"type": "integer"}},
                "required": ["bill_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "citations_parse",
            "description": "Extract OSCOLA citations from free text.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "citations_resolve",
            "description": "Resolve a parsed OSCOLA citation to its canonical URL.",
            "parameters": {
                "type": "object",
                "properties": {"citation": {"type": "string"}},
                "required": ["citation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hmrc_get_vat_rate",
            "description": "UK VAT rate lookup for a specific product category.",
            "parameters": {
                "type": "object",
                "properties": {"category": {"type": "string"}},
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hmrc_search_guidance",
            "description": "Search HMRC guidance documents.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Surface B — primitive (collapsed)
# ---------------------------------------------------------------------------

SURFACE_B_PRIMITIVE = [
    {
        "type": "function",
        "function": {
            "name": "legal_search",
            "description": (
                "Search UK legal sources by free-text query. Routes across case law (court "
                "judgments), legislation (Acts and SIs), Hansard (parliamentary debates and "
                "member contributions), bills (parliamentary bills), votes (divisions), "
                "committees (select committee evidence), and HMRC guidance. Use scope to "
                "restrict; omit scope for a broad search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "free-text query"},
                    "scope": {
                        "type": "string",
                        "enum": ["case_law", "legislation", "hansard", "bills",
                                 "votes", "committees", "hmrc", "all"],
                        "description": "restrict to one source family; default 'all'",
                    },
                    "filters": {
                        "type": "object",
                        "description": (
                            "scope-specific filters: court/year for case_law; "
                            "year/extent for legislation; house/member_id/date for hansard; "
                            "etc. Free-form."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legal_get",
            "description": (
                "Fetch a specific UK legal document by URI. Handles judgment XML, legislation "
                "section text, Hansard debate or contribution, bill details, division detail. "
                "Optionally drill in via fragment (section number, paragraph eId, member_id, "
                "etc.) The URI scheme tells the tool which upstream to call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {
                        "type": "string",
                        "description": (
                            "URI of the document. Examples: 'judgment://ukut/aac/2021/95', "
                            "'legislation://ukpga/2024/15', 'hansard://debate/{ext_id}', "
                            "'bill://3796'"
                        ),
                    },
                    "fragment": {
                        "type": "string",
                        "description": "optional sub-resource (section number, member_id, etc.)",
                    },
                },
                "required": ["uri"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legal_verify_citation",
            "description": (
                "Confirm a UK legal citation resolves to a real document. Returns the canonical "
                "URI, title, and source if valid; otherwise reports the citation is unknown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "citation": {"type": "string", "description": "the citation in any common form"}
                },
                "required": ["citation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legal_parse_citations",
            "description": "Extract all OSCOLA-style citations from free text.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legal_format_citation",
            "description": (
                "Format a UK legal source as an OSCOLA citation. Provide the structured fields "
                "(case_name, year, court, citation) or the canonical URI of the source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_type": {
                        "type": "string",
                        "enum": ["case", "legislation", "hansard", "journal"],
                    },
                    "fields": {
                        "type": "object",
                        "description": "structured fields appropriate to source_type",
                    },
                },
                "required": ["source_type", "fields"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Probe runner
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    prompt_id: str
    surface: str
    tool_calls: list[dict[str, Any]]
    no_tool_response_excerpt: str | None
    raw_finish_reason: str
    expected_any: list[str]
    hit: bool


def run_one(client: OpenAI, model: str, prompt: str, tools: list, expected_any: list[str], surface: str, prompt_id: str) -> ProbeResult:
    """Send one prompt with one surface, return what the model picked."""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        tools=tools,
        tool_choice="auto",
    )
    choice = resp.choices[0]
    finish_reason = choice.finish_reason
    tcs = choice.message.tool_calls or []
    tool_calls_data = [
        {"name": tc.function.name, "args": tc.function.arguments}
        for tc in tcs
    ]
    no_tool_excerpt = None
    if not tcs:
        content = choice.message.content or ""
        no_tool_excerpt = content[:200]

    picked_names = [tc["name"] for tc in tool_calls_data]
    hit = any(p == e for p in picked_names for e in expected_any)

    return ProbeResult(
        prompt_id=prompt_id,
        surface=surface,
        tool_calls=tool_calls_data,
        no_tool_response_excerpt=no_tool_excerpt,
        raw_finish_reason=finish_reason,
        expected_any=expected_any,
        hit=hit,
    )


def run_probe(model: str = "gpt-5") -> dict:
    client = OpenAI()
    surfaces = [
        ("A_namespaced", SURFACE_A_NAMESPACED, "expected_a_any"),
        ("B_primitive", SURFACE_B_PRIMITIVE, "expected_b_any"),
    ]
    results = []
    for prompt_spec in GOLDEN_PROMPTS:
        for surface_name, tools, expected_key in surfaces:
            try:
                r = run_one(
                    client=client,
                    model=model,
                    prompt=prompt_spec["prompt"],
                    tools=tools,
                    expected_any=prompt_spec[expected_key],
                    surface=surface_name,
                    prompt_id=prompt_spec["id"],
                )
                results.append(r.__dict__)
            except Exception as exc:
                results.append({
                    "prompt_id": prompt_spec["id"],
                    "surface": surface_name,
                    "error": repr(exc),
                })
            time.sleep(0.5)
    return {"model": model, "results": results}


def print_report(report: dict) -> None:
    model = report["model"]
    rows = report["results"]
    print(f"\n=== Probe C report (model: {model}) ===\n")
    # group by prompt_id
    from collections import defaultdict
    by_prompt = defaultdict(list)
    for r in rows:
        by_prompt[r["prompt_id"]].append(r)

    surface_a_hits = 0
    surface_b_hits = 0
    surface_a_total = 0
    surface_b_total = 0

    for pid, rs in by_prompt.items():
        print(f"--- {pid} ---")
        for r in rs:
            if "error" in r:
                print(f"  [{r['surface']}] ERROR: {r['error']}")
                continue
            names = [tc["name"] for tc in r["tool_calls"]]
            picked_str = ", ".join(names) if names else "(no tool call)"
            expected_str = " | ".join(r["expected_any"])
            mark = "PASS" if r["hit"] else "MISS"
            print(f"  [{r['surface']}] {mark}: picked [{picked_str}]  (expected any of: {expected_str})")
            if not r["tool_calls"] and r["no_tool_response_excerpt"]:
                print(f"    no-tool reply excerpt: {r['no_tool_response_excerpt'][:150]!r}")

            if r["surface"] == "A_namespaced":
                surface_a_total += 1
                if r["hit"]: surface_a_hits += 1
            else:
                surface_b_total += 1
                if r["hit"]: surface_b_hits += 1
        print()

    print(f"=== Routing accuracy ===")
    print(f"  Surface A (namespaced):  {surface_a_hits}/{surface_a_total}")
    print(f"  Surface B (primitive):   {surface_b_hits}/{surface_b_total}")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "gpt-5"
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set")
        sys.exit(1)
    report = run_probe(model=model)
    out_path = "/home/bch/dev/mcpfleet/uk-legal-mcp/probes/probe_c_results.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print_report(report)
    print(f"\nFull JSON written to: {out_path}")
