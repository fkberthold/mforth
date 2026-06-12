"""Unit tests for the mlog backend's emit pass — control flow.

Bead mforth-10t.17.  Extends the `(label, opcode, operands)` wire format
established by bead .16 with symbolic-label support and adds emission
arms for `IfThen`, `Begin(kind="until")`, `Begin(kind="while-repeat")`,
and `DoLoop`.

Contract pinned here (the M3 RED contract for this bead)
========================================================

**Label encoding.**  A label is the *first* element of a tuple, attached
to the instruction immediately following the label position.  When a
label has no following instruction in the same body (a trailing label
at end of block), the emitter emits a sentinel tuple
``(label_name, None, None)`` so bead .19 (line-resolution) can still
locate it.  Adjacent labels at the same line stack into a sentinel
tuple per extra label, in source order.

**Counter naming.**  Each control-flow construct has its own monotonic
counter on the emitter:

* `IfThen`: ``L_if_<N>_else``, ``L_if_<N>_end``
* `Begin(kind="until")`: ``L_begin_<N>_top``
* `Begin(kind="while-repeat")`: ``L_begin_<N>_top``, ``L_begin_<N>_after``
* `DoLoop`: ``L_do_<N>_top``  (no `_after` — fallthrough)

The counters increment per construct kind in source order across the
entire program (definitions emit in line at call sites, so nested
inlines push their counter values too).

**Jump targets.**  ``jump`` instructions use the label name verbatim as
the first operand.  Bead .19 rewrites the label to an absolute
0-indexed line number; we emit
``(None, "jump", ("L_if_3_else", "equal", "s0", "0"))`` and never touch
line numbers at this layer.

**IF/THEN.**  Stackcheck records the flag at the slot just below the
branches' shared base.  Emit:

* No else body:
  ```
  jump L_if_N_end equal s<flag> 0
  <then>
  L_if_N_end:
  ```
* With else body:
  ```
  jump L_if_N_else equal s<flag> 0
  <then>
  jump L_if_N_end always 0 0
  L_if_N_else:
  <else>
  L_if_N_end:
  ```
  (`always 0 0` is mlog's idiom for unconditional jump.)

**BEGIN/UNTIL.**  Body net-produces one flag (stackcheck enforces this).
The flag lives at slot ``s<depth_in(begin)>``.  Emit:

```
L_begin_N_top:
<body>
jump L_begin_N_top equal s<flag> 0
```

(UNTIL continues while flag == 0, exits when flag != 0 — Forth
tradition. The host backend's `_run_terms` confirms this semantics.)

**BEGIN/WHILE/REPEAT.**  `body` is the test (pushes flag);
`cond_body` is the loop body (stack-neutral).  Emit:

```
L_begin_N_top:
<test>
jump L_begin_N_after equal s<flag> 0
<cond_body>
jump L_begin_N_top always 0 0
L_begin_N_after:
```

**DO/LOOP.**  Forth ANS: `( limit index -- )` with index on top.  We use
named mlog variables `__do_idx_<N>` and `__do_limit_<N>` for each DO
instance.  The `<N>` suffix is the same counter used for the
`L_do_<N>_top` label, so nested DO/LOOP cannot collide.  Emit:

```
set __do_idx_N s<index_slot>
set __do_limit_N s<limit_slot>
L_do_N_top:
jump L_do_N_end greaterThanEq __do_idx_N __do_limit_N
<body>
op add __do_idx_N __do_idx_N 1
jump L_do_N_top always 0 0
L_do_N_end:
```

The bounds test is at the TOP of the loop (a zero-trip guard), matching
the host REPL's ``while: if idx >= limit: break``. A ``limit start DO``
with ``start >= limit`` runs the body ZERO times. The earlier
bottom-test (do-while) shape ran the body once regardless, diverging
from the REPL on every zero-trip loop — fixed under bead mforth-2p8
(generative equivalence harness found ``0 0 DO ... LOOP``).

The loop counter words `I` and `J` are no longer deferred; they
resolve to the innermost / next-out DO's `__do_idx_<N>` slot
respectively.  Emission is a single `set s<write> __do_idx_<N>`.

Out of scope for this bead (and these tests):
* Label resolution to absolute line numbers — bead `.19`.
* Mindustry primitives (PRINT, PRINTFLUSH, ...) — bead `.18`.
* The behavioural REPL ↔ mlog equivalence claim — pinned at `.31` when
  the in-repo mlog interpreter ships.

The deeper contract — never violated by these tests — is REPL ↔ mlog
behavioural equivalence (CLAUDE.md hard rule).  These unit tests pin
the *syntactic shape* of the emitted tuples; the behavioural claim is
hereditary on `.31` landing.
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
# IF / THEN — no else branch
# ---------------------------------------------------------------------------


def test_if_then_no_else_stack_neutral_body():
    """`1 IF 42 DROP THEN` — push a true flag, stack-neutral then-body
    (pushes 42 then drops it), no else-branch.  Stackcheck requires
    same depth on both arms; an empty else implies the then-body must
    be stack-neutral.

    Slot trace:
      LitInt(1)  writes s0          (depth 0 → 1)
      IfThen     reads s0 as flag   (depth 1 → 0 in branch bodies)
      LitInt(42) writes s0 in then  (depth 0 → 1)
      DROP                          (depth 1 → 0)
    """
    instrs = compile_to_tuples("1 IF 42 DROP THEN")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "jump", ("L_if_0_end", "equal", "s0", "0")),
        (None, "set", ("s0", "42")),
        ("L_if_0_end", None, None),
    ]


def test_if_then_no_else_with_op_in_body():
    """The flag is consumed before the body executes; then-body must
    be stack-neutral when there's no else."""
    instrs = compile_to_tuples("5 3 < IF 99 DROP THEN")
    # 5 → s0; 3 → s1; < reads s0 s1, writes s0; IF reads s0 (flag);
    # 99 → s0 in then-body; DROP drops s0 (no instruction).
    assert instrs == [
        (None, "set", ("s0", "5")),
        (None, "set", ("s1", "3")),
        (None, "op", ("lessThan", "s0", "s0", "s1")),
        (None, "jump", ("L_if_0_end", "equal", "s0", "0")),
        (None, "set", ("s0", "99")),
        ("L_if_0_end", None, None),
    ]


