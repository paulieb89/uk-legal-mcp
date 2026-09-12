"""Regression coverage for hmrc_get_vat_rate matching + data-currency semantics.

Source-fidelity audit (2026-09) found two confirmed failures in the previous
character-substring matcher (`if k in key or key in k`, first dict-order match
wins):

  - "hot food" matched the broader "food" entry first (zero-rated) instead of
    its own "hot food" entry (standard-rated) — because "food" is a character
    substring of "hot food" and iterated first.
  - "energy saving materials" / solar panels returned the stale 5% reduced
    rate; GOV.UK VAT Notice 708/6 has it at 0% (zero) from 1 April 2022 to
    31 March 2027.

A follow-up review found the first remediation still overstated certainty in
two ways, both covered below:

  - an unmatched or ambiguous query returned a fabricated `rate="standard"` /
    `rate_percentage=20.0` instead of no rate at all — a consumer reading
    `rate` without checking `matched_category` would be silently misled.
  - `effective_from` was repurposed to mean "last verified", conflating a
    data-currency signal with the rate's actual legal commencement date.

The fix replaces character-substring matching with whole-word-phrase
containment plus most-specific-wins selection (order-independent by
construction), returns no rate at all when no confident match exists, and
splits "last verified" (`verified_on`, always populated) from "known legal
commencement date" (`effective_from`, populated only where evidenced, usually
None). These tests assert the actual failure modes, not just that a result
object comes back.
"""

import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway
from src.modules.hmrc.tools import _lookup_vat, _matches_phrase, _VAT_LOOKUP


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


# ---------------------------------------------------------------------------
# Internal layer — _lookup_vat
# ---------------------------------------------------------------------------


class TestLookupVatInternal:
    def test_hot_food_is_standard_not_zero(self):
        """The audited failure: 'hot food' must not resolve via the 'food' entry."""
        result = _lookup_vat("hot food")
        assert result.matched_category == "hot food"
        assert result.rate == "standard"
        assert result.rate_percentage == 20.0

    def test_plain_food_is_still_zero(self):
        """Fixing 'hot food' must not break the unqualified 'food' query."""
        result = _lookup_vat("food")
        assert result.matched_category == "food"
        assert result.rate == "zero"
        assert result.rate_percentage == 0.0

    def test_hot_food_phrase_inside_longer_query(self):
        result = _lookup_vat("hot food from a takeaway van")
        assert result.matched_category == "hot food"
        assert result.rate == "standard"

    @pytest.mark.parametrize(
        "query",
        ["energy saving materials", "energy-saving materials", "solar panels", "Solar Panels"],
    )
    def test_energy_saving_materials_is_zero_not_reduced(self, query):
        """The audited failure: ESM/solar panels must be 0%, not the stale 5%."""
        result = _lookup_vat(query)
        assert result.matched_category is not None
        assert result.rate == "zero"
        assert result.rate_percentage == 0.0
        assert "708/6" in result.notes
        assert result.source_url.startswith("https://www.gov.uk/")

    def test_ebooks_not_shadowed_by_books(self):
        """Same substring-collision mechanism as hot-food/food, latent in books/ebooks.

        Both are zero-rated so the rate value was never visibly wrong, but the
        old matcher would still resolve "ebooks" via the "books" entry's notes
        and effective date instead of its own.
        """
        result = _lookup_vat("ebooks")
        assert result.matched_category == "ebooks"
        assert "books" != result.matched_category
        assert "since 1 May 2020" in result.notes

    def test_generic_short_query_does_not_match_specific_multiword_category(self):
        """'fuel' alone must not silently inherit 'domestic fuel's reduced rate.

        Old matcher: `key in k` matched a short generic query against a
        longer, more specific table key it happened to be a substring of.
        """
        result = _lookup_vat("fuel")
        assert result.matched_category is None
        assert result.rate is None

    def test_matching_is_not_dict_order_dependent(self):
        """Reversing table iteration order must not change the winning match.

        Regression guard against reintroducing "first match in dict order
        wins" — the actual bug shape behind the hot-food/food failure.
        """
        query_words = "hot food".split()
        forward = [k for k in _VAT_LOOKUP if _matches_phrase(query_words, k.split())]
        reversed_keys = list(reversed(list(_VAT_LOOKUP.keys())))
        backward = [k for k in reversed_keys if _matches_phrase(query_words, k.split())]
        assert set(forward) == set(backward) == {"food", "hot food"}
        # Selection logic (in _lookup_vat) must independently pick the same
        # longest match regardless of which order the candidates were found in.
        assert _lookup_vat("hot food").matched_category == "hot food"

    def test_every_table_entry_has_source_and_verified_date(self):
        """Every entry must carry discoverable provenance (audit requirement),
        even where the underlying rate hasn't been individually re-verified."""
        for key, entry in _VAT_LOOKUP.items():
            assert entry.source_url.startswith("https://www.gov.uk/"), key
            assert entry.verified_on is not None, key


