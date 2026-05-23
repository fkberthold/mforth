"""Unit tests for the mlog backend's static stack-slot allocator.

Bead mforth-10t.15. The allocator consumes the stack-checker's annotated
AST and produces a `SlotMap` keyed by `id(term)` that gives codegen the
exact `s<N>` slot names each term reads from and writes to. No mlog text
is emitted at this layer — that is bead mforth-10t.16.

Slot convention: at any program point where the (frame-local-plus-
entry-frame) stack depth is D, the items live in `s0, s1, ..., s(D-1)`
bottom-to-top. A push at depth D writes `s<D>`; a pop at depth D reads
`s<D-1>`.

Definition bodies start their local depth at 0 but their stack slots
are offset by the definition's `in_arity` so the caller's hand-off
items occupy `s0..s(in_arity-1)` and the body sees them as live.
"""

from __future__ import annotations

import pytest

from mforth.backend.mlog.slots import SlotMap, allocate_slots
from mforth.parse import (
    Begin,
    Definition,
    DoLoop,
    IfThen,
    LitInt,
    LitStr,
    WordCall,
    parse,
)
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def alloc(src: str) -> tuple[SlotMap, object]:
    prog = parse(src, file="<test>")
    result = stackcheck(prog)
    return allocate_slots(result), prog


def main_terms(prog) -> list:
    return prog.main


def def_by_name(prog, name: str) -> Definition:
    for d in prog.definitions:
        if d.name == name:
            return d
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Acceptance: '1 2 + 3 *' produces the expected slot sequence.
# ---------------------------------------------------------------------------


def test_acceptance_arithmetic_pipeline():
    """`1 2 + 3 *` — every step's slots are mechanically determined.

    depth:   0   1   2   1   2   1
    term:    1   2   +   3   *
    state:  [_] [s0][s0 s1][s0][s0 s1][s0]

    `1` writes s0.
    `2` writes s1.
    `+` reads s0, s1; writes s0.
    `3` writes s1.
    `*` reads s0, s1; writes s0.
    """
    sm, prog = alloc("1 2 + 3 *")
    t = main_terms(prog)
    assert len(t) == 5

    # LitInt 1
    assert sm.reads(t[0]) == ()
    assert sm.writes(t[0]) == ("s0",)
    # LitInt 2
    assert sm.reads(t[1]) == ()
    assert sm.writes(t[1]) == ("s1",)
    # +
    assert sm.reads(t[2]) == ("s0", "s1")
    assert sm.writes(t[2]) == ("s0",)
    # LitInt 3
    assert sm.reads(t[3]) == ()
    assert sm.writes(t[3]) == ("s1",)
    # *
    assert sm.reads(t[4]) == ("s0", "s1")
    assert sm.writes(t[4]) == ("s0",)


def test_dup_writes_two_slots():
    """DUP is (1,2): reads s<d-1>, writes s<d-1>, s<d>."""
    sm, prog = alloc("5 DUP")
    t = main_terms(prog)
    # 5 → s0
    assert sm.writes(t[0]) == ("s0",)
    # DUP reads s0, writes s0,s1
    assert sm.reads(t[1]) == ("s0",)
    assert sm.writes(t[1]) == ("s0", "s1")


def test_string_literal_takes_a_slot():
    sm, prog = alloc('S" hello"')
    t = main_terms(prog)
    assert isinstance(t[0], LitStr)
    assert sm.reads(t[0]) == ()
    assert sm.writes(t[0]) == ("s0",)


# ---------------------------------------------------------------------------
# IF / THEN merge — both branches use the same depth at merge point.
# ---------------------------------------------------------------------------


def test_if_then_merge_depth_consistent():
    """`1 2 = IF 10 ELSE 20 THEN .` — branches push into the same slot."""
    sm, prog = alloc("1 2 = IF 10 ELSE 20 THEN .")
    t = main_terms(prog)
    # t[0] LitInt 1 -> s0; t[1] LitInt 2 -> s1; t[2] = reads s0,s1 writes s0
    assert sm.writes(t[0]) == ("s0",)
    assert sm.writes(t[1]) == ("s1",)
    assert sm.reads(t[2]) == ("s0", "s1")
    assert sm.writes(t[2]) == ("s0",)
    # t[3] IfThen — reads the flag at s0, writes nothing immediately
    iff = t[3]
    assert isinstance(iff, IfThen)
    assert sm.reads(iff) == ("s0",)
    assert sm.writes(iff) == ()
    # Inside both branches, depth at branch entry is 0, so LitInts both write s0
    then_lit = iff.then_body[0]
    else_lit = iff.else_body[0]
    assert sm.writes(then_lit) == ("s0",)
    assert sm.writes(else_lit) == ("s0",)
    # `.` after THEN reads s0 (the merged result), writes nothing
    dot = t[4]
    assert sm.reads(dot) == ("s0",)
    assert sm.writes(dot) == ()


