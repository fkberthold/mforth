"""Unit tests for the mlog backend's emit pass — Mindustry primitives.

Bead mforth-10t.18. Extends the `(label, opcode, operands)` wire format
established by bead .16 (arith/stack) and .17 (control flow) with
emission arms for the five v1 Mindustry primitives:

    PRINT       ( str -- )            → ``print <operand>``
    PRINTFLUSH  ( block -- )          → ``printflush <operand>``
    WAIT        ( seconds -- )        → ``wait s<i-1>``
    SENSOR      ( block prop -- val ) → ``sensor s<i-2> <block> <prop>``
    GETLINK     ( i -- block )        → ``getlink s<i-1> s<i-1>``

THE LITERAL-LIFTING CONTRACT (load-bearing for .19)
====================================================

The bead spec asks for *bare* operands where the values are known at
compile time:

* ``S" hello" PRINT``    → ``print "hello"``  (the LitStr is lifted
  into the operand directly; the otherwise-required ``set s<i> "hello"``
  is elided)
* ``S" message1" PRINTFLUSH`` → ``printflush message1``  (LitStr value
  is lifted; outer quotes stripped because mlog block handles are bare
  identifiers, not strings)
* ``S" message1" S" @copper" SENSOR`` → ``sensor s<i-2> message1 @copper``
  (both LitStr values lifted with quotes stripped — @-property names
  and block handles are bare in mlog)

When the operand is NOT a preceding LitStr (i.e., the value was
computed by some earlier instruction and lives in a slot), emit the
slot reference instead. Bead .19 (final pass: label resolution +
sidecar substitution) is responsible for the sidecar→bare-name
substitution; THIS bead simply emits whatever literal is at hand at
compile time, with quote-stripping for the positions that mlog requires
to be bare.

WAIT and GETLINK never lift — they always emit slot references because
their operands are intrinsically runtime values (a seconds count, a
link index). GETLINK in particular reuses the index slot as the output
handle slot (the slot allocator's read-then-write same-slot pattern,
matching NOT).

CROSS-BEAD CONTRACT WITH .12 (host primitives)
==============================================

The host backend (bead .12) emits these observable events for the same
source programs:

    PRINT       → MessagePrintEvent(text)
    PRINTFLUSH  → MessagePrintflushEvent(block_name, buffer)
    WAIT        → WaitEvent(seconds) + events.tick advance
    SENSOR      → SensorReadEvent(block, prop, value) + push value
    GETLINK     → in-range: LinkResolvedEvent(index, block_name) + push name
                  out-of-range: NO event + push None (mlog null equivalent)

When the in-repo mlog interpreter (bead .31) executes the tuples emitted
here, the observable event sequence must match the host backend's. This
test file pins the syntactic shape; .31 will verify the behavioural
equivalence. The GETLINK out-of-range semantics are NATIVE to mlog
(`getlink` returns null past `@links`), so the emitter needs no bounds
check — see test_getlink_out_of_range_is_native_mlog_semantics for the
documented assertion that we rely on mlog's native behaviour rather
than emitting a guard.

CROSS-BEAD CONTRACT WITH .19 (sidecar substitution)
====================================================

The bead spec uses ``display PRINTFLUSH`` style notation in the v1 demo
fixtures (counter.fs, blink.fs, getlink_index_mode.fs) — the bare
identifier ``display`` is a sidecar-link reference that the resolver
will eventually turn into a ``LinkRef`` Term. THIS bead does NOT
implement that resolver path; the fixtures using it are XFAIL in
tests/golden/test_golden.py until .19 ships.

For now, fixtures use ``S" message1" PRINTFLUSH`` (string-literal block
names that the lifting pass strips quotes from). When .19 lands, the
resolver should synthesize a LinkRef Term that the emit pass treats
identically to the LitStr-lifting path here. The handoff to .19 is
documented in the .18 ship drawer.

Out of scope for this bead (and these tests):

* The IO printing word ``.`` (``( n -- )``) — still raises
  ``NotImplementedError`` per .16's deferral list. Filed for a future
  bead.
* Resolver synthesis of LinkRef Terms from bare-identifier sidecar
  references — bead .19's scope.
* The getlink prologue that index-mode sidecar bindings will need —
  also .19's scope.
"""

