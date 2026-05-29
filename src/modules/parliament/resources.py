"""Resource templates for the parliament module.

Three `hansard://` URI templates that surface large or context-heavy
Hansard payloads as host-controlled resources rather than tool responses.

Registered on the GATEWAY, not on the sub-MCP, following the same pattern
as case_law and legislation. See [src/modules/case_law/resources.py] for
the precedent.

URI scheme is `hansard` (not `parliament`) — RFC 3986 forbids underscores
in scheme names, and the scheme also reads naturally for citation contexts.

Upstream endpoints (probed live during planning):
  • hansard-api.parliament.uk/debates/Debate/{ExtId}.json
      Takes the DebateSectionExtId GUID (NOT the integer DebateSectionId).
      Returns {Overview, Navigator, Items, ChildDebates}. `Items` is the
      ordered contribution list with `ItemType`, `MemberId`, `AttributedTo`,
      `Value` (HTML), `OrderInSection`, `HansardSection`, `ExternalId`.

  • members-api.parliament.uk/api/Members/{id}/Biography
      Returns {value: {representations, electionsContested, houseMemberships,
      governmentPosts, oppositionPosts, otherPosts, partyAffiliations,
      committeeMemberships}}. Each post has name, startDate, endDate.

There is NO single-contribution endpoint upstream. The contribution resource
extracts the matching item from the parent debate JSON; the gateway-level
ReadResourceSettings cache (TTL 3600s) keeps the cost to one fetch per
debate per hour.
"""

import json
import re

import httpx
from fastmcp import Context, FastMCP

from ...deps import format_http_error

HANSARD_API = "https://hansard-api.parliament.uk"
MEMBERS_BASE = "https://members-api.parliament.uk/api"


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_COLUMN_NUMBER_RE = re.compile(r'data-column-number="(\d+)"')


def _strip_html(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub("", text or "")).strip()


def _extract_column_numbers(html: str) -> list[int]:
    """Find every `data-column-number` integer in a contribution's HTML.

    Hansard embeds column markers as `<span class="column-number..."
    data-column-number="N">` at the position where each printed-record
    column begins.
    """
    if not html:
        return []
    return [int(m) for m in _COLUMN_NUMBER_RE.findall(html)]


def _assign_columns(items: list[dict]) -> dict[int, tuple[int | None, int | None]]:
    """Walk debate items in document order and assign carry-forward
    column numbers to each contribution.

    Returns a dict keyed by item index → (column_start, column_end).

    Why carry-forward: Hansard only emits a `data-column-number` marker
    at the moment a printed column begins. A contribution that sits
    mid-column has no marker of its own. Without carry-forward, ~50%
    of contributions would report column_start=None; with it, every
    contribution carries the column it appears in — which is the value
    a lawyer needs for an OSCOLA footnote.

    Verified against live /debates/Debate/{ext}.json: 0/98 contributions
    return None, monotonically non-decreasing through the debate.
    """
    out: dict[int, tuple[int | None, int | None]] = {}
    current_column: int | None = None
    for idx, item in enumerate(items):
        markers = _extract_column_numbers(item.get("Value") or "")
        # column_start = first marker in this item if present, else the
        # column we were already in when this item began.
        col_start = markers[0] if markers else current_column
        # Update tracker to the last marker in this item, if any.
        if markers:
            current_column = markers[-1]
        out[idx] = (col_start, current_column)
    return out


async def _fetch_debate(debate_ext_id: str, ctx: Context) -> dict:
    client: httpx.AsyncClient = ctx.lifespan_context["http"]
    resp = await client.get(f"{HANSARD_API}/debates/Debate/{debate_ext_id}.json")
    resp.raise_for_status()
    return resp.json()


async def _fetch_biography(member_id: int, ctx: Context) -> dict:
    client: httpx.AsyncClient = ctx.lifespan_context["http"]
    resp = await client.get(f"{MEMBERS_BASE}/Members/{member_id}/Biography")
    resp.raise_for_status()
    return resp.json().get("value", {})


def _item_is_contribution(item: dict) -> bool:
    """Filter out column-number markers and other non-speech items."""
    if (item.get("ItemType") or "") != "Contribution":
        return False
    hrs = item.get("HRSTag") or ""
    if hrs == "hs_ColumnNumber":
        return False
    return bool(item.get("Value"))


