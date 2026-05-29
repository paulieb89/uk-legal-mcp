"""Unit tests for Hansard column-number extraction in parliament resources.

Regression coverage for the cross-reference hoisting bug: contributions that
cite columns in *other* debates/houses embed cross-reference spans with
data-column-number attributes. The old broad regex included those, producing
impossible spans (column_start=637, column_end=200). The fix targets only
<span class="column-number..."> elements.

Verified HTML patterns sourced from live debates (May 2026):
  - Real marker:       <span id="200" class="column-number" data-column-number="200">
  - Hidden marker:     <span id="207" class="column-number column-hidden" data-column-number="207">
  - Column-only:       <span id="197" class="column-number column-only" data-column-number="197">
  - Cross-reference:   <span id="vol_col637" class="cross-reference" data-house-id="1" data-column-number="637" ...>
"""

from src.modules.parliament.resources import _extract_column_numbers, _assign_columns


# ---------------------------------------------------------------------------
# _extract_column_numbers
# ---------------------------------------------------------------------------

class TestExtractColumnNumbers:
    def test_empty_string(self):
        assert _extract_column_numbers("") == []

    def test_none_like_empty(self):
        # Callers pass item.get("Value") or "" — empty string case
        assert _extract_column_numbers("") == []

    def test_real_marker_plain(self):
        html = '<span id="200" class="column-number" data-column-number="200">200</span>'
        assert _extract_column_numbers(html) == [200]

    def test_real_marker_column_hidden(self):
        html = '<span id="207" class="column-number column-hidden" data-column-number="207">207</span>'
        assert _extract_column_numbers(html) == [207]

    def test_real_marker_column_only(self):
        html = '<span id="197" class="column-number column-only" data-column-number="197">197</span>'
        assert _extract_column_numbers(html) == [197]

    def test_cross_reference_excluded(self):
        """Cross-reference spans must be silently dropped."""
        html = (
            '<span id="vol_col637" class="cross-reference" data-house-id="1" '
            'data-column-number="637" data-volume-number="" data-external-id="abc">637</span>'
        )
        assert _extract_column_numbers(html) == []

    def test_cross_reference_with_volume_number_excluded(self):
        """Cross-reference with data-volume-number (e.g. Charter Budget debate)."""
        html = (
            '<span id="vol761_col344" class="cross-reference" data-house-id="1" '
            'data-column-number="344" data-volume-number="761" data-external-id="abc">344</span>'
        )
        assert _extract_column_numbers(html) == []

    def test_lord_pannick_case(self):
        """The exact pattern that caused column_start=637, column_end=200.

        Lord Pannick's contribution (Renters' Rights Bill, Lords, 14 Oct 2025):
        a Commons cross-reference at col-637 appeared before the real Lords
        col-200 marker.  Old code: [637, 200]. New code: [200].
        """
        html = (
            "despite repeated attempts to do so"
            '<span id="vol_col637" class="cross-reference" data-house-id="1" '
            'data-column-number="637" data-volume-number="" data-external-id="abc">637</span>'
            " as my noble friend noted, "
            '<span id="200" class="column-number" data-column-number="200">200</span>'
            " the position is clear."
        )
        assert _extract_column_numbers(html) == [200]

    def test_multiple_cross_refs_with_real_markers(self):
        """Immigration debate pattern: many cross-refs interspersed with real markers.

        Old code: [509, 650, 299, 651, 285, 321, 652]
        New code: [650, 651, 652]
        """
        html = (
            '<span id="vol_col509" class="cross-reference" data-house-id="1" data-column-number="509">509</span>'
            '<span id="650" class="column-number" data-column-number="650">650</span>'
            '<span id="vol_col299" class="cross-reference" data-house-id="2" data-column-number="299">299</span>'
            '<span id="651" class="column-number" data-column-number="651">651</span>'
            '<span id="vol_col285" class="cross-reference" data-house-id="1" data-column-number="285">285</span>'
            '<span id="vol_col321" class="cross-reference" data-house-id="2" data-column-number="321">321</span>'
            '<span id="652" class="column-number" data-column-number="652">652</span>'
        )
        assert _extract_column_numbers(html) == [650, 651, 652]

    def test_budget_debate_pattern(self):
        """Charter for Budget Responsibility: cross-refs sandwiching real markers.

        Old code: [344, 302, 345, 303]
        New code: [302, 303]
        """
        html = (
            '<span id="vol761_col344" class="cross-reference" data-house-id="1" data-column-number="344" data-volume-number="761">344</span>'
            '<span id="302" class="column-number" data-column-number="302">302</span>'
            '<span id="vol761_col345" class="cross-reference" data-house-id="1" data-column-number="345" data-volume-number="761">345</span>'
            '<span id="303" class="column-number" data-column-number="303">303</span>'
        )
        assert _extract_column_numbers(html) == [302, 303]

    def test_no_column_markers_at_all(self):
        html = "<p>Some speech with no column markers.</p>"
        assert _extract_column_numbers(html) == []

    def test_multiple_real_markers_in_one_contribution(self):
        """A contribution that spans two printed columns — both markers captured."""
        html = (
            '<span id="185" class="column-number" data-column-number="185">185</span>'
            " some text "
            '<span id="186" class="column-number" data-column-number="186">186</span>'
        )
        assert _extract_column_numbers(html) == [185, 186]


