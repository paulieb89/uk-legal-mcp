"""
Tools for the parliament module.

Upstream: UK Parliament Members API + Hansard API
Wire format: JSON
"""

import json
from datetime import date

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from ...deps import format_http_error
from .models import HansardContribution, MemberResult, PolicyVibeResult

HANSARD_BASE = "https://hansard.parliament.uk"
MEMBERS_BASE = "https://members-api.parliament.uk/api"


class HansardSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description="Topic or keyword to search in Hansard, e.g. 'artificial intelligence regulation'", min_length=1, max_length=500)
    from_date: date | None = Field(None, description="Start date (YYYY-MM-DD)")
    to_date: date | None = Field(None, description="End date (YYYY-MM-DD)")
    member: str | None = Field(None, description="Filter by member name")


class PolicyVibeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    policy_text: str = Field(..., description="Description of the policy proposal to assess", min_length=10, max_length=2000)
    topic: str = Field(..., description="Short topic keyword for Hansard search, e.g. 'AI safety'", min_length=2, max_length=200)


class FindMemberInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Name or partial name, e.g. 'Starmer', 'Baroness Hale'", min_length=2, max_length=200)


class MemberDebatesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    member_id: int = Field(..., description="Parliament Members API integer ID. Obtain from parliament_find_member.", ge=1)
    topic: str | None = Field(None, description="Optional topic filter")