# ---------------------------------------------------------------------------
# IF / ELSE / THEN
# ---------------------------------------------------------------------------


def test_if_else_then_simple():
    """`1 IF 42 ELSE 7 THEN` — both arms write s0 at the same depth."""
    instrs = compile_to_tuples("1 IF 42 ELSE 7 THEN")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "jump", ("L_if_0_else", "equal", "s0", "0")),
        (None, "set", ("s0", "42")),
        (None, "jump", ("L_if_0_end", "always", "0", "0")),
        ("L_if_0_else", "set", ("s0", "7")),
        ("L_if_0_end", None, None),
    ]


def test_nested_if_inside_then_branch():
    """Nested IF/THEN — counters are per-construct-kind and monotonic
    across the whole program.  Inner IF gets counter 1 because outer
    consumes counter 0.  Both inner and outer bodies are stack-neutral
    (the inner pushes 42 and DROPs it; the outer body wraps that)."""
    instrs = compile_to_tuples("1 IF 1 IF 42 DROP THEN THEN")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "jump", ("L_if_0_end", "equal", "s0", "0")),
        (None, "set", ("s0", "1")),
        (None, "jump", ("L_if_1_end", "equal", "s0", "0")),
        (None, "set", ("s0", "42")),
        ("L_if_1_end", None, None),
        ("L_if_0_end", None, None),
    ]


