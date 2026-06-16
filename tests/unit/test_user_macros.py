"""Unit + cross-backend tests for the USER-FACING macro definition syntax
``MACRO: name <body> ;`` (bead mforth-7h1.3).

B1 (mforth-7h1.1) built the INTERNAL ``Macro(name, body)`` representation plus
ALL of the expansion/purity/cycle machinery in ``mforth.expand``: it inlines a
``Macro`` to a fixpoint (descending into control-flow bodies), raises
``ExpandError`` on a cyclic expansion, and raises ``PurityError`` (tag-driven)
when a macro body calls a world-sink primitive or reads runtime state. Those
tests (``tests/unit/test_expand.py``) seed ``Macro`` entries PROGRAMMATICALLY —
there is NO ``.fs`` surface syntax in B1.

B3 adds exactly that missing surface: the defining-word ``MACRO: name <body
terms> ;`` (Frank's locked choice — mirrors the ``:`` defining-word syntax and
reuses the ``;`` terminator). At a use site the macro name expands at compile
time, hygienically, by pure term-substitution to a fixpoint:

    MACRO: sq DUP * ;
    5 sq PRINT        \\  ->  5 DUP * PRINT  ->  prints "25"

Once B3 PARSES a ``MACRO:`` definition and REGISTERS a ``Macro`` in the
dictionary, B1's existing machinery does the rest. So these tests pin only
OBSERVABLE behaviour on the public source-level surface — they never assert HOW
``MACRO:`` is lexed/parsed or how the ``Macro`` is registered (that is the
implementer's freedom).

Six locked invariants (verbatim from the bead, grounded in design D2/D8 +
exploration F7):

INVARIANT A (basic expansion, both backends):
    ``MACRO: sq DUP * ;`` + ``5 sq PRINT`` prints "25" via BOTH the mlog backend
    (``compile_text`` -> ``MlogInterpreter``) and the host backend (``Runner``),
    REPL <-> mlog equivalent; the stamped/expanded macro is CELL-FREE (no mlog
    ``read``/``write``).

INVARIANT B (fixpoint / macro -> macro):
    A macro whose body references another macro expands fully:
    ``MACRO: sq DUP * ;`` + ``MACRO: quad sq sq ;`` then ``3 quad PRINT`` ->
    "81" (3 -> 9 -> 81). Zero macro names survive to stackcheck/codegen.

INVARIANT C (cyclic -> ExpandError):
    A self/mutually-cyclic macro (e.g. ``MACRO: loopy 1 loopy ;``) is a compile
    error (``mforth.expand.ExpandError``) — never a hang or a miscompile.

INVARIANT D (purity -> PurityError):
    A macro body that calls a world-sink primitive (e.g. ``MACRO: shout 1 PRINT
    ;``) is a compile error (``mforth.expand.PurityError``) — reusing B1's D14
    purity check.

INVARIANT E (macro inside a ``:`` definition):
    A macro used inside a ``: ... ;`` body expands there too:
    ``MACRO: sq DUP * ; : f sq ; 6 f PRINT`` -> "36".

INVARIANT F (hygiene = pure substitution, v1):
    The macro is a pure compile-time term substitution: the same macro at two
    different call sites each expands independently/correctly
    (``MACRO: sq DUP * ; 4 sq PRINT 5 sq PRINT`` -> "16" then "25"); the body's
    words are the standard ops regardless of call site. (mforth has a flat
    dictionary + no locals, so there are no binding forms to capture — the
    hygiene assertion stays at this observable "pure substitution / no capture"
    property.)

The whole file is RED until B3 lands: today ``MACRO: sq DUP * ;`` is not parsed
as a macro definition, so the source never registers a ``Macro`` and the
pipeline rejects it at parse/resolve (e.g. ``MACRO:`` and/or ``sq`` are
unresolved words). ``ExpandError`` and ``PurityError`` already exist in
``mforth.expand`` (B1) — they are imported directly, NOT new seams. The
implementer's only new work is the ``MACRO:`` parse + ``Macro`` registration;
no test below references an internal that B3 must invent.

Equivalence fixture G (``user_macro.{fs,world.toml}``) lives under
``tests/integration/fixtures/equivalence/`` and is auto-discovered by
``tests/integration/test_equivalence.py`` (mirrors ``constant_stamp``); it
exercises a macro printing to a message block, event-identical at O0.
"""

