"""Unit tests for the mlog backend's emit pass — arithmetic, stack, comparison,
logical, and variable primitives.

Bead mforth-10t.16. The emit pass consumes a `StackcheckResult` plus the
`SlotMap` produced by `mforth.backend.mlog.slots.allocate_slots` and walks
the annotated AST in source order, producing a flat list of `MlogInstr`
3-tuples ``(label, opcode, operands)`` where ``label`` is ``None`` for the
linear instruction stream (label resolution belongs to a later bead).

Out of scope for this bead — and therefore for these tests:

* Control flow (IF/THEN/ELSE, BEGIN/UNTIL, BEGIN/WHILE/REPEAT, DO/LOOP).
  These are bead mforth-10t.17 and intentionally raise from the emitter
  for now (test coverage at the end of this file pins that behaviour).
* Mindustry primitives (PRINT, PRINTFLUSH, WAIT, SENSOR, GETLINK). Filed
  for a later bead — exercise tests pin the NotImplementedError.
* Final mlog text rendering (joining tuples to ``op add s0 s0 s1`` lines).
  Tuples are the wire format; rendering happens at link/serialize time.

Contract pinned here (the M3 RED contract for this bead):

* Literals: ``LitInt(v)`` at write-slot s<i>  →  ``("set", "s<i>", "<v>")``.
* Arithmetic ``+ - * / MOD`` at slots (read=(s_a,s_b), write=(s_c,)) →
  ``("op", "<op>", "s<c>", "s<a>", "s<b>")`` with the mlog opcode being
  the obvious name (``add sub mul div mod``).  Note Forth ``/`` maps
  to mlog ``div`` (FLOAT division) — mforth-dlr (2026-05-23) flipped this
  from ``idiv`` so the mlog backend matches the host REPL primitive
  (which uses Python's ``/``, also float). The convergence restored the
  REPL ↔ mlog equivalence property (CLAUDE.md headline test class) on
  every program using ``/``. Forth tradition prefers integer ``/``;
  mforth's pragmatic dialect chooses Python-natural feel instead.
* Comparison ``= <> < > <= >=`` map to mlog ``equal notEqual lessThan
  greaterThan lessThanEq greaterThanEq``.  mlog returns 0/1; we keep it.
  This is a deliberate dialect choice (NOT Forth's traditional 0/-1) —
  see ``emit.py`` module docstring for the rationale + cross-ref to .11.
* Logical ``AND OR NOT`` map to mlog ``land or not``.  ``not`` is unary —
  takes one read slot and writes the same slot.
* Stack ops:
  ``DUP`` → ``("set", "s<top>", "s<top-1>")``.
  ``DROP`` → no instruction (the slot pointer advances; the slot becomes
  dead).  Encoded as zero output instructions.
  ``OVER`` → ``("set", "s<top>", "s<top-2>")``.
  ``NIP`` → ``("set", "s<bottom>", "s<top>")`` (copies the top into the
  slot below, discarding the original second-from-top).
  ``SWAP`` → 3 instructions via a named scratch ``__swap_tmp``.
  ``ROT`` → 4 instructions via the same scratch.
  ``TUCK`` → 3 instructions (write top into scratch, shift, restore).
* Variables:
  ``VARIABLE foo`` emits NO instructions at the definition site.
  ``foo @`` emits ``("set", "s<i>", "foo")`` — single fused instruction.
  ``value foo !`` emits ``("set", "foo", "s<value-slot>")`` — single
  fused instruction; the address-push is fused away.
* User definition calls: inlined as the body's emitted instructions, with
  slot-name rewriting so the body's ``s<k>`` becomes the caller's
  ``s<entry_depth + k>``.  v1 codegen strategy.

The deeper contract — never violated by these tests — is REPL ↔ mlog
behavioural equivalence (CLAUDE.md hard rule).  Equivalence fixtures
live in tests/integration once the in-repo mlog interpreter ships
(bead mforth-10t.31).  This unit suite pins the syntactic shape of the
emitted tuples and is the precondition for that equivalence work.
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
# Acceptance: the canonical `1 2 +` pipeline.
# ---------------------------------------------------------------------------


def test_acceptance_one_plus_two():
    """`1 2 +` — three instructions, exactly as documented in the design.

    Slot trace (from .15):
      LitInt(1)  writes s0
      LitInt(2)  writes s1
      WordCall(+) reads  s0, s1; writes s0
    """
    instrs = compile_to_tuples("1 2 +")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "2")),
        (None, "op", ("add", "s0", "s0", "s1")),
    ]


def test_acceptance_arithmetic_pipeline():
    """`1 2 + 3 *` — chained binary ops reuse the bottom slot."""
    instrs = compile_to_tuples("1 2 + 3 *")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "2")),
        (None, "op", ("add", "s0", "s0", "s1")),
        (None, "set", ("s1", "3")),
        (None, "op", ("mul", "s0", "s0", "s1")),
    ]


def test_litfloat_lowers_to_set_with_repr():
    """Float-literal lowering (bead mforth-xk7).

    ``LitFloat(0.95)`` at write-slot s<i> emits ``("set", "s<i>",
    "0.95")`` — using Python's ``repr()`` so the operand round-trips
    through mlog's tokenizer cleanly. The slot-form (not the PRINT
    lift) fires when no immediate PRINT follows.
    """
    instrs = compile_to_tuples("0.95 DROP")
    # DROP is a no-op in the emit pass (the slot pointer advances),
    # so only the literal set survives.
    assert instrs == [
        (None, "set", ("s0", "0.95")),
    ]


def test_litfloat_arithmetic_with_int():
    """Mixing float + int literals exercises the LitFloat emit path
    alongside the existing LitInt one."""
    instrs = compile_to_tuples("4 0.25 *")
    assert instrs == [
        (None, "set", ("s0", "4")),
        (None, "set", ("s1", "0.25")),
        (None, "op", ("mul", "s0", "s0", "s1")),
    ]


def test_litfloat_print_lift():
    """The PRINT lift extends to LitFloat — ``0.95 PRINT`` emits a
    single ``print 0.95`` rather than the slot-form ``set s0 0.95;
    print s0``. ``repr()`` is used so the operand matches the slot
    form's lowering verbatim, keeping REPL ↔ mlog event-stream
    equivalence regardless of which path the codegen picks."""
    instrs = compile_to_tuples("0.95 PRINT")
    assert instrs == [
        (None, "print", ("0.95",)),
    ]


# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------


def test_literal_int_emits_single_set():
    instrs = compile_to_tuples("42")
    assert instrs == [(None, "set", ("s0", "42"))]


def test_negative_literal_int():
    instrs = compile_to_tuples("-7")
    assert instrs == [(None, "set", ("s0", "-7"))]


def test_string_literal_emits_set_with_quoted_value():
    """S" hello" pushes a string onto the stack — mlog represents string
    literals inline with quotes; emit puts them in the operand verbatim
    so the serializer can render `set s0 "hello"` correctly."""
    instrs = compile_to_tuples('S" hello"')
    assert instrs == [(None, "set", ("s0", '"hello"'))]


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forth_op,mlog_op",
    [
        ("+", "add"),
        ("-", "sub"),
        ("*", "mul"),
        ("/", "div"),  # mforth-dlr: was "idiv" — flipped to match REPL
        ("MOD", "mod"),
    ],
)
def test_arithmetic_ops_map_to_mlog_opcodes(forth_op, mlog_op):
    instrs = compile_to_tuples(f"3 4 {forth_op}")
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "set", ("s1", "4")),
        (None, "op", (mlog_op, "s0", "s0", "s1")),
    ]


# ---------------------------------------------------------------------------
# Comparison — mlog's 0/1 encoding (deliberate dialect choice; not -1/0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forth_op,mlog_op",
    [
        ("=", "equal"),
        ("<>", "notEqual"),
        ("<", "lessThan"),
        (">", "greaterThan"),
        ("<=", "lessThanEq"),
        (">=", "greaterThanEq"),
    ],
)
def test_comparison_ops_emit_mlog_native_zero_one(forth_op, mlog_op):
    """mlog's `op equal` writes 0 or 1; we keep that encoding rather than
    translating to Forth's traditional 0/-1. See emit.py docstring."""
    instrs = compile_to_tuples(f"3 4 {forth_op}")
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "set", ("s1", "4")),
        (None, "op", (mlog_op, "s0", "s0", "s1")),
    ]


