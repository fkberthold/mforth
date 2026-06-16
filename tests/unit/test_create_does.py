"""Unit + cross-backend tests for CREATE / ``,`` / DOES> defining words with
compile-time STAMPING (bead mforth-7h1.2).

B2 builds on B1's phase-0 ``expand`` pass (``mforth.expand.expand``). A ``:``
definition that contains a ``CREATE … DOES>`` body is a *defining word*.
Calling it (e.g. ``76 CONSTANT TROMBONES``) runs the CREATE-phase at COMPILE
TIME to populate an immutable compile-time-constant field (via ``,``), then
partial-evaluates + const-folds the DOES> body against that field, producing a
**cell-free** result that each child inlines to (riding B1's expand: a stamped
child behaves as a literal push). CONSTANT is the canonical case.

Three locked invariants (verbatim from the bead, grounded in design D4/D5/D7 +
exploration F14/F15):

INVARIANT A (CONSTANT — the bead RED line):
    Given ``: CONSTANT CREATE , DOES> @ ;`` and ``76 CONSTANT TROMBONES``,
    when TROMBONES is used it has static stack effect ``( -- n )`` = ``(0, 1)``,
    and BOTH backends observe a push of 76 (mlog: a literal ``set``/``print``,
    NO memory-cell ``read``/``write``) — REPL ↔ mlog equivalent.

INVARIANT B (general stamper):
    A derived-constant defining word, e.g. ``: DOUBLED CREATE , DOES> @ 2 * ;``
    and ``21 DOUBLED X``, stamps X to push 42 (the DOES> body partial-evaluated
    / folded against the field), effect ``( -- n )``, cell-free, REPL ↔ mlog
    equivalent. This is what distinguishes the GENERAL stamper from a
    CONSTANT-only special-case.

INVARIANT C (D5 cell-free boundary):
    A defining word whose DOES> body can NOT be made cell-free (needs a runtime
    cell — e.g. the child leaves the bare field address for later mutation, a
    ``!`` store, or a runtime-indexed ``+ @``) is a COMPILE ERROR that names the
    offending word and cites the cell-free boundary — NOT a miscompile, NOT a
    silent pass.

Child stack effect (D7/F15): child effect = DOES>-body effect adjusted for the
auto-pushed body address (the ``does>`` comment is the CHILD's effect; the body
address is auto-provided). For ``DOES> @``: body ``@`` is ``( addr -- n )``,
addr auto-supplied → child ``( -- n )``.

Tests pin OBSERVABLE behavior on the public source-level surface
(``compile_text`` → mlog text + ``MlogInterpreter`` for the mlog path; the host
``Runner`` for the REPL path), NOT how CREATE/DOES> are detected internally
(that is the implementer's freedom). The whole file is RED until B2 lands:
today ``: CONSTANT CREATE , DOES> @ ;`` does not stamp a child, so
``76 CONSTANT TROMBONES`` leaves TROMBONES undefined and the pipeline raises
``UnresolvedWordError`` at resolve.

SEAM flagged for the implementer (see INVARIANT C tests): there is no compile
error today that names a cell-free-boundary violation. These tests name that
error ``CellBoundaryError`` and locate it in ``mforth.expand`` (modeled on B1's
``PurityError`` — message names the offending word + cites the cell-free
boundary, D5). The implementer inherits the name; if a different name/location
is chosen the import + the ``pytest.raises`` target must be updated in lockstep.
"""

from __future__ import annotations

import pytest

from mforth.backend.world import MessagePrintEvent, MockWorld
from mforth.dictionary import StackEffect, resolve
from mforth.mlog_interp import MlogInterpreter
from mforth.optimize import OptLevel, compile_text
from mforth.stackcheck import stackcheck

# The cell-free-boundary compile error is a NEW error type the implementer
# must add (INVARIANT C). It lives alongside B1's ExpandError / PurityError in
# mforth.expand. Until the implementer adds it, the import fails — so we fall
# back to a private placeholder that is NOT in any real exception's MRO. That
# keeps the WHOLE module collectable (so INVARIANT A/B tests still fail for
# THEIR own reason — resolve can't find the stamped child) while the INVARIANT
# C tests stay RED: ``pytest.raises(CellBoundaryError)`` against this
# placeholder will NOT catch the ``UnresolvedWordError`` the unimplemented
# pipeline raises, so it reports "DID NOT RAISE the expected exception". When
# the implementer adds the real ``CellBoundaryError`` to ``mforth.expand``,
# this import resolves to it and the C tests pin the real behavior.
# ``test_cell_boundary_error_type_exists`` (below) is the dedicated RED assertion
# for the seam itself.
try:
    from mforth.expand import CellBoundaryError