from __future__ import annotations

import pytest

from mforth.backend.mlog.emit import MlogInstr, emit
from mforth.backend.mlog.slots import allocate_slots
from mforth.parse import parse
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compile_to_tuples(src: str) -> list[MlogInstr]:
    """Run lex → parse → resolve → stackcheck → slot-alloc → emit."""
    prog = parse(src, file="<test>")
    result = stackcheck(prog)
    sm = allocate_slots(result)
    return emit(result, sm)


# ---------------------------------------------------------------------------
# Acceptance shape — the bead's stated mappings
# ---------------------------------------------------------------------------


def test_acceptance_print_literal_lifts_into_operand():
    """`S" hello" PRINT` — the LitStr value is lifted into the operand
    of `print`; the otherwise-emitted `set s0 "hello"` is elided."""
    instrs = compile_to_tuples('S" hello" PRINT')
    assert instrs == [
        (None, "print", ('"hello"',)),
    ]


def test_acceptance_printflush_literal_strips_quotes_and_lifts():
    """`S" message1" PRINTFLUSH` — the LitStr value's surrounding quotes
    are stripped (mlog block handles are bare identifiers) and the bare
    name becomes the operand."""
    instrs = compile_to_tuples('S" message1" PRINTFLUSH')
    assert instrs == [
        (None, "printflush", ("message1",)),
    ]


def test_acceptance_wait_emits_slot_reference():
    """`5 WAIT` — seconds is a runtime value; emit slot reference."""
    instrs = compile_to_tuples("5 WAIT")
    assert instrs == [
        (None, "set", ("s0", "5")),
        (None, "wait", ("s0",)),
    ]


def test_acceptance_sensor_lifts_block_and_prop_literals():
    """`S" message1" S" @copper" SENSOR` — both LitStrs lifted with
    quotes stripped; sensor writes into the block-slot (the slot
    allocator gave SENSOR reads (s0, s1) and writes (s0,) per its
    (2, 1) stack effect)."""
    instrs = compile_to_tuples('S" message1" S" @copper" SENSOR')
    assert instrs == [
        (None, "sensor", ("s0", "message1", "@copper")),
    ]


def test_acceptance_getlink_emits_same_slot_in_and_out():
    """`0 GETLINK` — index is a runtime value; mlog getlink writes the
    block handle into a result slot reading the index from another.
    The slot allocator gave GETLINK reads (s0,) and writes (s0,) per
    its (1, 1) effect (same slot — `i` becomes `block_handle`)."""
    instrs = compile_to_tuples("0 GETLINK")
    assert instrs == [
        (None, "set", ("s0", "0")),
        (None, "getlink", ("s0", "s0")),
    ]


# ---------------------------------------------------------------------------
# PRINT — literal lifting and fallback
# ---------------------------------------------------------------------------


def test_print_literal_lifting_does_not_emit_set():
    """The LitStr that immediately precedes PRINT must not produce a
    `set` instruction — it is consumed by the lifting pass."""
    instrs = compile_to_tuples('S" hi" PRINT')
    assert not any(t[1] == "set" for t in instrs)
    assert instrs[0][1] == "print"


def test_print_runtime_value_falls_back_to_slot_reference():
    """If PRINT's operand was NOT a preceding LitStr — e.g., it came
    from a fetched VARIABLE — emit `print s<i-1>`, leaving the upstream
    `set` in place. This is the .19-substitution target shape for
    computed-then-printed values."""
    instrs = compile_to_tuples("VARIABLE n  42 n !  n @ PRINT")
    # `42` pushes to s0; `n !` fuses to VarRef(store) reading s0;
    # `n @` fuses to VarRef(fetch) writing s0; PRINT has VarRef
    # (not LitStr/LitInt) immediately before it so no lift fires —
    # PRINT emits the slot-reference form `print s0`.
    assert instrs == [
        (None, "set", ("s0", "42")),
        (None, "set", ("n", "s0")),
        (None, "set", ("s0", "n")),
        (None, "print", ("s0",)),
    ]