# ---------------------------------------------------------------------------
# Logical
# ---------------------------------------------------------------------------


def test_and_emits_land():
    """Forth `AND` → mlog `land` (logical-and; mlog `and` is bitwise)."""
    instrs = compile_to_tuples("1 0 AND")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "0")),
        (None, "op", ("land", "s0", "s0", "s1")),
    ]


def test_or_emits_or():
    """Forth `OR` → mlog `or`.  mlog's `or` here is the logical-or
    keyword in the `op` instruction; we accept the slight asymmetry
    with `land`/`AND` because that is what the mlog opcode is named."""
    instrs = compile_to_tuples("1 0 OR")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "0")),
        (None, "op", ("or", "s0", "s0", "s1")),
    ]


def test_not_emits_unary_not():
    """NOT is (1, 1) — reads and writes the same slot, second operand
    is the mlog convention of `0` (mlog `op not` is documented as unary
    but takes a placeholder second operand in the 4-arg `op` form)."""
    instrs = compile_to_tuples("1 NOT")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "op", ("not", "s0", "s0", "0")),
    ]


# ---------------------------------------------------------------------------
# Stack operations
# ---------------------------------------------------------------------------


def test_dup_copies_top_to_next_slot():
    instrs = compile_to_tuples("5 DUP")
    assert instrs == [
        (None, "set", ("s0", "5")),
        (None, "set", ("s1", "s0")),
    ]