def test_if_branches_write_at_post_flag_depth():
    """After IF pops the flag, both arms operate at the lower depth.
    `2 3 < IF 100 ELSE 200 THEN` writes the result to s0 in both arms
    because the comparison consumed s0/s1 and wrote s0, then IF popped
    that flag leaving depth 0."""
    instrs = compile_to_tuples("2 3 < IF 100 ELSE 200 THEN")
    assert instrs == [
        (None, "set", ("s0", "2")),
        (None, "set", ("s1", "3")),
        (None, "op", ("lessThan", "s0", "s0", "s1")),
        (None, "jump", ("L_if_0_else", "equal", "s0", "0")),
        (None, "set", ("s0", "100")),
        (None, "jump", ("L_if_0_end", "always", "0", "0")),
        ("L_if_0_else", "set", ("s0", "200")),
        ("L_if_0_end", None, None),
    ]


# ---------------------------------------------------------------------------
# BEGIN / UNTIL
# ---------------------------------------------------------------------------


def test_begin_until_simple():
    """`BEGIN 0 UNTIL` — minimal loop body that pushes 0 (so the loop
    repeats forever as a torture test of label encoding).  The body
    nets +1 (the literal); UNTIL consumes it.

    Slot trace:
      Begin (depth 0)
        LitInt(0) writes s0
      UNTIL reads s0 (the flag).
    """
    instrs = compile_to_tuples("BEGIN 0 UNTIL")
    assert instrs == [
        ("L_begin_0_top", "set", ("s0", "0")),
        (None, "jump", ("L_begin_0_top", "equal", "s0", "0")),
    ]


def test_begin_until_with_decrement_pattern():
    """`5 BEGIN 1 - DUP 0 = UNTIL` — countdown loop.

    Slot trace:
      LitInt(5) writes s0         depth: 0 → 1
      Begin at depth 1
        LitInt(1) writes s1       depth: 1 → 2
        WordCall(-) reads s0,s1 writes s0   depth: 2 → 1
        DUP reads s0 writes s0,s1 depth: 1 → 2
        LitInt(0) writes s2       depth: 2 → 3
        WordCall(=) reads s1,s2 writes s1   depth: 3 → 2
      UNTIL reads s1 (flag at s1)
    """
    instrs = compile_to_tuples("5 BEGIN 1 - DUP 0 = UNTIL")
    assert instrs == [
        (None, "set", ("s0", "5")),
        ("L_begin_0_top", "set", ("s1", "1")),
        (None, "op", ("sub", "s0", "s0", "s1")),
        (None, "set", ("s1", "s0")),
        (None, "set", ("s2", "0")),
        (None, "op", ("equal", "s1", "s1", "s2")),
        (None, "jump", ("L_begin_0_top", "equal", "s1", "0")),
    ]


# ---------------------------------------------------------------------------
# BEGIN / WHILE / REPEAT
# ---------------------------------------------------------------------------


def test_begin_while_repeat_simple():
    """`BEGIN 1 WHILE 42 DROP REPEAT` — infinite-loop pattern with a
    constant-true test.  Pin the full skeleton."""
    instrs = compile_to_tuples("BEGIN 1 WHILE 42 DROP REPEAT")
    assert instrs == [
        ("L_begin_0_top", "set", ("s0", "1")),
        (None, "jump", ("L_begin_0_after", "equal", "s0", "0")),
        (None, "set", ("s0", "42")),
        (None, "jump", ("L_begin_0_top", "always", "0", "0")),
        ("L_begin_0_after", None, None),
    ]


def test_begin_while_repeat_countdown():
    """A real countdown using WHILE/REPEAT.

    `5 BEGIN DUP 0 > WHILE 1 - REPEAT`

    Slot trace (frame_offset = 0):
      LitInt(5) writes s0          depth: 0 → 1
      Begin at depth 1
        test:
          DUP reads s0 writes s0,s1 depth: 1 → 2
          LitInt(0) writes s2      depth: 2 → 3
          WordCall(>) reads s1,s2 writes s1  depth: 3 → 2
        WHILE pops flag (s1) → depth 1
        cond_body:
          LitInt(1) writes s1      depth: 1 → 2
          WordCall(-) reads s0,s1 writes s0  depth: 2 → 1
        REPEAT jumps to L_begin_0_top.
    """
    instrs = compile_to_tuples("5 BEGIN DUP 0 > WHILE 1 - REPEAT")
    assert instrs == [
        (None, "set", ("s0", "5")),
        ("L_begin_0_top", "set", ("s1", "s0")),
        (None, "set", ("s2", "0")),
        (None, "op", ("greaterThan", "s1", "s1", "s2")),
        (None, "jump", ("L_begin_0_after", "equal", "s1", "0")),
        (None, "set", ("s1", "1")),
        (None, "op", ("sub", "s0", "s0", "s1")),
        (None, "jump", ("L_begin_0_top", "always", "0", "0")),
        ("L_begin_0_after", None, None),
    ]


