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

That fix used whole-word-phrase containment plus most-specific-wins
selection, returned no rate at all when no confident match existed, and split
"last verified" (`verified_on`, always populated) from "known legal
commencement date" (`effective_from`, populated only where evidenced, usually
None).

The qualification re-audit (2026-09-13) found containment itself unsafe: a
qualifier outside the table vanished into the broader category. "pet food"
matched "food" and came back zero-rated (GOV.UK: packaged pet food is
standard-rated). Two entries were also wrong at source: "funeral" said burial
and cremation are zero-rated (VAT Notice 701/32: exempt, while flowers,
headstones and animal cremation are standard-rated), and "medicine" said
certain over-the-counter medicines are zero-rated (VAT Notice 701/57: only
qualifying goods dispensed on prescription under set conditions; medicines
sold over the counter are standard-rated). Matching is now exact category
names or explicit aliases, the broad "funeral" and "medicine" categories are
replaced by source-worded ones, and "pet food" is its own entry. These tests
assert the actual failure modes, not just that a result object comes back.
"""

import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway
from src.modules.hmrc.tools import _VAT_ALIASES, _VAT_LOOKUP, _lookup_vat, _normalise


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

    def test_category_inside_a_longer_query_is_not_matched(self):
        """Exact matching: extra words are never silently discarded, even when
        (as here) the broader category would happen to give the right answer."""
        result = _lookup_vat("hot food from a takeaway van")
        assert result.matched_category is None
        assert result.rate is None

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

    def test_every_category_and_alias_is_reachable_exactly(self):
        """Keys and aliases are stored normalised, so each one can be matched,
        and every alias points at a real category."""
        for key in _VAT_LOOKUP:
            assert _normalise(key) == key, key
            assert _lookup_vat(key).matched_category == key
        for alias, key in _VAT_ALIASES.items():
            assert _normalise(alias) == alias, alias
            assert key in _VAT_LOOKUP, alias
            assert _lookup_vat(alias).matched_category == key

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
        assert result.verified_on is None
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

    def test_unmatched_notes_list_the_category_names(self):
        """The retry path for an exact matcher: the names come from the table itself."""
        result = _lookup_vat("pet food supplies")
        for key in _VAT_LOOKUP:
            assert key in result.notes


class TestQualifiersAreNeverDiscarded:
    """A category phrase inside a qualified query must not resolve to that category.

    Each query below contains a table category plus words the table does not
    model; the qualifier can change the VAT treatment, so the result must be
    unresolved rather than the broader category's rate.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "baby food",
            "animal feed food",
            "hot food takeaway",
            "animal cremation",
            "over the counter medicine",
            "animal medicine",
            "prescription medicine",
            "funeral services",
            "funeral flowers",
            "pet insurance",
            "land with buildings",
            "domestic fuel oil",
            "solar panels for a business",
        ],
    )
    def test_qualified_query_is_unresolved(self, query):
        result = _lookup_vat(query)
        assert (result.matched_category, result.rate, result.rate_percentage) == (None, None, None), query

    @pytest.mark.parametrize("query", ["funeral", "burial", "cremation", "medicine", "medicines"])
    def test_broad_terms_with_mixed_treatment_are_unresolved(self, query):
        """GOV.UK gives these terms more than one treatment (exempt disposal of
        the dead vs standard-rated flowers, headstones and animal cremation;
        zero-rated dispensed prescriptions vs standard-rated over-the-counter
        medicines), so no single rate is returned."""
        result = _lookup_vat(query)
        assert (result.matched_category, result.rate) == (None, None), query

    @pytest.mark.parametrize(
        "query", ["Hot  Food", "SOLAR PANELS", "energy-saving materials", "children\u2019s clothing"]
    )
    def test_normalisation_is_limited_to_case_spacing_hyphens_and_apostrophes(self, query):
        assert _lookup_vat(query).matched_category is not None, query