def test_print_with_numeric_literal_lifts_value():
    """`42 PRINT` — numeric literal is lifted as a bare number operand
    (no `set` for the literal). Matches the host backend's
    PRINT-coerces-numerics-via-str behaviour (.12 ship drawer §6)."""
    instrs = compile_to_tuples("42 PRINT")
    assert instrs == [
        (None, "print", ("42",)),
    ]


# ---------------------------------------------------------------------------
# PRINTFLUSH — literal lifting and fallback
# ---------------------------------------------------------------------------


def test_printflush_strips_outer_quotes_from_litstr():
    """The LitStr's value is `message1` (no quotes); mlog needs bare
    identifiers in block-handle position. The lifting pass emits the
    bare name."""
    instrs = compile_to_tuples('S" message1" PRINTFLUSH')
    operands = instrs[0][2]
    assert operands == ("message1",)
    # Sanity: no quotes survive.
    assert '"' not in operands[0]


def test_printflush_runtime_value_falls_back_to_slot_reference():
    """If PRINTFLUSH consumes a non-LitStr value (rare in practice, but
    structurally possible) — fall back to slot reference for the
    .19-substitution target shape."""
    # Construct via a user definition that returns a string the caller
    # consumes; here we keep it simple via a fetched VARIABLE.
    instrs = compile_to_tuples('VARIABLE blk  S" message1" blk !  blk @ PRINTFLUSH')
    # The LitStr precedes a VarRef (not PRINTFLUSH), so no lift fires —
    # `S" message1"` emits `set s0 "message1"`. Then VarRef(store, blk)
    # reads s0 → `set blk s0`. Then VarRef(fetch, blk) writes s0 →
    # `set s0 blk`. Then PRINTFLUSH on the slot reference → `printflush s0`.
    assert instrs == [
        (None, "set", ("s0", '"message1"')),
        (None, "set", ("blk", "s0")),
        (None, "set", ("s0", "blk")),
        (None, "printflush", ("s0",)),
    ]


# ---------------------------------------------------------------------------
# WAIT — always slot reference
# ---------------------------------------------------------------------------


def test_wait_with_integer_literal_keeps_set_emission():
    """WAIT never lifts — the seconds value is intrinsically runtime
    (even when sourced from a literal, the literal-set is left in place
    because mlog `wait` accepts any variable but readers benefit from
    the slot being explicit). This matches the bead's stated emission:
    `wait s<i-1>`."""
    instrs = compile_to_tuples("3 WAIT")
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "wait", ("s0",)),
    ]


def test_wait_with_computed_value_uses_slot_reference():
    """`2 3 + WAIT` — the wait duration is a computed value at s0
    (after the `op add` reuses the bottom slot)."""
    instrs = compile_to_tuples("2 3 + WAIT")
    assert instrs == [
        (None, "set", ("s0", "2")),
        (None, "set", ("s1", "3")),
        (None, "op", ("add", "s0", "s0", "s1")),
        (None, "wait", ("s0",)),
    ]


# ---------------------------------------------------------------------------
# SENSOR — two-literal fusion
# ---------------------------------------------------------------------------


def test_sensor_lifts_both_block_and_prop_literals():
    """Both immediate-preceding LitStrs are lifted, quotes stripped.
    No `set` instructions for either literal."""
    instrs = compile_to_tuples('S" message1" S" @copper" SENSOR')
    assert instrs == [
        (None, "sensor", ("s0", "message1", "@copper")),
    ]


def test_sensor_only_prop_literal_falls_back_for_block():
    """If only the prop is a literal (block was computed/fetched), the
    block becomes a slot reference but the prop still lifts. Mixed-mode
    coverage."""
    instrs = compile_to_tuples('VARIABLE blk  S" message1" blk !  blk @ S" @copper" SENSOR')
    # S" message1" emits set s0 "message1" (no lift — VarRef follows).
    # blk! → set blk s0.
    # blk@ → set s0 blk.
    # S" @copper" lifts into SENSOR via the 2-term `LitStr SENSOR`
    # matcher (only prop lifts; block comes from the slot the previous
    # term wrote into).
    # Result: sensor s0 s0 @copper (write into block-slot per (2,1) effect).
    assert instrs == [
        (None, "set", ("s0", '"message1"')),
        (None, "set", ("blk", "s0")),
        (None, "set", ("s0", "blk")),
        (None, "sensor", ("s0", "s0", "@copper")),
    ]