# ---------------------------------------------------------------------------
# DO / LOOP
# ---------------------------------------------------------------------------


def test_do_loop_no_body_iteration_skeleton():
    """`10 0 DO LOOP` — empty body loop; pin the prologue, the top
    label, the increment, and the back-jump.

    `( limit index -- )`: 10 is limit, 0 is index.  After the literals
    push, s0=10, s1=0.  DO reads (s0=limit, s1=index)."""
    instrs = compile_to_tuples("10 0 DO LOOP")
    assert instrs == [
        (None, "set", ("s0", "10")),
        (None, "set", ("s1", "0")),
        (None, "set", ("__do_idx_0", "s1")),
        (None, "set", ("__do_limit_0", "s0")),
        ("L_do_0_top", "jump", ("L_do_0_end", "greaterThanEq", "__do_idx_0", "__do_limit_0")),
        (None, "op", ("add", "__do_idx_0", "__do_idx_0", "1")),
        (None, "jump", ("L_do_0_top", "always", "0", "0")),
        ("L_do_0_end", None, None),
    ]


def test_do_loop_i_reads_loop_counter():
    """`5 0 DO I DROP LOOP` — body reads I (current counter).  I writes
    its value into the next data-stack slot.  After DO consumes both
    limit and index, depth drops to 0, so I writes s0."""
    instrs = compile_to_tuples("5 0 DO I DROP LOOP")
    assert instrs == [
        (None, "set", ("s0", "5")),
        (None, "set", ("s1", "0")),
        (None, "set", ("__do_idx_0", "s1")),
        (None, "set", ("__do_limit_0", "s0")),
        ("L_do_0_top", "jump", ("L_do_0_end", "greaterThanEq", "__do_idx_0", "__do_limit_0")),
        (None, "set", ("s0", "__do_idx_0")),
        (None, "op", ("add", "__do_idx_0", "__do_idx_0", "1")),
        (None, "jump", ("L_do_0_top", "always", "0", "0")),
        ("L_do_0_end", None, None),
    ]


def test_nested_do_loop_uses_distinct_counter_slots():
    """Nested DO/LOOP: outer is do-0, inner is do-1.  Both I (inner)
    and J (outer) work.

    `3 0 DO 3 0 DO I J + DROP LOOP LOOP`

    Slot trace (frame_offset=0):
      LitInt(3) s0    depth 0 → 1
      LitInt(0) s1    depth 1 → 2
      Outer DO reads (s0=limit, s1=index)   depth 2 → 0
        LitInt(3) s0   depth 0 → 1
        LitInt(0) s1   depth 1 → 2
        Inner DO reads (s0=limit, s1=index) depth 2 → 0
          I writes s0   depth 0 → 1
          J writes s1   depth 1 → 2
          + reads s0,s1 writes s0  depth 2 → 1
          DROP          depth 1 → 0
        Inner LOOP
      Outer LOOP
    """
    instrs = compile_to_tuples("3 0 DO 3 0 DO I J + DROP LOOP LOOP")
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "set", ("s1", "0")),
        (None, "set", ("__do_idx_0", "s1")),
        (None, "set", ("__do_limit_0", "s0")),
        ("L_do_0_top", "jump", ("L_do_0_end", "greaterThanEq", "__do_idx_0", "__do_limit_0")),
        (None, "set", ("s0", "3")),
        (None, "set", ("s1", "0")),
        (None, "set", ("__do_idx_1", "s1")),
        (None, "set", ("__do_limit_1", "s0")),
        ("L_do_1_top", "jump", ("L_do_1_end", "greaterThanEq", "__do_idx_1", "__do_limit_1")),
        (None, "set", ("s0", "__do_idx_1")),
        (None, "set", ("s1", "__do_idx_0")),
        (None, "op", ("add", "s0", "s0", "s1")),
        (None, "op", ("add", "__do_idx_1", "__do_idx_1", "1")),
        (None, "jump", ("L_do_1_top", "always", "0", "0")),
        ("L_do_1_end", "op", ("add", "__do_idx_0", "__do_idx_0", "1")),
        (None, "jump", ("L_do_0_top", "always", "0", "0")),
        ("L_do_0_end", None, None),
    ]