def test_drop_emits_nothing():
    """DROP is purely a slot-pointer adjustment; no mlog instruction."""
    instrs = compile_to_tuples("5 DROP")
    assert instrs == [(None, "set", ("s0", "5"))]


def test_over_copies_second_to_top():
    instrs = compile_to_tuples("1 2 OVER")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "2")),
        (None, "set", ("s2", "s0")),
    ]


def test_nip_removes_second():
    """NIP: a b -> b.  Copies top into the slot below; the original top
    slot becomes dead (next push will overwrite it)."""
    instrs = compile_to_tuples("1 2 NIP")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "2")),
        (None, "set", ("s0", "s1")),
    ]


def test_swap_uses_named_scratch():
    """SWAP needs one temp.  We use the named variable ``__swap_tmp``
    (rather than reserving a stack slot above max) because mlog has a
    flat global namespace and a single named scratch is auditable and
    distinguishable from real user variables.  The double-underscore
    prefix matches the Python-private convention to signal `do not
    touch`.  Three instructions total: stash top, move bottom to top,
    restore stash into bottom."""
    instrs = compile_to_tuples("1 2 SWAP")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "2")),
        (None, "set", ("__swap_tmp", "s0")),
        (None, "set", ("s0", "s1")),
        (None, "set", ("s1", "__swap_tmp")),
    ]


def test_rot_uses_named_scratch():
    """ROT: a b c -> b c a.  Slots before: s0=a, s1=b, s2=c.
    Slots after:  s0=b, s1=c, s2=a.  Implemented as
        tmp = s0;  s0 = s1;  s1 = s2;  s2 = tmp.
    """
    instrs = compile_to_tuples("1 2 3 ROT")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "2")),
        (None, "set", ("s2", "3")),
        (None, "set", ("__swap_tmp", "s0")),
        (None, "set", ("s0", "s1")),
        (None, "set", ("s1", "s2")),
        (None, "set", ("s2", "__swap_tmp")),
    ]


def test_tuck_uses_named_scratch():
    """TUCK: a b -> b a b.  Slots before: s0=a, s1=b.
    Slots after: s0=b, s1=a, s2=b.  Implemented as
        tmp = s1; s1 = s0; s0 = tmp; s2 = tmp.
    """
    instrs = compile_to_tuples("1 2 TUCK")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "set", ("s1", "2")),
        (None, "set", ("__swap_tmp", "s1")),
        (None, "set", ("s1", "s0")),
        (None, "set", ("s0", "__swap_tmp")),
        (None, "set", ("s2", "__swap_tmp")),
    ]


# ---------------------------------------------------------------------------
# Variables — VARIABLE/@/! fusion
# ---------------------------------------------------------------------------


def test_variable_declaration_emits_nothing():
    """`VARIABLE foo` is a name binding only — mlog variables don't
    require declaration, so no instructions land at this site."""
    instrs = compile_to_tuples("VARIABLE foo")
    assert instrs == []


def test_fetch_fuses_to_single_set():
    """`foo @` compiles to `set s<i> foo`.  The push-of-address WordCall
    that the dictionary nominally produces is fused away — there is no
    addressable value on the stack, ever, in v1.  The mforth name is
    used verbatim as the mlog variable name."""
    instrs = compile_to_tuples("VARIABLE foo  foo @")
    assert instrs == [(None, "set", ("s0", "foo"))]


def test_store_fuses_to_single_set():
    """`42 foo !` compiles to `set foo s0`.  The value sits in s0; the
    address-push is fused away; the `!` writes directly into the named
    mlog variable."""
    instrs = compile_to_tuples("VARIABLE foo  42 foo !")
    assert instrs == [
        (None, "set", ("s0", "42")),
        (None, "set", ("foo", "s0")),
    ]