def test_sensor_neither_literal_uses_pure_slot_references():
    """Both block and prop are runtime — fall back to pure slot
    references. The .19-substitution target shape for fully-computed
    sensor reads."""
    instrs = compile_to_tuples(
        'VARIABLE blk  VARIABLE prop  '
        'S" message1" blk !  S" @copper" prop !  '
        'blk @ prop @ SENSOR'
    )
    # Each LitStr precedes a VarRef (not a Mindustry primitive), so
    # neither lifts. Pattern repeats: literal → s0/s1, store → named var.
    # Then two fetches reload, then SENSOR pulls from slots (no lift —
    # the term immediately before SENSOR is a VarRef, not a LitStr).
    assert instrs == [
        (None, "set", ("s0", '"message1"')),
        (None, "set", ("blk", "s0")),
        (None, "set", ("s0", '"@copper"')),
        (None, "set", ("prop", "s0")),
        (None, "set", ("s0", "blk")),
        (None, "set", ("s1", "prop")),
        (None, "sensor", ("s0", "s0", "s1")),
    ]


# ---------------------------------------------------------------------------
# GETLINK — always slot reference; same slot in and out
# ---------------------------------------------------------------------------


def test_getlink_with_literal_index_keeps_set():
    """GETLINK never lifts — the index is intrinsically runtime. The
    output handle goes into the SAME slot as the index (the slot
    allocator's (1, 1) same-slot-overwrite pattern, like NOT)."""
    instrs = compile_to_tuples("3 GETLINK")
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "getlink", ("s0", "s0")),
    ]


def test_getlink_with_computed_index_uses_slot_reference():
    """`1 2 + GETLINK` — index is computed at s0; getlink writes the
    handle back into s0."""
    instrs = compile_to_tuples("1 2 + GETLINK")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "2")),
        (None, "op", ("add", "s0", "s0", "s1")),
        (None, "getlink", ("s0", "s0")),
    ]


def test_getlink_out_of_range_is_native_mlog_semantics():
    """The bead's stated contract: out-of-range GETLINK in mlog returns
    `null` natively; the emitter does NOT add a bounds check. This
    matches the host primitive's `push None + no event` behaviour
    (.12 ship drawer §2). The .31 equivalence harness will verify the
    behavioural match; this test pins the lack-of-guard at the emitter
    level."""
    instrs = compile_to_tuples("99 GETLINK")
    # Exactly two instructions: set + getlink. No bounds-check op.
    assert len(instrs) == 2
    opcodes = [t[1] for t in instrs]
    assert opcodes == ["set", "getlink"]
    # No comparison or jump instructions (no bounds guard).
    assert not any(t[1] in {"op", "jump"} for t in instrs)


# ---------------------------------------------------------------------------
# Composed programs — the v1 demos in miniature
# ---------------------------------------------------------------------------


def test_print_then_printflush_canonical_acceptance():
    """The bead's literal acceptance shape: `S" hello" PRINT  S" message1"
    PRINTFLUSH`. Mirrors `tests/unit/test_mindustry_primitives.py::
    test_integration_acceptance_print_hello_printflush_message1`."""
    instrs = compile_to_tuples('S" hello" PRINT  S" message1" PRINTFLUSH')
    assert instrs == [
        (None, "print", ('"hello"',)),
        (None, "printflush", ("message1",)),
    ]


def test_wait_between_prints_preserves_order():
    """`S" a" PRINT  2 WAIT  S" b" PRINT` — the WAIT is between, with
    its own set+wait pair. Mirrors the host backend's
    test_integration_wait_between_prints_advances_timeline."""
    instrs = compile_to_tuples('S" a" PRINT  2 WAIT  S" b" PRINT')
    assert instrs == [
        (None, "print", ('"a"',)),
        (None, "set", ("s0", "2")),
        (None, "wait", ("s0",)),
        (None, "print", ('"b"',)),
    ]