# ---------------------------------------------------------------------------
# Combining IF and BEGIN — counter independence
# ---------------------------------------------------------------------------


def test_if_and_begin_use_independent_counters():
    """IF counter and BEGIN counter increment independently.

    The IF body is stack-neutral: pushes 0, runs a BEGIN/UNTIL that
    pushes a literal-1 flag (so UNTIL exits immediately and net stack
    delta from BEGIN..UNTIL is 0 — the flag is consumed), then DROPs
    the 0 that was pushed before BEGIN.
    """
    instrs = compile_to_tuples("1 IF 0 BEGIN 1 UNTIL DROP THEN")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "jump", ("L_if_0_end", "equal", "s0", "0")),
        (None, "set", ("s0", "0")),
        ("L_begin_0_top", "set", ("s1", "1")),
        (None, "jump", ("L_begin_0_top", "equal", "s1", "0")),
        ("L_if_0_end", None, None),
    ]


# ---------------------------------------------------------------------------
# Negative cases — contract failure surface
# ---------------------------------------------------------------------------


def test_bare_i_outside_do_loop_raises():
    """`I` outside any DO/LOOP context — the dictionary lets it parse,
    stackcheck can't prove the absence of context, but the emitter has
    no `__do_idx_<N>` to point at.  Raise NotImplementedError with a
    clear message naming the word."""
    with pytest.raises(NotImplementedError, match=r"DO/LOOP counter"):
        compile_to_tuples("I DROP")


def test_bare_j_outside_do_loop_raises():
    """Same for J."""
    with pytest.raises(NotImplementedError, match=r"DO/LOOP counter"):
        compile_to_tuples("J DROP")


def test_j_inside_single_do_loop_raises():
    """`J` requires *two* enclosing DO/LOOP layers — using it in a
    single layer is a contract violation that produces malformed mlog
    if silently allowed (would index past the end of the loop stack)."""
    with pytest.raises(NotImplementedError, match=r"DO/LOOP counter"):
        compile_to_tuples("5 0 DO J DROP LOOP")


# ---------------------------------------------------------------------------
# User-definition inlining with control flow
# ---------------------------------------------------------------------------


def test_user_def_with_if_inlines_with_offset():
    """A definition that contains control flow gets inlined at the
    call site with the slot-offset rewrite applied, and the labels
    keep their counter numbers across the inline expansion.

    The definition is a stack-neutral predicate ( n -- n ) that doubles
    the value when negative, leaves it untouched otherwise — both arms
    leave the stack at depth 1 so stackcheck accepts it."""
    src = """
: clamp-negative ( n -- n' )  DUP 0 < IF -1 * THEN ;
3 clamp-negative DROP
"""
    instrs = compile_to_tuples(src)
    # Caller pushes 3 → s0.  Definition body inlines with caller_base
    # = 0 (the call's reads start at s0):
    #   DUP        reads s0 writes s0,s1
    #   LitInt(0)  writes s2
    #   <          reads s1,s2 writes s1   (flag)
    #   IF         reads s1
    #     LitInt(-1) writes s1
    #     *          reads s0,s1 writes s0
    #   THEN
    # Caller then DROPs the result.
    assert instrs == [
        (None, "set", ("s0", "3")),
        (None, "set", ("s1", "s0")),
        (None, "set", ("s2", "0")),
        (None, "op", ("lessThan", "s1", "s1", "s2")),
        (None, "jump", ("L_if_0_end", "equal", "s1", "0")),
        (None, "set", ("s1", "-1")),
        (None, "op", ("mul", "s0", "s0", "s1")),
        ("L_if_0_end", None, None),
    ]


