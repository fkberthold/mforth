"""Unit tests for the LSP's awareness of the phase-0 expand pass (B6).

Bead mforth-7h1.4. The meta layer — B1 macro inlining, B2 CREATE/`,`/DOES>
defining words + CONSTANT stamping, B3 user macros (`MACRO: name ... ;`) —
is eliminated by a single phase-0 ``expand`` pass that the compiler runs
BETWEEN ``resolve`` and ``stackcheck`` (design D13). Before this bead the
LSP analyzers ran ``resolve → stackcheck`` directly, so a defining-word or
macro document diverged from ``mforth compile``: stackcheck saw the
not-yet-expanded meta-words (``CREATE``, a ``Macro`` entry) and either
reported a phantom ``unresolved word 'CREATE'`` or crashed on an
``unknown dictionary entry type Macro``.

This module pins three contracts:

1. **Diagnostics match ``mforth compile``** (D13 hard contract). On a
   defining-word / macro source ``analyze_document`` must agree with
   ``compile_text`` (the ``mforth compile`` library entry point): clean
   when the compiler compiles, error when the compiler raises.

2. **Hover shows the child's stamped stack effect** (D7/F15). Hovering a
   stamped child (``TROMBONES`` from ``76 CONSTANT TROMBONES``) shows the
   child's uniform statically-known effect ``( 0 -- 1 )`` — it pushes the
   stamped constant.

3. **Completion includes user defining words + their children + macros.**
"""

from __future__ import annotations

import pytest

pytest.importorskip("pygls")
pytest.importorskip("lsprotocol")

from lsprotocol import types as lsp

from mforth.lsp.server import (
    analyze_document,
    completions_for,
    hover_for,
)
from mforth.optimize import compile_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos(line: int, character: int) -> lsp.Position:
    return lsp.Position(line=line, character=character)


def _hover_text(hover: lsp.Hover | None) -> str:
    assert hover is not None
    contents = hover.contents
    if isinstance(contents, lsp.MarkupContent):
        return contents.value
    if isinstance(contents, str):
        return contents
    return "\n".join(getattr(c, "value", c) for c in contents)


def _compiles(text: str) -> bool:
    """True iff ``mforth compile`` (the library entry point) accepts ``text``."""
    try:
        compile_text(text)
        return True
    except Exception:
        return False


# Canonical defining-word source: the textbook CONSTANT.
_CONSTANT_SRC = (
    ": CONSTANT CREATE , DOES> @ ;\n"
    "76 CONSTANT TROMBONES\n"
    "TROMBONES .\n"
)

# Canonical user-macro source.
_MACRO_SRC = "MACRO: inc 1 + ;\n5 inc .\n"

# A DOES> body that crosses the cell-free boundary (a `!` store) — the
# compiler rejects this with CellBoundaryError; the LSP must agree.
_CELL_BOUNDARY_SRC = ": MKVAR CREATE , DOES> ! ;\n5 MKVAR FOO\n"


# ---------------------------------------------------------------------------
# 1. Diagnostics match `mforth compile`
# ---------------------------------------------------------------------------


def test_defining_word_source_has_no_diagnostics_matching_compiler():
    """A clean CREATE/,/DOES> CONSTANT compiles, so the LSP must show
    no diagnostics. Before expand was slotted into ``analyze_document``,
    stackcheck saw the un-stripped ``CREATE`` and reported a phantom
    ``unresolved word 'CREATE'``."""
    assert _compiles(_CONSTANT_SRC), "fixture must compile via mforth compile"
    diags = analyze_document(_CONSTANT_SRC, uri="file:///tmp/constant.fs")
    assert diags == [], f"expected no diagnostics, got {[d.message for d in diags]!r}"


def test_user_macro_source_has_no_diagnostics_matching_compiler():
    """A clean ``MACRO: inc 1 + ;`` compiles, so the LSP must show no
    diagnostics. Before expand was slotted in, stackcheck hit the
    not-yet-inlined ``Macro`` entry and crashed with
    ``unknown dictionary entry type Macro``."""
    assert _compiles(_MACRO_SRC), "fixture must compile via mforth compile"
    diags = analyze_document(_MACRO_SRC, uri="file:///tmp/macro.fs")
    assert diags == [], f"expected no diagnostics, got {[d.message for d in diags]!r}"


def test_cell_boundary_violation_yields_diagnostic_matching_compiler():
    """A DOES> body that needs a mutable cell is rejected by the compiler
    (CellBoundaryError); the LSP must surface exactly one error diagnostic,
    agreeing with ``mforth compile``."""
    assert not _compiles(_CELL_BOUNDARY_SRC), (
        "fixture must FAIL to compile (cell-free boundary)"
    )
    diags = analyze_document(_CELL_BOUNDARY_SRC, uri="file:///tmp/cell.fs")
    assert len(diags) == 1
    assert diags[0].severity == lsp.DiagnosticSeverity.Error
    assert diags[0].source == "mforth"


# ---------------------------------------------------------------------------
# 2. Hover shows the stamped child stack effect (D7/F15)
# ---------------------------------------------------------------------------


def test_hover_on_stamped_child_shows_stamped_stack_effect():
    """Hovering ``TROMBONES`` (a stamped CONSTANT child) shows the child's
    statically-known stack effect ``( 0 -- 1 )`` — it pushes one value.

    The stamped child becomes a ``Macro`` whose body is a single literal
    push, so its effect is zero-in / one-out. The hover must compute this
    from the post-expand dictionary (the original WordCall is inlined away,
    so positioning uses the pre-expand parse)."""
    # `TROMBONES` call site is on line 3 (LSP line 2), starting col 1.
    h = hover_for(_CONSTANT_SRC, uri="file:///tmp/constant.fs", position=_pos(2, 3))
    body = _hover_text(h)
    assert "TROMBONES" in body
    assert "( 0 -- 1 )" in body, f"expected stamped effect ( 0 -- 1 ), got: {body!r}"


def test_hover_on_user_macro_call_shows_effect():
    """Hovering a user-macro call (``inc``) shows its inlined stack effect
    ``( 1 -- 1 )`` (``1 +`` consumes one, produces one)."""
    # `inc` call site on line 2 (LSP line 1) at col 3.
    h = hover_for(_MACRO_SRC, uri="file:///tmp/macro.fs", position=_pos(1, 2))
    body = _hover_text(h)
    assert "inc" in body
    assert "( 1 -- 1 )" in body, f"expected macro effect ( 1 -- 1 ), got: {body!r}"


# ---------------------------------------------------------------------------
# 3. Completion includes defining words + their children + macros
# ---------------------------------------------------------------------------


def test_completion_includes_defining_word_and_stamped_child():
    """Completion after both declarations surfaces the defining word
    (``CONSTANT``) AND its stamped child (``TROMBONES``)."""
    items = completions_for(
        _CONSTANT_SRC, uri="file:///tmp/constant.fs", position=_pos(2, 0)
    )
    labels = {i.label for i in items}
    assert "CONSTANT" in labels, "defining word missing from completion"
    assert "TROMBONES" in labels, "stamped child missing from completion"


def test_completion_includes_user_macro():
    """Completion after a ``MACRO:`` declaration surfaces the macro name."""
    items = completions_for(
        _MACRO_SRC, uri="file:///tmp/macro.fs", position=_pos(1, 0)
    )
    labels = {i.label for i in items}
    assert "inc" in labels, "user macro missing from completion"