def test_variable_round_trip():
    """Combined: declare, store, fetch, add."""
    instrs = compile_to_tuples("VARIABLE foo  3 foo !  foo @ 1 +")
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "set", ("foo", "s0")),
        (None, "set", ("s0", "foo")),
        (None, "set", ("s1", "1")),
        (None, "op", ("add", "s0", "s0", "s1")),
    ]


# ---------------------------------------------------------------------------
# User-definition inlining
# ---------------------------------------------------------------------------


def test_user_definition_call_inlines_body():
    """`: square dup * ;  3 square`  expands to:
        set s0 3       \\ push 3
        set s1 s0      \\ dup → s1 = s0
        op mul s0 s0 s1 \\ multiply

    The definition body is inlined at the call site with the slot frame
    rewritten by the caller's entry depth (here the caller is at depth 1
    when the call happens, so the body's local s0 becomes the caller's
    s0 — they coincide because `square` takes one input)."""
    instrs = compile_to_tuples(": square dup * ;  3 square")
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "set", ("s1", "s0")),
        (None, "op", ("mul", "s0", "s0", "s1")),
    ]


def test_user_definition_inlining_offsets_slots():
    """When the caller's depth at the call site exceeds the callee's
    in_arity, the body's slot names need offsetting.

    `: inc 1 + ;` has in_arity 1, body uses local s0 (input) and pushes
    s1 (the literal 1), then `+` reads s0,s1 writes s0.

    Call with extra below: `5 7 inc` — at the call site depth is 2; the
    body's local-frame s0 maps to caller's s1, local s1 maps to caller's
    s2, etc.  Emitted:
        set s0 5
        set s1 7
        set s2 1     \\ body's local-s1
        op add s1 s1 s2  \\ body's `+` reads local-s0,local-s1 → s1,s2; writes local-s0 → s1
    """
    instrs = compile_to_tuples(": inc 1 + ;  5 7 inc")
    assert instrs == [
        (None, "set", ("s0", "5")),
        (None, "set", ("s1", "7")),
        (None, "set", ("s2", "1")),
        (None, "op", ("add", "s1", "s1", "s2")),
    ]


# ---------------------------------------------------------------------------
# Negative cases — things this bead deliberately doesn't support
# ---------------------------------------------------------------------------


def test_control_flow_is_supported_by_bead_17():
    """Bead .17 added IF/ELSE/THEN, BEGIN/UNTIL, BEGIN/WHILE/REPEAT,
    DO/LOOP emission.  This sanity check confirms a balanced IF/ELSE/THEN
    no longer raises (the historical placeholder behaviour at .16
    pinned a NotImplementedError; that's now obsolete).  Full coverage
    of the control-flow shapes lives in ``test_emit_control.py``."""
    instrs = compile_to_tuples("1 IF 2 ELSE 3 THEN")
    # Smoke check: at least one jump and one label-bearing tuple emit.
    assert any(opcode == "jump" for (_, opcode, _) in instrs if opcode is not None)
    assert any(label is not None for (label, _, _) in instrs)


def test_mindustry_primitives_now_supported_smoke_check():
    """As of bead .18, the five v1 Mindustry primitives (PRINT,
    PRINTFLUSH, WAIT, SENSOR, GETLINK) emit real instructions.  This
    smoke check confirms `42 PRINT` no longer raises and produces a
    `print` instruction; full contract coverage lives in
    `test_emit_mindustry.py`.

    The IO printing word `.` remains deferred — see
    `test_dot_io_still_deferred` in `test_emit_mindustry.py`."""
    instrs = compile_to_tuples("42 PRINT")
    assert any(opcode == "print" for (_, opcode, _) in instrs if opcode is not None)


def test_loose_variable_address_on_stack_is_rejected():
    """In v1 there is no addressable cell — you can't push a variable
    address and operate on it later.  Detected at emit time so the user
    gets a clear error rather than a malformed mlog program."""
    with pytest.raises(NotImplementedError, match="VARIABLE|address"):
        compile_to_tuples("VARIABLE foo  foo dup @")


# ---------------------------------------------------------------------------
# MlogInstr shape sanity
# ---------------------------------------------------------------------------


def test_mloginstr_is_three_tuple():
    """Every emitted instruction is a 3-tuple ``(label, opcode, operands)``
    with label currently always ``None`` (label resolution belongs to a
    later bead) and operands a tuple of strings."""
    instrs = compile_to_tuples("1 2 +")
    for instr in instrs:
        assert isinstance(instr, tuple)
        assert len(instr) == 3
        label, opcode, operands = instr
        assert label is None
        assert isinstance(opcode, str)
        assert isinstance(operands, tuple)
        for operand in operands:
            assert isinstance(operand, str)