class TestSourceCorrections:
    """Entries corrected against GOV.UK in the 2026-09-13 re-audit."""

    def test_pet_food_is_standard_rated(self):
        result = _lookup_vat("pet food")
        assert (result.matched_category, result.rate, result.rate_percentage) == ("pet food", "standard", 20.0)
        assert result.verified_on.isoformat() == "2026-09-13"
        assert result.source_url == "https://www.gov.uk/guidance/vat-rates-on-different-goods-and-services"

    @pytest.mark.parametrize(
        "query", ["burial or cremation of the dead", "burial or cremation of dead people", "burial at sea"]
    )
    def test_burial_or_cremation_of_the_dead_is_exempt(self, query):
        result = _lookup_vat(query)
        assert (result.matched_category, result.rate, result.rate_percentage) == ("burial or cremation of the dead", "exempt", None)
        assert "701/32" in result.notes
        assert "animals are standard-rated" in result.notes
        assert result.source_url.endswith("burial-cremation-and-commemoration-of-the-dead-notice-70132")
        assert result.verified_on.isoformat() == "2026-09-13"

    @pytest.mark.parametrize(
        "query",
        [
            "prescriptions dispensed by a registered pharmacist",
            "dispensing of prescriptions by a registered pharmacist",
            "dispensed prescriptions",
        ],
    )
    def test_dispensed_prescriptions_are_zero_rated_with_conditions(self, query):
        result = _lookup_vat(query)
        assert (result.matched_category, result.rate) == ("prescriptions dispensed by a registered pharmacist", "zero")
        assert "over the counter are a separate, standard-rated supply" in result.notes
        assert result.source_url.endswith("health-professionals-pharmaceutical-products-and-vat-notice-70157")
        assert result.verified_on.isoformat() == "2026-09-13"

    def test_corrected_entries_do_not_mark_unrelated_rows_reverified(self):
        new = {"pet food", "burial or cremation of the dead", "prescriptions dispensed by a registered pharmacist"}
        earlier = {"food", "hot food", "energy saving materials", "solar panels"}
        dates = {k: e.verified_on.isoformat() for k, e in _VAT_LOOKUP.items()}
        assert {k for k, d in dates.items() if d == "2026-09-13"} == new
        assert {k for k, d in dates.items() if d == "2026-09-12"} == earlier


class TestRatePercentageSemantics:
    """Zero-rated, exempt and unresolved must stay structurally distinguishable.

    An exempt supply is not taxed at 0%: exemption and zero rating have different
    consequences (e.g. for input tax recovery), so exempt carries no percentage.
    """

    def test_table_invariant_by_treatment(self):
        for key, entry in _VAT_LOOKUP.items():
            if entry.rate == "exempt":
                assert entry.percentage is None, key
            elif entry.rate == "zero":
                assert entry.percentage == 0.0, key
            else:
                assert entry.rate in ("standard", "reduced"), key
                assert entry.percentage is not None and entry.percentage > 0, key

    def test_zero_exempt_and_unresolved_differ(self):
        zero = _lookup_vat("food")
        exempt = _lookup_vat("financial services")
        unresolved = _lookup_vat("quantum widgets")
        assert (zero.rate, zero.rate_percentage) == ("zero", 0.0)
        assert (exempt.rate, exempt.rate_percentage) == ("exempt", None)
        assert (unresolved.matched_category, unresolved.rate, unresolved.rate_percentage) == (None, None, None)
        assert exempt.matched_category is not None and exempt.verified_on is not None
        assert unresolved.verified_on is None

    @pytest.mark.asyncio
    async def test_zero_exempt_and_unresolved_differ_via_mcp_tool(self, client: Client):
        async def call(q):
            result = await client.call_tool("hmrc_get_vat_rate", {"commodity_code": q})
            assert not result.is_error
            d = result.structured_content
            return d["matched_category"], d["rate"], d["rate_percentage"], d["verified_on"]

        assert await call("dispensed prescriptions") == ("prescriptions dispensed by a registered pharmacist", "zero", 0.0, "2026-09-13")
        assert await call("burial at sea") == ("burial or cremation of the dead", "exempt", None, "2026-09-13")
        assert await call("insurance") == ("insurance", "exempt", None, "2023-11-22")
        assert await call("underwater basket weaving lessons") == (None, None, None, None)


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
    @pytest.mark.parametrize(
        "query, expected",
        [
            ("pet food", ("pet food", "standard", 20.0)),
            ("burial or cremation of the dead", ("burial or cremation of the dead", "exempt", None)),
            ("dispensed prescriptions", ("prescriptions dispensed by a registered pharmacist", "zero", 0.0)),
            ("funeral", (None, None, None)),
            ("cremation", (None, None, None)),
            ("medicine", (None, None, None)),
            ("animal medicine", (None, None, None)),
            ("baby food", (None, None, None)),
        ],
    )
    async def test_audited_and_qualifier_cases_via_mcp_tool(self, client: Client, query, expected):
        result = await client.call_tool("hmrc_get_vat_rate", {"commodity_code": query})
        assert not result.is_error
        data = result.structured_content
        assert (data["matched_category"], data["rate"], data["rate_percentage"]) == expected

    @pytest.mark.asyncio
    async def test_unmatched_query_via_mcp_tool_has_no_rate(self, client: Client):
        result = await client.call_tool("hmrc_get_vat_rate", {"commodity_code": "artisanal candle making kits"})
        assert not result.is_error
        assert result.data.matched_category is None
        assert result.data.rate is None
        assert result.data.rate_percentage is None
        assert result.data.verified_on is None

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
        # verified_on is null for an unresolved query, which has no matched entry.
        assert allows_null(props["verified_on"]), props["verified_on"]