def test_getlink_then_sensor_chain():
    """`0 GETLINK S" @copper" SENSOR` — link 0 lookup then sensor on the
    resulting block handle. Block operand is the slot (runtime); prop
    lifts. The bead's stated `sensor s<i-2> <block> <prop>` shape
    when block is a slot."""
    instrs = compile_to_tuples('0 GETLINK S" @copper" SENSOR')
    # 0 → set s0 0
    # GETLINK → getlink s0 s0  (handle in s0)
    # S" @copper" → lifts into SENSOR (immediately precedes it).
    # SENSOR with reads (s0=block, s1=prop), writes (s0,) → sensor s0 s0 @copper
    assert instrs == [
        (None, "set", ("s0", "0")),
        (None, "getlink", ("s0", "s0")),
        (None, "sensor", ("s0", "s0", "@copper")),
    ]


# ---------------------------------------------------------------------------
# Inside control flow — lifting must respect label queueing (cross-bead .17)
# ---------------------------------------------------------------------------


def test_print_inside_if_then_attaches_labels_correctly():
    """`1 IF S" hi" PRINT THEN` — the print inside the THEN body must
    use the label-queue plumbing (self._emit, not out.append) so any
    trailing labels attach correctly. This is the .17 cross-bead
    contract for non-control-flow emit sites."""
    instrs = compile_to_tuples('1 IF S" hi" PRINT THEN')
    # Expected:
    #   set s0 1
    #   jump L_if_0_end equal s0 0
    #   print "hi"
    #   L_if_0_end:  (sentinel — no instruction after THEN)
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "jump", ("L_if_0_end", "equal", "s0", "0")),
        (None, "print", ('"hi"',)),
        ("L_if_0_end", None, None),
    ]


def test_wait_in_loop_body_works():
    """`3 0 DO 1 WAIT LOOP` — WAIT inside a DO/LOOP body. The loop's
    counter variable plumbing is unaffected by primitive emission."""
    instrs = compile_to_tuples("3 0 DO 1 WAIT LOOP")
    # Slot trace:
    #   3 → set s0 3 (limit)
    #   0 → set s1 0 (index)
    #   DO → set __do_idx_0 s1; set __do_limit_0 s0
    #   L_do_0_top:
    #     jump L_do_0_end greaterThanEq __do_idx_0 __do_limit_0  (zero-trip guard)
    #     1 → set s0 1  (after DO, depth went 2→0, then 0→1 for the literal)
    #     WAIT → wait s0
    #   op add __do_idx_0 __do_idx_0 1
    #   jump L_do_0_top always 0 0
    #   L_do_0_end:
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "set", ("s1", "0")),
        (None, "set", ("__do_idx_0", "s1")),
        (None, "set", ("__do_limit_0", "s0")),
        ("L_do_0_top", "jump", ("L_do_0_end", "greaterThanEq", "__do_idx_0", "__do_limit_0")),
        (None, "set", ("s0", "1")),
        (None, "wait", ("s0",)),
        (None, "op", ("add", "__do_idx_0", "__do_idx_0", "1")),
        (None, "jump", ("L_do_0_top", "always", "0", "0")),
        ("L_do_0_end", None, None),
    ]


# ---------------------------------------------------------------------------
# Negative cases — out-of-context use, malformed patterns
# ---------------------------------------------------------------------------


def test_dot_io_now_lowers_to_print_slot_form():
    """The IO printing word `.` was deferred by .16 (raised
    NotImplementedError); bead mforth-va2 implements it. `42 .` now
    lowers to a `print s<i-1>` slot-form instruction (NOT an error),
    producing the same MessagePrintEvent the host REPL's `.` emits.
    The full lowering shapes are pinned in the `.`-section tests near
    the end of this module."""
    instrs = compile_to_tuples("42 .")
    assert instrs == [
        (None, "set", ("s0", "42")),
        (None, "print", ("s0",)),
    ]


def test_print_with_empty_stack_after_lift_is_stack_underflow():
    """If a stray `PRINT` had nothing in front of it, the stackcheck
    pass would catch the underflow before emit ever runs. This is
    structural — the test exists to document the boundary."""
    with pytest.raises(Exception):
        compile_to_tuples("PRINT")


# ---------------------------------------------------------------------------
# Wire-shape regression: tuples are consistent with .29 serializer
# ---------------------------------------------------------------------------