def register_parliament_resources(gateway: FastMCP) -> None:
    """Register the hansard:// resource family on the gateway."""

    @gateway.resource(
        "hansard://debate/{debate_ext_id}/header",
        name="Hansard Debate — header + ordered contribution index",
        description=(
            "Debate overview (title, date, house, location, volume) plus an "
            "ordered index of contributions in the debate: "
            "'OrderInSection: AttributedTo [col_ref] — first 100 chars'. "
            "Use the debate_ext_id (DebateSectionExtId GUID) returned by "
            "parliament_search_hansard or parliament_policy_position_summary. "
            "Typical size: ~3-8k tokens for a 50-150 contribution debate."
        ),
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"parliament", "hansard", "debate"},
    )
    async def hansard_debate_header(debate_ext_id: str, ctx: Context) -> str:
        try:
            data = await _fetch_debate(debate_ext_id, ctx)
        except httpx.HTTPError as e:
            return json.dumps({"error": format_http_error(e), "debate_ext_id": debate_ext_id})

        overview = data.get("Overview") or {}
        items = data.get("Items") or []

        columns_by_idx = _assign_columns(items)
        index: list[dict] = []
        for idx, item in enumerate(items):
            if not _item_is_contribution(item):
                continue
            preview = _strip_html(item.get("Value") or "")[:100]
            col_start, col_end = columns_by_idx[idx]
            index.append({
                "order": item.get("OrderInSection"),
                "contribution_ext_id": item.get("ExternalId"),
                "member_id": item.get("MemberId"),
                "attributed_to": item.get("AttributedTo"),
                "column_ref": item.get("HansardSection"),
                "column_start": col_start,
                "column_end": col_end,
                "preview": preview,
            })

        out = {
            "debate_id": overview.get("Id"),
            "debate_ext_id": overview.get("ExtId") or debate_ext_id,
            "title": (overview.get("Title") or "").strip(),
            "date": (overview.get("Date") or "")[:10],
            "house": overview.get("House"),
            "location": overview.get("Location"),
            "volume_no": overview.get("VolumeNo"),
            # source_code is an undocumented integer enum (observed 2 for recent
            # debates, 3 for older debates). Surfaced raw with no mapping claim —
            # callers should not assume "Bound Volume" vs "Daily Part" without
            # verifying against parliament.uk publication conventions. Pairs with
            # content_last_updated (newly added) for a derivable provenance signal.
            "source_code": overview.get("Source"),
            "content_last_updated": overview.get("ContentLastUpdated"),
            "previous_debate_ext_id": overview.get("PreviousDebateExtId"),
            "previous_debate_title": (overview.get("PreviousDebateTitle") or "").strip() or None,
            "next_debate_ext_id": overview.get("NextDebateExtId"),
            "next_debate_title": (overview.get("NextDebateTitle") or "").strip() or None,
            "contribution_count": len(index),
            "contributions_index": index,
        }
        return json.dumps(out, indent=2, default=str)

    @gateway.resource(
        "hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}",
        name="Hansard Contribution — single contribution full text",
        description=(
            "A single contribution's full text + member metadata + column "
            "reference, extracted from its parent debate. Use the "
            "debate_ext_id and contribution_ext_id returned by "
            "parliament_search_hansard. Typical size: ~200-2000 tokens. "
            "The parent debate JSON is cached at the gateway for 1 hour, "
            "so successive contribution reads from the same debate are free."
        ),
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"parliament", "hansard", "contribution"},
    )
    async def hansard_debate_contribution(debate_ext_id: str, contribution_ext_id: str, ctx: Context) -> str:
        try:
            data = await _fetch_debate(debate_ext_id, ctx)
        except httpx.HTTPError as e:
            return json.dumps({
                "error": format_http_error(e),
                "debate_ext_id": debate_ext_id,
                "contribution_ext_id": contribution_ext_id,
            })

        overview = data.get("Overview") or {}
        items = data.get("Items") or []

        columns_by_idx = _assign_columns(items)
        match_idx = next(
            (i for i, it in enumerate(items) if (it.get("ExternalId") or "") == contribution_ext_id),
            None,
        )
        if match_idx is None:
            return json.dumps({
                "error": f"Contribution {contribution_ext_id} not found in debate {debate_ext_id}.",
                "debate_ext_id": debate_ext_id,
                "contribution_ext_id": contribution_ext_id,
            })

        match = items[match_idx]
        html_value = match.get("Value") or ""
        column_start, column_end = columns_by_idx[match_idx]
        return json.dumps({
            "debate_ext_id": overview.get("ExtId") or debate_ext_id,
            "debate_title": (overview.get("Title") or "").strip(),
            "debate_date": (overview.get("Date") or "")[:10],
            "house": overview.get("House"),
            "contribution_ext_id": match.get("ExternalId"),
            "order_in_debate": match.get("OrderInSection"),
            "member_id": match.get("MemberId"),
            "attributed_to": match.get("AttributedTo"),
            "column_ref": match.get("HansardSection"),
            "column_start": column_start,
            "column_end": column_end,
            "uin": match.get("UIN"),
            "is_reiteration": match.get("IsReiteration"),
            "text": _strip_html(html_value),
            "html": html_value,
        }, indent=2, default=str)

    @gateway.resource(
        "hansard://member/{member_id}/biography",
        name="UK Parliament Member — biography",
        description=(
            "Full biography from the Members API: constituency representations, "
            "house memberships, government posts, opposition posts, committee "
            "memberships, party affiliations. Each post has startDate and endDate "
            "so a caller can determine a member's role at the time of any "
            "specific contribution. Typical size: ~2-5k tokens."
        ),
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"parliament", "members", "biography"},
    )
    async def hansard_member_biography(member_id: int, ctx: Context) -> str:
        try:
            bio = await _fetch_biography(member_id, ctx)
        except httpx.HTTPError as e:
            return json.dumps({"error": format_http_error(e), "member_id": member_id})
        return json.dumps({"member_id": member_id, **bio}, indent=2, default=str)