from __future__ import annotations

import pytest

from mforth.backend.world import MessagePrintEvent, MockWorld
from mforth.mlog_interp import MlogInterpreter
from mforth.optimize import OptLevel, compile_text

# ExpandError / PurityError already exist in mforth.expand (bead mforth-7h1.1).
# They are NOT new seams — B3 reuses B1's cycle + purity machinery wholesale,
# so we import them directly. A failure to import here would be a real
# regression in B1, not a missing B3 feature.
from mforth.expand import ExpandError, PurityError


# ---------------------------------------------------------------------------
# Canonical macro-definition prelude lines, reused across tests.
# ---------------------------------------------------------------------------

# The headline macro: square the top of stack.
SQ_DEF = "MACRO: sq DUP * ;\n"

# A macro whose body references another macro (fixpoint).
QUAD_DEF = "MACRO: quad sq sq ;\n"


# ---------------------------------------------------------------------------
# Helpers — source-level, mirroring test_create_does.py / test_expand.py house
# style. Compile/run .fs SOURCE STRINGS and assert OBSERVABLE results; never
# assert on how MACRO: is lexed/parsed or how the Macro is registered.
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
    the macro-definition machinery is wired into the real host front-end (not a
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


def _opcodes(mlog_text: str) -> set[str]:
    """The set of mlog opcodes (first token of each non-comment line)."""
    return {
        line.split()[0]
        for line in mlog_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


# ===========================================================================
# INVARIANT A — basic expansion on BOTH backends + cell-free.
#   `MACRO: sq DUP * ;` + `5 sq PRINT` -> prints "25" on mlog AND host,
#   REPL <-> mlog equivalent; no memory-cell read/write.
# ===========================================================================


def test_basic_macro_expands_and_prints_mlog():
    """``5 sq PRINT`` prints "25" via the mlog backend.

    The macro body ``DUP *`` is spliced in place of ``sq`` at compile time, so
    ``5 sq`` becomes ``5 DUP *`` -> 25 — exactly as if the source said
    ``5 DUP * PRINT``."""
    src = SQ_DEF + "5 sq PRINT\n"
    assert _mlog_prints(_mlog(src)) == ["25"]


def test_basic_macro_expands_and_prints_host(tmp_path):
    """``5 sq PRINT`` prints "25" via the host REPL backend — REPL <-> mlog
    equivalence for the basic macro case (CLAUDE.md headline property)."""
    src = SQ_DEF + "5 sq PRINT\n"
    assert _host_prints(src, tmp_path) == ["25"]


def test_basic_macro_repl_mlog_equivalent_print(tmp_path):
    """Same source, both backends, same observable PRINT — pinned directly as
    an equality (the headline REPL <-> mlog property at the unit level)."""
    src = SQ_DEF + "5 sq PRINT\n"
    mlog_prints = _mlog_prints(_mlog(src))
    host_prints = _host_prints(src, tmp_path)
    assert mlog_prints == host_prints == ["25"]


def test_basic_macro_is_cell_free():
    """An expanded macro is CELL-FREE (v1): its mlog contains no memory-cell
    ``read``/``write`` instruction.

    v1 is cell-free — values live in bare mlog variables / literals, never in a
    memory cell. The macro body is pure arithmetic/stack ops, so ``5 sq`` lowers
    to ``set``/``op``/``print`` and neither cell opcode may appear. If a future
    implementation ever routed a macro through a runtime cell, this trips
    loudly."""
    src = SQ_DEF + "5 sq PRINT\n"
    mlog_text = _mlog(src)
    opcodes = _opcodes(mlog_text)
    assert "read" not in opcodes, (
        f"expanded macro emitted a memory-cell read — not cell-free:\n{mlog_text}"
    )
    assert "write" not in opcodes, (
        f"expanded macro emitted a memory-cell write — not cell-free:\n{mlog_text}"
    )


# ===========================================================================
# INVARIANT B — fixpoint: a macro whose body references another macro.
#   `MACRO: sq DUP * ;` + `MACRO: quad sq sq ;` + `3 quad PRINT` -> "81".
# ===========================================================================


def test_macro_referencing_macro_expands_to_fixpoint_mlog():
    """``3 quad PRINT`` prints "81": ``quad``'s body ``sq sq`` itself expands
    (each ``sq`` -> ``DUP *``), so 3 -> 9 -> 81 — expansion runs to a fixpoint,
    not just one level."""
    src = SQ_DEF + QUAD_DEF + "3 quad PRINT\n"
    assert _mlog_prints(_mlog(src)) == ["81"]


def test_macro_referencing_macro_expands_to_fixpoint_host(tmp_path):
    """The host backend agrees: ``3 quad PRINT`` -> "81" — REPL <-> mlog
    equivalence for the nested-macro fixpoint."""
    src = SQ_DEF + QUAD_DEF + "3 quad PRINT\n"
    assert _host_prints(src, tmp_path) == ["81"]


def test_nested_macro_is_cell_free():
    """The fully-expanded nested macro is ALSO cell-free — zero macro names
    survive to codegen, so no memory-cell ``read``/``write`` is emitted."""
    src = SQ_DEF + QUAD_DEF + "3 quad PRINT\n"
    mlog_text = _mlog(src)
    opcodes = _opcodes(mlog_text)
    assert "read" not in opcodes and "write" not in opcodes, (
        f"nested macro was not cell-free:\n{mlog_text}"
    )


# ===========================================================================
# INVARIANT C — cyclic macro -> ExpandError (B1's cycle machinery, reused).
# ===========================================================================


def test_self_cyclic_macro_is_compile_error():
    """A self-referential macro never converges → ``ExpandError`` (a compile
    error), NOT a hang or a miscompile.

    ``MACRO: loopy 1 loopy ;`` would inline ``loopy`` inside its own body
    forever; B1's cycle detection (reused unchanged by B3) aborts cleanly."""
    src = "MACRO: loopy 1 loopy ;\nloopy PRINT\n"
    with pytest.raises(ExpandError):
        _mlog(src)


def test_mutually_cyclic_macros_is_compile_error():
    """A mutual cycle (``ma`` -> ``mb`` -> ``ma``) also fails to converge →
    ``ExpandError`` — proving B3's surface syntax inherits B1's full cycle
    detection, not just the direct-self case."""
    src = (
        "MACRO: ma mb ;\n"
        "MACRO: mb ma ;\n"
        "ma PRINT\n"
    )
    with pytest.raises(ExpandError):
        _mlog(src)


# ===========================================================================
# INVARIANT D — impure macro body -> PurityError (B1's D14 check, reused).
# ===========================================================================


def test_macro_calling_world_sink_is_purity_error():
    """A macro whose body calls a world-sink primitive (``PRINT``) is a compile
    error → ``PurityError``. Macros are pure compile-time substitutions; they
    may not bake a runtime world effect into their body.

    ``MACRO: shout 1 PRINT ;`` would inline a ``PRINT`` (tag "mindustry") into
    every call site; B1's tag-driven purity check (reused by B3) rejects it."""
    src = "MACRO: shout 1 PRINT ;\nshout\n"
    with pytest.raises(PurityError):
        _mlog(src)


def test_purity_error_names_the_offending_primitive():
    """The ``PurityError`` message NAMES the offending world-sink primitive so
    the user can find it — the same property B1's ``test_expand.py`` pins for a
    programmatically-seeded macro, now reached through the ``MACRO:`` surface."""
    src = "MACRO: shout 1 PRINT ;\nshout\n"
    with pytest.raises(PurityError) as exc:
        _mlog(src)
    assert "PRINT" in str(exc.value).upper(), (
        f"purity error must name the offending primitive; got: {exc.value!r}"
    )


# ===========================================================================
# INVARIANT E — a macro used inside a `: ... ;` definition body expands there.
#   `MACRO: sq DUP * ; : f sq ; 6 f PRINT` -> "36".
# ===========================================================================


def test_macro_inside_colon_definition_expands_mlog():
    """A macro called inside a ``:`` definition body expands within that body:
    ``: f sq ;`` becomes ``: f DUP * ;``, so ``6 f`` -> 36 on the mlog
    backend."""
    src = SQ_DEF + ": f sq ;\n6 f PRINT\n"
    assert _mlog_prints(_mlog(src)) == ["36"]


def test_macro_inside_colon_definition_expands_host(tmp_path):
    """The host backend agrees: ``6 f PRINT`` -> "36" with ``f`` defined as
    ``: f sq ;`` — REPL <-> mlog equivalence for a macro inside a colon
    definition."""
    src = SQ_DEF + ": f sq ;\n6 f PRINT\n"
    assert _host_prints(src, tmp_path) == ["36"]


# ===========================================================================
# INVARIANT F — hygiene = pure compile-time substitution (v1).
#   The same macro at two call sites each expands independently/correctly:
#   `MACRO: sq DUP * ; 4 sq PRINT 5 sq PRINT` -> "16" then "25".
# ===========================================================================


def test_macro_is_pure_substitution_at_each_call_site_mlog():
    """The same macro expands INDEPENDENTLY at each call site: ``4 sq`` -> 16
    and ``5 sq`` -> 25 in the same program. Expansion is a pure compile-time
    term substitution — each ``sq`` lowers ``DUP *`` against whatever value
    precedes it, with no carry-over or capture between sites."""
    src = SQ_DEF + "4 sq PRINT\n5 sq PRINT\n"
    assert _mlog_prints(_mlog(src)) == ["16", "25"]


def test_macro_is_pure_substitution_at_each_call_site_host(tmp_path):
    """The host backend observes the same two independent expansions —
    ``["16", "25"]`` — confirming the pure-substitution / no-capture property
    holds identically on the REPL teaching surface."""
    src = SQ_DEF + "4 sq PRINT\n5 sq PRINT\n"
    assert _host_prints(src, tmp_path) == ["16", "25"]


# ===========================================================================
# G — the equivalence fixture (user_macro.{fs,world.toml}) is auto-discovered
# by tests/integration/test_equivalence.py; this unit test additionally pins,
# at the source level, that a macro printing to a message block is cell-free
# and observable on the mlog backend (the same program the fixture exercises).
# ===========================================================================


def test_macro_printing_to_message_block_is_cell_free():
    """A macro used in a program that PRINTs + PRINTFLUSHes to a message block
    stays cell-free on the mlog backend — the property the ``user_macro``
    equivalence fixture defends, asserted here at the unit level too.

    (The full REPL <-> mlog event-equivalence is pinned by the fixture pair via
    ``tests/integration/test_equivalence.py``; here we just confirm the macro
    expands + prints + emits no memory-cell op.)"""
    src = SQ_DEF + "6 sq PRINT\n"
    mlog_text = _mlog(src)
    assert _mlog_prints(mlog_text) == ["36"]
    opcodes = _opcodes(mlog_text)
    assert "read" not in opcodes and "write" not in opcodes, (
        f"macro-to-message-block program was not cell-free:\n{mlog_text}"
    )