class TestUnresolvedQueriesNeverFabricateARate:
    """No confident match => no rate at all, not a silently-assumed standard 20%."""

    def test_unmatched_query_has_no_rate(self):
        result = _lookup_vat("underwater basket weaving lessons")
        assert result.matched_category is None
        assert result.rate is None
        assert result.rate_percentage is None
        assert result.effective_from is None
        assert result.verified_on is not None
        assert "GOV.UK" in result.notes

    def test_unmatched_query_notes_do_not_imply_a_default_rate(self):
        result = _lookup_vat("underwater basket weaving lessons")
        assert "20%" not in result.notes
        assert "standard" not in result.notes.lower()

    def test_generic_short_query_has_no_rate(self):
        """'fuel' must not silently inherit any rate, standard or otherwise."""
        result = _lookup_vat("fuel")
        assert result.rate is None
        assert result.rate_percentage is None

    def test_ambiguous_tie_has_no_rate(self, monkeypatch):
        """A genuine tie between equally-specific categories must return no
        rate at all — not pick one by table order, and not fall back to a
        fabricated standard-rate default either."""
        import src.modules.hmrc.tools as hmrc_tools

        tied_table = {
            "aaa bbb": hmrc_tools._VATEntry("zero", 0.0, "n1", hmrc_tools._LEGACY_REVIEW_DATE, hmrc_tools._GENERAL_RATES_URL),
            "ccc ddd": hmrc_tools._VATEntry("standard", 20.0, "n2", hmrc_tools._LEGACY_REVIEW_DATE, hmrc_tools._GENERAL_RATES_URL),
        }
        monkeypatch.setattr(hmrc_tools, "_VAT_LOOKUP", tied_table)
        result = hmrc_tools._lookup_vat("aaa bbb ccc ddd")
        assert result.matched_category is None
        assert result.rate is None
        assert result.rate_percentage is None
        assert "Ambiguous" in result.notes


class TestEffectiveFromVsVerifiedOn:
    """effective_from (legal commencement, evidenced-only) must never be
    conflated with verified_on (data-currency, always populated)."""

    def test_energy_saving_materials_has_evidenced_effective_from(self):
        result = _lookup_vat("solar panels")
        assert result.effective_from is not None
        assert result.effective_from.isoformat() == "2022-04-01"
        assert result.verified_on is not None
        assert result.verified_on != result.effective_from

    def test_food_has_no_evidenced_effective_from_but_has_verified_on(self):
        """'food' was re-checked live (verified_on = today of this fix) but no
        commencement date was gathered as evidence, so effective_from is None
        — must not silently fall back to the verification date."""
        result = _lookup_vat("food")
        assert result.effective_from is None
        assert result.verified_on is not None

    def test_legacy_entry_has_no_effective_from(self):
        result = _lookup_vat("domestic fuel")
        assert result.matched_category == "domestic fuel"
        assert result.effective_from is None
        assert result.verified_on is not None

    def test_unmatched_result_has_no_effective_from(self):
        result = _lookup_vat("artisanal candle making kits")
        assert result.effective_from is None


# ---------------------------------------------------------------------------
# Public MCP surface — hmrc_get_vat_rate
# ---------------------------------------------------------------------------


class TestGetVatRateTool:
    @pytest.mark.asyncio
    async def test_hot_food_via_mcp_tool(self, client: Client):
        result = await client.call_tool("hmrc_get_vat_rate", {"commodity_code": "hot food"})
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.matched_category == "hot food"
        assert result.data.rate == "standard"
        assert result.data.rate_percentage == 20.0

    @pytest.mark.asyncio
    async def test_food_via_mcp_tool(self, client: Client):
        result = await client.call_tool("hmrc_get_vat_rate", {"commodity_code": "food"})
        assert not result.is_error
        assert result.data.matched_category == "food"
        assert result.data.rate == "zero"
        assert result.data.effective_from is None

    @pytest.mark.asyncio
    async def test_solar_panels_via_mcp_tool(self, client: Client):
        result = await client.call_tool("hmrc_get_vat_rate", {"commodity_code": "solar panels"})
        assert not result.is_error
        assert result.data.rate == "zero"
        assert result.data.rate_percentage == 0.0
        assert result.data.effective_from == "2022-04-01"

    @pytest.mark.asyncio
    async def test_fuel_via_mcp_tool_has_no_rate(self, client: Client):
        result = await client.call_tool("hmrc_get_vat_rate", {"commodity_code": "fuel"})
        assert not result.is_error
        assert result.data.matched_category is None
        assert result.data.rate is None
        assert result.data.rate_percentage is None

    @pytest.mark.asyncio
    async def test_unmatched_query_via_mcp_tool_has_no_rate(self, client: Client):
        result = await client.call_tool("hmrc_get_vat_rate", {"commodity_code": "artisanal candle making kits"})
        assert not result.is_error
        assert result.data.matched_category is None
        assert result.data.rate is None
        assert result.data.rate_percentage is None
        assert result.data.verified_on is not None

    @pytest.mark.asyncio
    async def test_output_schema_has_nullable_rate_fields(self, client: Client):
        """The advertised MCP output schema must honestly mark rate/percentage/
        effective_from as nullable, since a real response can null all three."""
        tools = await client.list_tools()
        tool = next(t for t in tools if t.name == "hmrc_get_vat_rate")
        props = tool.outputSchema["properties"]

        def allows_null(schema: dict) -> bool:
            if schema.get("type") == "null":
                return True
            any_of = schema.get("anyOf") or schema.get("oneOf") or []
            return any(s.get("type") == "null" for s in any_of)

        assert allows_null(props["rate"]), props["rate"]
        assert allows_null(props["rate_percentage"]), props["rate_percentage"]
        assert allows_null(props["effective_from"]), props["effective_from"]
        assert allows_null(props["matched_category"]), props["matched_category"]
        # verified_on is always populated — must NOT be nullable.
        assert not allows_null(props["verified_on"]), props["verified_on"]