# ---------------------------------------------------------------------------
# _assign_columns — integration across a full item list
# ---------------------------------------------------------------------------

class TestAssignColumns:
    def _make_item(self, html: str = "") -> dict:
        return {"Value": html, "ItemType": "Contribution"}

    def test_carry_forward_when_no_marker(self):
        """Items without a marker inherit the running column."""
        items = [
            self._make_item('<span id="10" class="column-number" data-column-number="10">10</span>'),
            self._make_item("no marker here"),
            self._make_item("also no marker"),
        ]
        result = _assign_columns(items)
        assert result[0] == (10, 10)
        assert result[1] == (10, 10)   # carry-forward
        assert result[2] == (10, 10)   # still carry-forward

    def test_cross_reference_does_not_corrupt_carry_forward(self):
        """The fixed bug: cross-ref col-637 must not leak into column_start or current_column.

        Before fix: result[1] = (637, 200) — impossible span.
        After fix:  result[1] = (200, 200) — correct Lords column.
        """
        items = [
            # Previous item set current_column = 199
            self._make_item('<span id="199" class="column-number" data-column-number="199">199</span>'),
            # Lord Pannick: cross-ref to Commons col-637, then real Lords col-200
            self._make_item(
                '<span id="vol_col637" class="cross-reference" data-house-id="1" data-column-number="637">637</span>'
                ' some speech '
                '<span id="200" class="column-number" data-column-number="200">200</span>'
            ),
            # Next contribution: no marker — should carry-forward 200, not 637
            self._make_item("further speech"),
        ]
        result = _assign_columns(items)
        assert result[0] == (199, 199)
        assert result[1] == (200, 200), f"Expected (200, 200), got {result[1]}"
        assert result[2] == (200, 200), f"Expected carry-forward 200, got {result[2]}"

    def test_monotone_through_full_debate_shape(self):
        """Column numbers must be monotonically non-decreasing across all items."""
        items = [
            self._make_item('<span id="100" class="column-number" data-column-number="100">100</span>'),
            self._make_item("no marker"),
            self._make_item('<span id="101" class="column-number" data-column-number="101">101</span>'),
            self._make_item(
                '<span id="vol_col50" class="cross-reference" data-house-id="1" data-column-number="50">50</span>'
                '<span id="102" class="column-number" data-column-number="102">102</span>'
            ),
            self._make_item("no marker"),
        ]
        result = _assign_columns(items)
        col_starts = [result[i][0] for i in range(len(items))]
        for i in range(1, len(col_starts)):
            assert col_starts[i] >= col_starts[i - 1], (
                f"Non-monotone at idx={i}: {col_starts[i - 1]} → {col_starts[i]}"
            )
