"""Count-drift guards for the Mindustry @-identifier registry.

The dictionary's own header comments cite a total @-identifier count and a
per-category breakdown (bead mforth-eaz). Those numbers drifted out of sync
with the actual registry (bead mforth-73h: comments said 154; the registry
holds 170 entries + 1 alias = 171). These tests pin the real, programmatically
computed counts so any future addition to `_MINDUSTRY_IDENTIFIERS` forces the
human to update the header comment in the same change — the comment is parsed
out of the source and asserted equal to the live count.
"""

from __future__ import annotations

import inspect
import re

import mforth.dictionary as dictionary
from mforth.dictionary import (
    _MINDUSTRY_ALIASES,
    _MINDUSTRY_IDENTIFIERS,
    _block_entries,
    _item_entries,
    _liquid_entries,
    _magic_entries,
    _sensor_prop_entries,
    _unit_entries,
)

# Real, hand-verified-then-locked counts. Computed programmatically below
# (see test_expected_counts_match_live_registry); these literals exist so a
# future edit that changes a category size shows up as a one-line diff here
# AND must be matched by a comment edit in dictionary.py.
EXPECTED_MAGIC = 29
EXPECTED_ITEM = 22
EXPECTED_LIQUID = 11
EXPECTED_UNIT = 22
EXPECTED_BLOCK = 15
EXPECTED_SENSOR_PROP = 71
EXPECTED_ALIASES = 1
EXPECTED_REGISTRY = (
    EXPECTED_MAGIC
    + EXPECTED_ITEM
    + EXPECTED_LIQUID
    + EXPECTED_UNIT
    + EXPECTED_BLOCK
    + EXPECTED_SENSOR_PROP
)  # 170
EXPECTED_TOTAL = EXPECTED_REGISTRY + EXPECTED_ALIASES  # 171 (170 + 1 alias)


def test_per_category_counts_match_live_entry_builders() -> None:
    assert len(_magic_entries()) == EXPECTED_MAGIC
    assert len(_item_entries()) == EXPECTED_ITEM
    assert len(_liquid_entries()) == EXPECTED_LIQUID
    assert len(_unit_entries()) == EXPECTED_UNIT
    assert len(_block_entries()) == EXPECTED_BLOCK
    assert len(_sensor_prop_entries()) == EXPECTED_SENSOR_PROP


def test_registry_length_matches_expected() -> None:
    assert len(_MINDUSTRY_IDENTIFIERS) == EXPECTED_REGISTRY


def test_alias_count_matches_expected() -> None:
    assert len(_MINDUSTRY_ALIASES) == EXPECTED_ALIASES


def test_total_is_registry_plus_aliases() -> None:
    assert len(_MINDUSTRY_IDENTIFIERS) + len(_MINDUSTRY_ALIASES) == EXPECTED_TOTAL


def _documented_numbers() -> list[int]:
    """Every integer the dictionary.py source text claims as an @-identifier
    count, pulled from the comments. Robust to wording: we collect all
    integers that appear as ``<n> entries`` or ``<n>-entry`` in the source.
    """
    src = inspect.getsource(dictionary)
    nums: list[int] = []
    for m in re.finditer(r"(\d+)\s*(?:-entry|entries)\b", src):
        nums.append(int(m.group(1)))
    return nums


def test_source_comment_cites_correct_total() -> None:
    """The header comment must reference the true total (171) so the stale
    '154' can never silently survive a future registry edit.
    """
    src = inspect.getsource(dictionary)
    documented = _documented_numbers()
    assert EXPECTED_TOTAL in documented, (
        f"dictionary.py comments do not cite the real total {EXPECTED_TOTAL}; "
        f"found counts cited as '<n> entries/-entry': {documented}"
    )
    # And the known-stale number must be gone.
    assert 154 not in documented, (
        "dictionary.py still cites the stale total 154 in a count comment"
    )


def test_source_comment_cites_each_category_count() -> None:
    """The per-category breakdown in the §-comment must match the live
    builders, so a category that grows forces a comment edit.
    """
    documented = set(_documented_numbers())
    for label, count in (
        ("magic", EXPECTED_MAGIC),
        ("item", EXPECTED_ITEM),
        ("liquid", EXPECTED_LIQUID),
        ("unit", EXPECTED_UNIT),
        ("block", EXPECTED_BLOCK),
        ("sensor-prop", EXPECTED_SENSOR_PROP),
    ):
        assert count in documented, (
            f"dictionary.py comments do not cite the {label} category "
            f"count {count}; cited counts: {sorted(documented)}"
        )