# ---------------------------------------------------------------------------
# Edge case — empty bodies inside IF
# ---------------------------------------------------------------------------


def test_if_with_empty_then_body():
    """`1 IF THEN` — degenerate but legal; flag is popped, no body
    emission.  Just the jump-over and the end label sentinel."""
    instrs = compile_to_tuples("1 IF THEN")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "jump", ("L_if_0_end", "equal", "s0", "0")),
        ("L_if_0_end", None, None),
    ]


# ---------------------------------------------------------------------------
# Integration — every emitted jump target resolves to a placed label
# ---------------------------------------------------------------------------


def _collect_labels(instrs: list[MlogInstr]) -> set[str]:
    """Return every label name that has been *placed* in the stream
    (i.e. appears as the first element of any tuple, including
    sentinel ``(label, None, None)`` tuples)."""
    return {label for (label, _, _) in instrs if label is not None}


def _collect_jump_targets(instrs: list[MlogInstr]) -> set[str]:
    """Return every label name that a ``jump`` instruction references."""
    targets: set[str] = set()
    for (_, opcode, operands) in instrs:
        if opcode == "jump":
            targets.add(operands[0])
    return targets


@pytest.mark.parametrize(
    "src",
    [
        # IF / ELSE
        "1 IF 2 ELSE 3 THEN DROP",
        # BEGIN / UNTIL
        "5 BEGIN 1 - DUP 0 = UNTIL DROP",
        # BEGIN / WHILE / REPEAT
        "5 BEGIN DUP 0 > WHILE 1 - REPEAT DROP",
        # DO / LOOP (empty body)
        "5 0 DO LOOP",
        # DO / LOOP with body that uses I
        "5 0 DO I DROP LOOP",
        # Nested DO/LOOP with J
        "3 0 DO 3 0 DO I J + DROP LOOP LOOP",
        # IF nested inside DO/LOOP body
        "5 0 DO I 2 < IF 99 DROP THEN LOOP",
    ],
    ids=[
        "if-else",
        "begin-until",
        "begin-while-repeat",
        "do-loop-empty",
        "do-loop-with-i",
        "nested-do-loop-with-j",
        "if-inside-do-loop",
    ],
)
def test_every_jump_target_is_placed_as_a_label(src):
    """Cross-cut integration: bead .19 (label resolution) will need
    every ``jump`` target to map to a placed label in the same
    instruction stream.  If the emitter ever leaves a dangling jump
    target this test catches it before .19 has to.

    This is the contract handshake to .19 — without it that bead has
    no choice but to error on dangling labels, but the error would
    surface at link time instead of emit time and the user wouldn't
    know which source construct generated the bad jump."""
    instrs = compile_to_tuples(src)
    placed = _collect_labels(instrs)
    targets = _collect_jump_targets(instrs)
    dangling = targets - placed
    assert not dangling, (
        f"jump target(s) {sorted(dangling)} not placed as labels "
        f"in stream: {instrs}"
    )


def test_if_with_empty_then_and_else_collapses_to_no_else_form():
    """`1 IF ELSE THEN` — the parser stores both bodies as ``[]`` and
    the emitter has no AST signal that ELSE was present.  This is
    intentional: the construct is semantically a no-op (pop flag, do
    nothing) and lowers to the same shape as ``1 IF THEN``.  Pinning
    that here so a future parser refactor that DOES carry an
    ELSE-marker can't silently change codegen."""
    instrs = compile_to_tuples("1 IF ELSE THEN")
    assert instrs == [
        (None, "set", ("s0", "1")),
        (None, "jump", ("L_if_0_end", "equal", "s0", "0")),
        ("L_if_0_end", None, None),
    ]
