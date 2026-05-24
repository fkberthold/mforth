"""Integration test for the LSP surfacing of Mindustry @-identifiers.

Bead mforth-eaz. Drives the in-process LSP seams (`completions_for`,
`hover_for`) and asserts that the 154 magic-var / content-name /
sensor-property dictionary entries automatically appear in LSP
completion and hover — because both .25 and .24 walk
`standard_dictionary()`, no LSP code change should be required.
"""

from __future__ import annotations

import textwrap

import pytest

pytest.importorskip("pygls")
pytest.importorskip("lsprotocol")

from lsprotocol import types as lsp

from mforth.lsp.server import completions_for, hover_for


# ---------------------------------------------------------------------------
# Completion: @-identifiers appear by category
# ---------------------------------------------------------------------------


def _completion_labels(text: str, line: int = 0, col: int = 0) -> set[str]:
    items = completions_for(
        text, uri="file:///t.fs", position=lsp.Position(line=line, character=col)
    )
    return {item.label for item in items}


def test_completion_surfaces_time_magic_vars():
    labels = _completion_labels("")
    for name in ["@tick", "@time", "@thisx", "@thisy"]:
        assert name in labels, f"missing magic-var completion '{name}'"


def test_completion_surfaces_content_names():
    labels = _completion_labels("")
    for name in [
        "@copper", "@lead", "@phase-fabric", "@surge-alloy",
        "@water", "@cryofluid",
    ]:
        assert name in labels, f"missing content completion '{name}'"


def test_completion_surfaces_sensor_props():
    labels = _completion_labels("")
    for name in ["@health", "@maxHealth", "@x", "@y", "@team"]:
        assert name in labels, f"missing sensor-prop completion '{name}'"


def test_completion_surfaces_essential_unit_and_block():
    labels = _completion_labels("")
    for name in ["@poly", "@flare", "@logic-processor", "@message"]:
        assert name in labels, f"missing unit/block completion '{name}'"


def test_completion_surfaces_alias_at_ticks():
    """@ticks (alias of @tick) must surface in completion too."""
    labels = _completion_labels("")
    assert "@ticks" in labels
    assert "@tick" in labels


# ---------------------------------------------------------------------------
# Hover: @-identifier hover returns doc string + stack effect
# ---------------------------------------------------------------------------


def _hover_text(src: str, line: int, col: int) -> str | None:
    h = hover_for(
        src, uri="file:///t.fs", position=lsp.Position(line=line, character=col)
    )
    if h is None or h.contents is None:
        return None
    # h.contents is a MarkupContent
    return h.contents.value if hasattr(h.contents, "value") else str(h.contents)


def test_hover_on_at_copper_shows_doc():
    src = "@copper"
    hov = _hover_text(src, 0, 3)
    assert hov is not None
    # Must include the canonical name and stack-effect render.
    assert "@copper" in hov
    # Stack effect rendered with explicit arity numbers per .24's
    # `_format_stack_effect` (e.g., `( 0 -- 1 )` for `(--n)`-style entries).
    assert "(" in hov and "--" in hov


def test_hover_on_at_time_shows_doc():
    src = "@time"
    hov = _hover_text(src, 0, 2)
    assert hov is not None
    assert "@time" in hov