except ImportError:  # pragma: no cover - RED until B2 lands the error type

    class CellBoundaryError(Exception):  # type: ignore[no-redef]
        """Placeholder so the module collects; NOT the real error.

        Deliberately unrelated to any pipeline exception, so a
        ``pytest.raises(CellBoundaryError)`` cannot be satisfied by an
        incidental ``UnresolvedWordError`` / ``StackError`` from the
        unimplemented stamper — the INVARIANT C tests stay honestly RED.
        """


# ---------------------------------------------------------------------------
# Canonical defining-word prelude lines, reused across tests.
# ---------------------------------------------------------------------------

# The textbook CONSTANT defining word.
CONSTANT_DEF = ": CONSTANT CREATE , DOES> @ ;\n"

# A derived-constant defining word: stores n, but each child pushes n*2.
DOUBLED_DEF = ": DOUBLED CREATE , DOES> @ 2 * ;\n"


# ---------------------------------------------------------------------------
# Helpers — source-level, mirroring test_fold.py / test_expand.py house style.
# ---------------------------------------------------------------------------


def _mlog(src: str) -> str:
    """Compile ``src`` to finalized mlog text at O0 (library default — the
    strict teaching-equivalence level)."""
    return compile_text(src, opt_level=OptLevel.O0, source_path="<test>")


def _mlog_prints(mlog_text: str) -> list[str]:
    """Run ``mlog_text`` through the in-repo interpreter for one iteration and
    return the ordered ``MessagePrintEvent`` texts."""
    world = MockWorld()
    MlogInterpreter(
        world=world, text=mlog_text, user_variables=set()
    ).run(iterations=1)
    return [
        e.text for e in world.events if isinstance(e, MessagePrintEvent)
    ]


def _host_prints(src: str, tmp_path) -> list[str]:
    """Run ``src`` through the host REPL ``Runner`` for one iteration and
    return the ordered ``MessagePrintEvent`` texts.

    Drives the runner's OWN public path (``Runner.from_path`` + ``run_once``),
    the same entry point the equivalence harness uses, so the test pins that
    the defining-word machinery is wired into the real host front-end (not a
    hand-assembled pipeline).
    """
    from mforth.backend.runner import Runner

    src_file = tmp_path / "prog.fs"
    src_file.write_text(src)
    runner = Runner.from_path(src_file)
    runner.run_once()
    return [
        e.text
        for e in runner.executor.world.events
        if isinstance(e, MessagePrintEvent)
    ]


def _child_effect(src: str, use_word: str) -> StackEffect:
    """Compile-check ``src`` and return the inferred stack effect of a
    definition named ``use_word`` (which wraps a single use of the stamped
    child).

    The cleanest faithful way to pin a child's ``( -- n )`` effect through the
    EXISTING public surface is to wrap one use of the child in a definition and
    read that definition's inferred effect off ``StackcheckResult.effects``
    (the same accessor test_stackcheck.py asserts against). The wrapper
    definition's effect equals the child's effect because the wrapper body is
    exactly the single child call.
    """
    from mforth.expand import expand
    from mforth.parse import parse

    program = parse(src, file="<test>")
    dictionary = resolve(program)
    program = expand(program, dictionary)
    result = stackcheck(program, dictionary=dictionary)
    return result.effects[use_word]


# ===========================================================================
# INVARIANT A — CONSTANT: the bead RED line.
#   `: CONSTANT CREATE , DOES> @ ;` + `76 CONSTANT TROMBONES`
#   → child effect ( -- n ) = (0, 1); BOTH backends push 76; mlog is cell-free.
# ===========================================================================


def test_constant_stamps_child_to_literal_push_mlog():
    """The mlog backend stamps TROMBONES to a literal push of 76 and PRINTs it.

    The whole point of compile-time stamping: ``76 CONSTANT TROMBONES`` runs
    the CREATE-phase at compile time, so a later ``TROMBONES PRINT`` lowers to
    a literal print of 76 — exactly as if the source said ``76 PRINT``.
    """
    src = CONSTANT_DEF + "76 CONSTANT TROMBONES\nTROMBONES PRINT\n"
    assert _mlog_prints(_mlog(src)) == ["76"]


