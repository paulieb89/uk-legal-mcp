"""Unit tests for hansard_source_label — the Overview.Source ordinal → name map.

The Hansard Swagger declares Source as a 4-value string enum
(RollingHansard, DailyHansard, BoundVolume, Historic); the live JSON API
serialises it as the 1-indexed ordinal of that enum. The mapping is closed
(spec-derived) but must degrade gracefully on any unseen code rather than
raise. No network. (See memory/hansard-schema.md, Source section.)
"""

import pytest

from src.modules.parliament.resources import HANSARD_SOURCE_LABELS, hansard_source_label


@pytest.mark.parametrize(
    "code,expected",
    [
        (1, "RollingHansard"),
        (2, "DailyHansard"),
        (3, "BoundVolume"),
        (4, "Historic"),
    ],
)
def test_known_codes_map_to_swagger_names(code, expected):
    assert hansard_source_label(code) == expected


def test_none_passes_through_as_none():
    # A missing Source must stay None, never become a label or an error.
    assert hansard_source_label(None) is None


def test_unknown_code_degrades_to_labelled_fallback():
    # Unseen ordinal: honest fallback, not a KeyError and not a silent None.
    assert hansard_source_label(9) == "Unknown source code 9"
    assert hansard_source_label(0) == "Unknown source code 0"


def test_mapping_is_one_indexed_and_complete():
    # Guards the 1-indexed contract: keys are exactly 1..4 in enum order.
    assert list(HANSARD_SOURCE_LABELS) == [1, 2, 3, 4]
    assert list(HANSARD_SOURCE_LABELS.values()) == [
        "RollingHansard",
        "DailyHansard",
        "BoundVolume",
        "Historic",
    ]