def test_emitted_tuples_match_serializer_format_contract():
    """The .29 golden harness's `_serialize` joins operands with single
    spaces. Every emit here must produce tuples whose operands are all
    strings (not other types). Pin this for the five Mindustry primitives
    to catch a regression where an operand slips through as int or None."""
    instrs = compile_to_tuples(
        'S" hello" PRINT  '
        'S" message1" PRINTFLUSH  '
        '2 WAIT  '
        'S" message1" S" @copper" SENSOR  '
        '0 GETLINK'
    )
    for label, opcode, operands in instrs:
        assert label is None or isinstance(label, str)
        assert opcode is None or isinstance(opcode, str)
        if operands is not None:
            assert isinstance(operands, tuple)
            for op in operands:
                assert isinstance(op, str), (
                    f"non-string operand {op!r} in {opcode} {operands}"
                )


# ---------------------------------------------------------------------------
# Stackcheck contract — the five primitives are recognised with the right
# stack effects (cross-bead with the dictionary)
# ---------------------------------------------------------------------------


def test_print_stack_effect_one_zero():
    """PRINT consumes one and produces zero — stackcheck must not
    raise on the canonical PRINT shape."""
    prog = parse('S" hi" PRINT', file="<t>")
    result = stackcheck(prog)
    assert result.program is not None


def test_printflush_stack_effect_one_zero():
    prog = parse('S" m" PRINTFLUSH', file="<t>")
    result = stackcheck(prog)
    assert result.program is not None


def test_sensor_stack_effect_two_one():
    """SENSOR consumes two, produces one — net -1. Stackcheck must
    not raise on the canonical shape."""
    prog = parse('S" m" S" @c" SENSOR', file="<t>")
    result = stackcheck(prog)
    assert result.program is not None


def test_getlink_stack_effect_one_one():
    prog = parse('0 GETLINK', file="<t>")
    result = stackcheck(prog)
    assert result.program is not None


# ---------------------------------------------------------------------------
# `.` (pop-and-print) — bead mforth-va2
#
# Standard Forth `.` is ( n -- ): pop the top of the data stack and print
# it. The host REPL primitive (`backend/primitives.py::_dot`) funnels the
# popped value through `world.print(...)`, emitting a MessagePrintEvent —
# the SAME observable the mlog `print` instruction produces. So `.` lowers
# to the slot-reference form `print s<i-1>`, mirroring PRINT's slot-form
# fallback but for a numeric stack value (no string-literal lift). The
# interpreter's `_format_for_print` already matches the host `.` formatting
# (integer-valued floats render WITHOUT a trailing `.0`), so no interp
# change is needed — equivalence holds by construction.
# ---------------------------------------------------------------------------


def test_dot_lowers_to_print_slot_form():
    """`5 .` — the literal lands in s0, then `.` emits `print s0`
    (pop-and-print of the numeric stack value). Mirrors `5 WAIT`'s
    slot-reference shape but with the `print` opcode."""
    instrs = compile_to_tuples("5 .")
    assert instrs == [
        (None, "set", ("s0", "5")),
        (None, "print", ("s0",)),
    ]


def test_dot_after_arithmetic_prints_result_slot():
    """`7 3 - .` — the subtraction writes its result into s0, then `.`
    prints that slot. No literal-lift for `.`: the printed value is
    always the runtime top-of-stack."""
    instrs = compile_to_tuples("7 3 - .")
    assert instrs == [
        (None, "set", ("s0", "7")),
        (None, "set", ("s1", "3")),
        (None, "op", ("sub", "s0", "s0", "s1")),
        (None, "print", ("s0",)),
    ]


def test_dot_stack_effect_one_zero():
    """`.` consumes one and produces zero — stackcheck must not raise on
    the canonical pop-and-print shape, and the emitter must no longer
    defer it (was NotImplementedError before bead mforth-va2)."""
    prog = parse("5 .", file="<t>")
    result = stackcheck(prog)
    assert result.program is not None
    sm = allocate_slots(result)
    # Must not raise NotImplementedError.
    instrs = emit(result, sm)
    assert (None, "print", ("s0",)) in instrs