def test_nested_if_inside_if():
    # Inner IF/ELSE/THEN keeps branch depths balanced so stackcheck
    # accepts the program; both branches push exactly one slot.
    src = "1 IF 2 IF 3 ELSE 4 THEN . THEN"
    sm, prog = alloc(src)
    outer = main_terms(prog)[1]
    assert isinstance(outer, IfThen)
    # outer flag at s0
    assert sm.reads(outer) == ("s0",)
    inner_lit_2 = outer.then_body[0]  # LitInt 2 inside outer THEN body
    assert sm.writes(inner_lit_2) == ("s0",)
    inner_if = outer.then_body[1]
    assert isinstance(inner_if, IfThen)
    # inner_if reads its flag at s0
    assert sm.reads(inner_if) == ("s0",)
    # innermost LitInt 3 / 4 both write s0 (branch entry depth 0)
    assert sm.writes(inner_if.then_body[0]) == ("s0",)
    assert sm.writes(inner_if.else_body[0]) == ("s0",)
    # `.` after inner THEN consumes s0
    dot = outer.then_body[2]
    assert sm.reads(dot) == ("s0",)


# ---------------------------------------------------------------------------
# DO / LOOP body must be stack-neutral; loop indices live outside slots.
# ---------------------------------------------------------------------------


def test_do_loop_consumes_limit_and_index_slots():
    """`10 0 DO 5 . LOOP` — DO reads 2 slots, body is neutral."""
    sm, prog = alloc("10 0 DO 5 . LOOP")
    t = main_terms(prog)
    # t[0]=10 → s0; t[1]=0 → s1; t[2]=DoLoop reads s0,s1 writes nothing
    assert sm.writes(t[0]) == ("s0",)
    assert sm.writes(t[1]) == ("s1",)
    loop = t[2]
    assert isinstance(loop, DoLoop)
    assert sm.reads(loop) == ("s0", "s1")
    assert sm.writes(loop) == ()
    # Body LitInt 5 enters at depth 0 -> writes s0; '.' reads s0
    body_lit = loop.body[0]
    body_dot = loop.body[1]
    assert sm.writes(body_lit) == ("s0",)
    assert sm.reads(body_dot) == ("s0",)


def test_do_loop_i_index_writes_a_stack_slot():
    """`I` is a builtin (0,1): pushes the current loop counter onto the
    stack. The pushed value occupies a normal `s<N>` slot. The runtime
    loop-counter variable itself is the codegen's problem (.16), not the
    allocator's."""
    sm, prog = alloc("3 0 DO I . LOOP")
    t = main_terms(prog)
    loop = t[2]
    i_call = loop.body[0]
    assert isinstance(i_call, WordCall) and i_call.name.upper() == "I"
    # depth at body entry = 0; I pushes -> writes s0
    assert sm.reads(i_call) == ()
    assert sm.writes(i_call) == ("s0",)
    dot = loop.body[1]
    assert sm.reads(dot) == ("s0",)


# ---------------------------------------------------------------------------
# BEGIN / UNTIL back-edge — body net-pushes a flag, UNTIL consumes it.
# ---------------------------------------------------------------------------


def test_begin_until_back_edge_depth_match():
    """`BEGIN 1 UNTIL` — at the back-edge, the flag sits at s0; UNTIL
    pops it. The Begin container itself reads/writes nothing at its
    program point (the loop has no precondition); its body's terms have
    their own dict entries."""
    sm, prog = alloc("BEGIN 1 UNTIL")
    t = main_terms(prog)
    beg = t[0]
    assert isinstance(beg, Begin) and beg.kind == "until"
    assert sm.reads(beg) == ()
    assert sm.writes(beg) == ()
    # LitInt 1 inside writes s0
    body_lit = beg.body[0]
    assert sm.writes(body_lit) == ("s0",)


def test_begin_while_repeat_test_and_body_neutral():
    """`BEGIN 1 WHILE REPEAT` — test pushes the flag (which WHILE pops),
    body is stack-neutral."""
    sm, prog = alloc("BEGIN 1 WHILE REPEAT")
    t = main_terms(prog)
    beg = t[0]
    assert isinstance(beg, Begin) and beg.kind == "while-repeat"
    # Test body LitInt 1 -> s0
    test_lit = beg.body[0]
    assert sm.writes(test_lit) == ("s0",)


# ---------------------------------------------------------------------------
# User-defined word with non-zero in_arity — slot indices reflect the
# entry-frame depth (callee sees caller's items at s0..s(in_arity-1)).
# ---------------------------------------------------------------------------


def test_user_def_in_arity_offsets_body_slot_indices():
    """`: DOUBLE + ;` has in_arity=2. Inside the body, `+` reads s0,s1
    (the caller-supplied items) and writes s0. The local-body depth at
    `+` is 0, but the allocator must add the entry frame (in_arity=2)
    to find the actual slot indices."""
    src = ": DOUBLE + ; 1 2 DOUBLE"
    sm, prog = alloc(src)
    defn = def_by_name(prog, "DOUBLE")
    plus = defn.body[0]
    assert isinstance(plus, WordCall) and plus.name == "+"
    assert sm.reads(plus) == ("s0", "s1")
    assert sm.writes(plus) == ("s0",)


