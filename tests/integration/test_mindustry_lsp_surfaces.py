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
    """@ticks (alias of @tick) must surface in completion too.

    The alias is wired into ``standard_dictionary()._entries`` (mforth-eaz)
    and the completion path iterates ``_entries`` so the alias produces its
    own item. Both the alias label and the canonical label appear, and the
    alias detail carries the SAME canonical stack-effect render as every
    other value-pushing @-identifier — ``( 0 -- 1 )`` (one value out, zero
    in), per ``_format_stack_effect`` (beads mforth-7ma + mforth-9lx).
    """
    items = completions_for(
        "", uri="file:///t.fs", position=lsp.Position(line=0, character=0)
    )
    by_label = {item.label: item for item in items}
    assert "@ticks" in by_label
    assert "@tick" in by_label
    # Canonical stack-effect format — NOT the Forth-traditional `( -- n )`.
    assert by_label["@ticks"].detail == "( 0 -- 1 )"
    assert by_label["@tick"].detail == "( 0 -- 1 )"
    # The alias documentation declares it an alias of the canonical name.
    alias_doc = by_label["@ticks"].documentation
    alias_doc_value = (
        alias_doc.value if hasattr(alias_doc, "value") else str(alias_doc)
    )
    assert "alias of @tick" in alias_doc_value


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
    # `_format_stack_effect`. @copper pushes one value (zero in, one out),
    # so it renders with the SAME convention as every other value-pusher:
    # `( 0 -- 1 )`. This is the canonical format — NOT the Forth-traditional
    # `( -- n )` the bead's original assertion wrongly expected (mforth-7ma).
    assert "( 0 -- 1 )" in hov
    assert "( -- " not in hov
    # Whole render: `<name> <effect>\n<doc>`.
    assert hov.startswith("@copper ( 0 -- 1 )")


def test_hover_on_alias_at_ticks_renders_canonical():
    """Hovering the @ticks alias resolves to its canonical @tick entry and
    renders the canonical `( 0 -- 1 )` stack effect (mforth-9lx)."""
    src = "@ticks"
    hov = _hover_text(src, 0, 3)
    assert hov is not None
    # Alias hover surfaces the canonical entry name + canonical effect.
    assert hov.startswith("@tick ( 0 -- 1 )")
    assert "( -- " not in hov


def test_hover_on_at_time_shows_doc():
    src = "@time"
    hov = _hover_text(src, 0, 2)
    assert hov is not None
    assert "@time" in hov