def _parse_hansard_contributions(data: dict) -> list[HansardContribution]:
    contributions = []
    for item in data.get("Results", data.get("results", [])):
        try:
            contributions.append(HansardContribution(
                member_name=item.get("MemberName", item.get("memberName", "Unknown")),
                party=item.get("Party", item.get("party")),
                constituency=item.get("Constituency", item.get("constituency")),
                date=date.fromisoformat(item.get("SittingDate", item.get("date", "1970-01-01"))[:10]),
                debate_title=item.get("DebateSection", item.get("debateTitle", "Unknown debate")),
                section=item.get("Section", item.get("section", "Unknown")),
                text=item.get("Value", item.get("text", ""))[:3000],
                url=item.get("Url", item.get("url", "")),
            ))
        except Exception:
            continue
    return contributions


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search_hansard",
        annotations={"title": "Search Hansard Debates", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_search_hansard(params: HansardSearchInput, ctx: Context) -> str:
        """Search Hansard for parliamentary debates, questions, and speeches.

        Returns contributions from MPs and Lords including date, party, debate title,
        and full text. Useful for understanding legislative intent or political context.

        Args:
            params (HansardSearchInput): query, optional date range, optional member filter.

        Returns:
            str: JSON array of contributions (member_name, party, constituency,
                date, debate_title, section, text, url).
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            qp: dict = {"queryParameters.searchTerm": params.query, "queryParameters.take": 20}
            if params.from_date: qp["queryParameters.startDate"] = params.from_date.isoformat()
            if params.to_date: qp["queryParameters.endDate"] = params.to_date.isoformat()
            if params.member: qp["queryParameters.memberId"] = params.member
            resp = await client.get(f"{HANSARD_BASE}/search/contributions.json", params=qp)
            resp.raise_for_status()
            contributions = _parse_hansard_contributions(resp.json())
            return json.dumps([c.model_dump(mode="json") for c in contributions], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="vibe_check",
        annotations={"title": "Parliamentary Policy Vibe Check", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def parliament_vibe_check(params: PolicyVibeInput, ctx: Context) -> str:
        """Assess the likely parliamentary reception of a policy proposal.

        Searches Hansard for relevant debates, then uses LLM sampling to classify
        sentiment and extract supporters, opponents, and key concerns.
        Returns raw contributions alongside the AI-generated summary for verification.

        Degrades gracefully if sampling is unavailable — returns contributions only.

        Args:
            params (PolicyVibeInput): policy_text (full description), topic (search keyword).

        Returns:
            str: JSON PolicyVibeResult with contributions, sentiment_summary,
                key_supporters, key_opponents, key_concerns.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            resp = await client.get(
                f"{HANSARD_BASE}/search/contributions.json",
                params={"queryParameters.searchTerm": params.topic, "queryParameters.take": 15},
            )
            resp.raise_for_status()
            contributions = _parse_hansard_contributions(resp.json())

            if not contributions:
                return PolicyVibeResult(
                    query=params.topic, contributions=[],
                    sentiment_summary="No Hansard contributions found for this topic.",
                    key_supporters=[], key_opponents=[], key_concerns=[],
                ).model_dump_json(indent=2)

            contributions_text = "\n\n".join(
                f"{c.member_name} ({c.party or 'Unknown'}, {c.date}):\n{c.text[:500]}"
                for c in contributions[:10]
            )
            sample_prompt = (
                f"Policy proposal: {params.policy_text}\n\n"
                f"Relevant Hansard contributions:\n{contributions_text}\n\n"
                f"Respond ONLY with a JSON object (no markdown fences):\n"
                '"sentiment_summary": "...", "key_supporters": [...], '
                '"key_opponents": [...], "key_concerns": [...]}'
            )

            sentiment_summary = None
            key_supporters: list[str] = []
            key_opponents: list[str] = []
            key_concerns: list[str] = []

            try:
                result = await ctx.sample(sample_prompt, result_type=str)
                raw = result.text.strip().lstrip("```json").lstrip("```").rstrip("```")
                parsed = json.loads(raw)
                sentiment_summary = parsed.get("sentiment_summary")
                key_supporters = parsed.get("key_supporters", [])
                key_opponents = parsed.get("key_opponents", [])
                key_concerns = parsed.get("key_concerns", [])
            except Exception:
                sentiment_summary = "Sentiment analysis unavailable (sampling not supported by this client)."

            return PolicyVibeResult(
                query=params.topic, contributions=contributions,
                sentiment_summary=sentiment_summary, key_supporters=key_supporters,
                key_opponents=key_opponents, key_concerns=key_concerns,
            ).model_dump_json(indent=2)

        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="find_member",
        annotations={"title": "Find Member of Parliament", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_find_member(params: FindMemberInput, ctx: Context) -> str:
        """Search for a current or former MP or Lord by name.

        Returns member ID, party, constituency, house, and current status.
        Use the integer member_id with parliament_member_debates.

        Args:
            params (FindMemberInput): name — full or partial member name.

        Returns:
            str: JSON array of MemberResult objects (id, name, party, constituency, house, is_current).
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            resp = await client.get(f"{MEMBERS_BASE}/Members/Search", params={"Name": params.name, "IsCurrentMember": "false"})
            resp.raise_for_status()
            data = resp.json()
            members = []
            for item in data.get("items", []):
                v = item.get("value", item)
                house_id = v.get("latestHouseMembership", {}).get("house", 1)
                members.append(MemberResult(
                    id=v.get("id", 0), name=v.get("nameDisplayAs", "Unknown"),
                    party=v.get("latestParty", {}).get("name", "Unknown"),
                    constituency=v.get("latestHouseMembership", {}).get("membershipFrom"),
                    house="Commons" if house_id == 1 else "Lords",
                    is_current=v.get("latestHouseMembership", {}).get("membershipStatus", {}).get("statusIsActive", False),
                ))
            return json.dumps([m.model_dump() for m in members], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="member_debates",
        annotations={"title": "Get Member Debates", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_member_debates(params: MemberDebatesInput, ctx: Context) -> str:
        """Retrieve Hansard contributions by a specific member ID, optionally filtered by topic.

        Use parliament_find_member first to obtain the integer member ID.

        Args:
            params (MemberDebatesInput): member_id (int), optional topic filter.

        Returns:
            str: JSON array of HansardContribution objects.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            qp: dict = {"queryParameters.memberId": params.member_id, "queryParameters.take": 20}
            if params.topic: qp["queryParameters.searchTerm"] = params.topic
            resp = await client.get(f"{HANSARD_BASE}/search/contributions.json", params=qp)
            resp.raise_for_status()
            contributions = _parse_hansard_contributions(resp.json())
            return json.dumps([c.model_dump(mode="json") for c in contributions], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})