def test_user_def_with_inputs_and_local_push_uses_offset_slots():
    """`: ADD3 + 3 + ;` — in_arity=2. Walk:
        entry: s0=a, s1=b   (depth=2 frame, local=0)
        +    : reads s0,s1 writes s0   (local depth 0 -> after, local=1, slot s0 live)
        3    : writes s1   (local depth 1 -> slot index = 2 + 1 = ... wait)
    Hmm: local depth_in at `3` is 1 (after `+` reduced from 0 to -1 then... wait).
    Re-derive: stackcheck simulates body with initial_depth=0.
      term 0 `+`: depth_in=0; effect (2,1); after = -2 + 1 = -1.
      term 1 `3`: depth_in=-1; +1 → 0.
      term 2 `+`: depth_in=0; (2,1); after = -2+1 = -1.
    Final depth = -1; in_arity = max(0, -(-2)) = 2; out_arity = depth + in_arity
      = -1 + 2 = 1.  Effect (2,1). Good.
    With entry_depth=2:
      `+` (first): reads s<2+0-2..2+0-1> = s0,s1; writes s<2+0-2> = s0.
      `3`        : writes s<2+(-1)+0>? — actually a push at local depth=-1
                    means stack-relative slot = entry+local = 2 + (-1) = 1 → s1.
      `+` (second): reads s<2+0-2..> = s0,s1; writes s0.
    """
    sm, prog = alloc(": ADD3 + 3 + ; 10 20 ADD3")
    defn = def_by_name(prog, "ADD3")
    plus1, lit3, plus2 = defn.body
    assert sm.reads(plus1) == ("s0", "s1")
    assert sm.writes(plus1) == ("s0",)
    assert sm.writes(lit3) == ("s1",)
    assert sm.reads(plus2) == ("s0", "s1")
    assert sm.writes(plus2) == ("s0",)


def test_user_def_call_site_reads_inputs_writes_outputs():
    """At the call site of DOUBLE (in_arity=2, out_arity=1), the
    WordCall behaves like a builtin of effect (2,1): reads the top two
    slots, writes the bottom one of them. This lets .16 emit the actual
    call without re-analysing user-defs."""
    src = ": DOUBLE + ; 10 20 DOUBLE"
    sm, prog = alloc(src)
    t = main_terms(prog)
    # t[0]=10 -> s0, t[1]=20 -> s1
    call = t[2]
    assert isinstance(call, WordCall) and call.name == "DOUBLE"
    assert sm.reads(call) == ("s0", "s1")
    assert sm.writes(call) == ("s0",)


# ---------------------------------------------------------------------------
# Empty programs / no-op terms degrade gracefully.
# ---------------------------------------------------------------------------


def test_empty_program_yields_empty_slotmap():
    sm, _prog = alloc("")
    # max slot index used should be -1 (no slots) or 0 — assert via API
    assert sm.max_slot_index() == -1


def test_max_slot_index_tracks_widest_frame():
    sm, _prog = alloc("1 2 3 4")
    # depths 0,1,2,3; final depth 4 → slots s0..s3 used
    assert sm.max_slot_index() == 3


def test_max_slot_index_includes_def_body_frames():
    """A def whose body dips into the caller's frame and then pushes
    must reflect both the entry frame *and* the local pushes.

    `: WIDE + 1 2 3 + + ;`
      local depths: + (0,-1), 1 (-1,0), 2 (0,1), 3 (1,2),
                    + (2,1), + (1,0).
      => in_arity=2, out_arity=2.
    With entry_depth=2, the LitInt `3` pushes at absolute slot s3."""
    src = ": WIDE + 1 2 3 + + ; 9 8 WIDE"
    sm, _prog = alloc(src)
    assert sm.max_slot_index() == 3


# ---------------------------------------------------------------------------
# Identity-keyed: two structurally-equal literals in different positions
# get distinct dict entries.
# ---------------------------------------------------------------------------


def test_identical_literals_are_distinct_keys():
    """`1 1 +` — two LitInt(1) terms are equal by value but live at
    different depths. The allocator must key on `id(term)` not on
    structural equality."""
    sm, prog = alloc("1 1 +")
    t = main_terms(prog)
    lit_a, lit_b, plus = t
    assert sm.writes(lit_a) == ("s0",)
    assert sm.writes(lit_b) == ("s1",)
    assert sm.reads(plus) == ("s0", "s1")
    assert sm.writes(plus) == ("s0",)


# ---------------------------------------------------------------------------
# Defensive: unknown term raises.
# ---------------------------------------------------------------------------


def test_query_unknown_term_raises_keyerror():
    sm, _prog = alloc("1 2 +")
    stray = LitInt(value=99, src_loc=__import__("mforth.parse", fromlist=["SrcLoc"]).SrcLoc("x", 1, 1))
    with pytest.raises(KeyError):
        sm.reads(stray)