def test_constant_emits_no_memory_cell_read_or_write():
    """A stamped CONSTANT is CELL-FREE (D5): its mlog contains no memory-cell
    ``read``/``write`` instruction.

    v1 is cell-free — values live in bare mlog variables / literals, never in a
    memory cell. The ONLY mlog instructions that touch a memory cell are
    ``read`` (cell fetch) and ``write`` (cell store). A stamped constant lowers
    to ``set``/``print`` (a literal), so neither opcode may appear. If the
    implementer ever lowered a CONSTANT to a runtime cell, this assertion trips
    loudly.
    """
    src = CONSTANT_DEF + "76 CONSTANT TROMBONES\nTROMBONES PRINT\n"
    mlog_text = _mlog(src)
    opcodes = {
        line.split()[0]
        for line in mlog_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "read" not in opcodes, (
        f"stamped CONSTANT emitted a memory-cell read — not cell-free:\n{mlog_text}"
    )
    assert "write" not in opcodes, (
        f"stamped CONSTANT emitted a memory-cell write — not cell-free:\n{mlog_text}"
    )


def test_constant_stamps_child_to_literal_push_host(tmp_path):
    """The host REPL backend also observes a push of 76 — REPL ↔ mlog
    equivalence for the CONSTANT case (CLAUDE.md headline property)."""
    src = CONSTANT_DEF + "76 CONSTANT TROMBONES\nTROMBONES PRINT\n"
    assert _host_prints(src, tmp_path) == ["76"]


def test_constant_repl_mlog_equivalent_print():
    """Same source, both backends, same observable PRINT — pinned directly as
    an equality (the headline REPL ↔ mlog property at the unit level)."""
    src = CONSTANT_DEF + "76 CONSTANT TROMBONES\nTROMBONES PRINT\n"
    mlog_prints = _mlog_prints(_mlog(src))
    # Host path via a temp file (mirrors _host_prints but inline so the two
    # event streams are compared in one place).
    import tempfile
    from pathlib import Path

    from mforth.backend.runner import Runner

    d = Path(tempfile.mkdtemp())
    f = d / "c.fs"
    f.write_text(src)
    runner = Runner.from_path(f)
    runner.run_once()
    host_prints = [
        e.text
        for e in runner.executor.world.events
        if isinstance(e, MessagePrintEvent)
    ]
    assert mlog_prints == host_prints == ["76"]


def test_constant_child_has_push_effect():
    """The stamped child has static stack effect ``( -- n )`` = ``(0, 1)``.

    For ``DOES> @``: the body ``@`` is ``( addr -- n )`` and the body address
    is auto-provided, so the CHILD nets ``( -- n )``. We pin this by wrapping a
    single use of the child in a definition and reading that definition's
    inferred effect (the child call is the wrapper's whole body, so the
    wrapper's effect IS the child's effect).
    """
    src = (
        CONSTANT_DEF
        + "76 CONSTANT TROMBONES\n"
        + ": USE-IT TROMBONES ;\n"
    )
    assert _child_effect(src, "USE-IT") == StackEffect(0, 1)


def test_two_constants_stamp_independently():
    """Two distinct CONSTANT children each stamp their own field value —
    proving the CREATE-phase field is per-child, not shared global state."""
    src = (
        CONSTANT_DEF
        + "3 CONSTANT THREE\n"
        + "9 CONSTANT NINE\n"
        + "THREE PRINT\nNINE PRINT\n"
    )
    assert _mlog_prints(_mlog(src)) == ["3", "9"]


# ===========================================================================
# INVARIANT B — general stamper: a DERIVED-constant defining word.
#   `: DOUBLED CREATE , DOES> @ 2 * ;` + `21 DOUBLED X`
#   → child pushes 42 (DOES> body partial-evaluated against the field),
#     effect ( -- n ), cell-free, REPL ↔ mlog equivalent.
# ===========================================================================


def test_general_stamper_folds_does_body_mlog():
    """``21 DOUBLED X`` stamps X to push 42: the DOES> body ``@ 2 *`` is
    partial-evaluated / const-folded against the field value 21 at compile
    time. This is the GENERAL stamper (not a CONSTANT-only special case)."""
    src = DOUBLED_DEF + "21 DOUBLED X\nX PRINT\n"
    assert _mlog_prints(_mlog(src)) == ["42"]


def test_general_stamper_is_cell_free():
    """The derived-constant child is ALSO cell-free — no ``read``/``write``.

    Even though the DOES> body does arithmetic (``@ 2 *``), it reduces to a
    cell-free literal push of 42 against the const field, so no memory-cell
    instruction is emitted (D5)."""
    src = DOUBLED_DEF + "21 DOUBLED X\nX PRINT\n"
    mlog_text = _mlog(src)
    opcodes = {
        line.split()[0]
        for line in mlog_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "read" not in opcodes and "write" not in opcodes, (
        f"derived-constant child was not cell-free:\n{mlog_text}"
    )


def test_general_stamper_host(tmp_path):
    """Host backend observes 42 too — REPL ↔ mlog equivalence for the derived
    stamper."""
    src = DOUBLED_DEF + "21 DOUBLED X\nX PRINT\n"
    assert _host_prints(src, tmp_path) == ["42"]


def test_general_stamper_child_has_push_effect():
    """The derived-constant child also has effect ``( -- n )`` = ``(0, 1)``:
    ``DOES> @ 2 *`` body is ``( addr -- n )`` (the ``2 *`` is stack-neutral
    after the fetch), addr auto-supplied → child ``( -- n )``."""
    src = (
        DOUBLED_DEF
        + "21 DOUBLED X\n"
        + ": USE-X X ;\n"
    )
    assert _child_effect(src, "USE-X") == StackEffect(0, 1)


# ===========================================================================
# INVARIANT C — D5 cell-free boundary: a DOES> body that needs a runtime cell
#   is a COMPILE ERROR naming the offending word + citing the cell-free
#   boundary. NOT a miscompile, NOT a silent pass.
# ===========================================================================


def test_cell_boundary_error_type_exists():
    """The seam itself: ``mforth.expand`` must export a real ``CellBoundaryError``
    exception type (the cell-free-boundary compile error, INVARIANT C).

    This is RED until the implementer adds it. The module-level fallback above
    installs a private placeholder so the rest of the file collects; this test
    asserts the REAL symbol exists by re-importing it fresh, so it fails
    cleanly (ImportError → AssertionError) until B2 lands the error type.
    """
    import importlib

    expand_mod = importlib.import_module("mforth.expand")
    assert hasattr(expand_mod, "CellBoundaryError"), (
        "mforth.expand must export CellBoundaryError — the cell-free-boundary "
        "compile error (D5 / INVARIANT C)"
    )
    assert issubclass(expand_mod.CellBoundaryError, Exception)


def test_does_body_storing_to_field_is_cell_boundary_error():
    """A DOES> body that performs a ``!`` store needs a mutable runtime cell —
    rejected at compile time (D5), NOT miscompiled.

    ``: CELLY CREATE , DOES> 5 SWAP ! ;`` would have each child store 5 into
    its field at runtime — that demands a mutable memory cell, which v1's
    cell-free strategy forbids."""
    src = (
        ": CELLY CREATE , DOES> 5 SWAP ! ;\n"
        + "0 CELLY COUNTER\n"
        + "COUNTER\n"
    )
    with pytest.raises(CellBoundaryError):
        _mlog(src)


def test_does_body_leaving_bare_address_is_cell_boundary_error():
    """A DOES> body that leaves the bare field ADDRESS on the stack (for later
    mutation by the caller) needs an addressable runtime cell — rejected (D5).

    ``: VAR CREATE , DOES> ;`` is the classic Forth ``VARIABLE``-via-CREATE:
    each child pushes its field address so the caller can ``@`` / ``!`` it.
    That is exactly the addressable-cell semantics v1 cannot represent."""
    src = (
        ": VAR CREATE , DOES> ;\n"
        + "0 VAR SPEED\n"
        + "SPEED @ PRINT\n"
    )
    with pytest.raises(CellBoundaryError):
        _mlog(src)


def test_does_body_runtime_indexed_fetch_is_cell_boundary_error():
    """A DOES> body with a runtime-indexed fetch (``+ @`` against the field
    address) needs an indexable runtime cell — rejected (D5).

    ``: TABLE CREATE , DOES> + @ ;`` is the array-access idiom: the child takes
    a runtime index, adds it to the field base, and fetches — a runtime memory
    read mforth v1 has no cell for."""
    src = (
        ": TABLE CREATE , DOES> + @ ;\n"
        + "100 TABLE LUT\n"
        + "3 LUT PRINT\n"
    )
    with pytest.raises(CellBoundaryError):
        _mlog(src)


def test_cell_boundary_error_names_the_offending_word():
    """The cell-free-boundary error message NAMES the offending defining word
    (or child) so the user can find it — modeled on B1's ``PurityError``, which
    names the offending primitive. We assert the message mentions the defining
    word ``CELLY`` (the word whose DOES> body crossed the boundary)."""
    src = (
        ": CELLY CREATE , DOES> 5 SWAP ! ;\n"
        + "0 CELLY COUNTER\n"
        + "COUNTER\n"
    )
    with pytest.raises(CellBoundaryError) as exc:
        _mlog(src)
    assert "CELLY" in str(exc.value).upper(), (
        f"cell-boundary error must name the offending word; got: {exc.value!r}"
    )


def test_cell_boundary_error_is_not_a_silent_pass():
    """A boundary-crossing DOES> body must NEVER silently compile to something
    observable — it MUST raise. This guards against a regression where the
    stamper degrades to a no-op or a miscompile instead of a clean error."""
    src = (
        ": CELLY CREATE , DOES> 5 SWAP ! ;\n"
        + "0 CELLY COUNTER\n"
        + "COUNTER PRINT\n"
    )
    with pytest.raises(CellBoundaryError):
        _mlog(src)
