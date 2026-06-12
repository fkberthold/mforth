"""Unit + equivalence tests for the constant-folding pass (bead mforth-10t.35).

``mforth.fold.fold_constants`` is a v2, pre-codegen AST pass (post-stackcheck,
pre-slot-alloc). It walks each ``Definition`` body + ``main``, maintaining a
symbolic constant stack; when a ``Term`` consumes ONLY known constants AND is
pure (arithmetic, comparison, bitwise/logical, stack ops), it evaluates the
operation at compile time and replaces the consumed-literal-plus-word sequence
with a single literal node. Side-effectful terms and control flow break the
constant chain (the pending constants are flushed unchanged).

The pass is INTENTIONALLY DEAD CODE until bead mforth-10t.40 wires the ``-O``
levels — it is not yet in the default pipeline. These tests exercise it
standalone.

The headline obligation (CLAUDE.md hard rule): folding MUST preserve the
observable EventStream. ``test_fold_preserves_events_*`` execute the
un-folded and folded programs through the host executor and assert the
event sequences are byte-for-byte identical.
"""

from __future__ import annotations

from mforth.backend.host import Executor
from mforth.backend.primitives import register_all
from mforth.backend.world import MockWorld
from mforth.dictionary import resolve
from mforth.fold import fold_constants
from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    LitStr,
    WordCall,
    parse,
)
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fold(src: str):
    """Parse + fold ``src`` and return the folded ``Program``."""
    program = parse(src, file="<test>")
    return fold_constants(program)


def _run_events(program) -> list:
    """Execute ``program.main`` once through the host executor; return events."""
    world = MockWorld()
    dictionary = resolve(program)
    result = stackcheck(program, dictionary=dictionary)
    executor = Executor(world=world, dictionary=dictionary)
    register_all(executor)
    executor.execute(result)
    return list(world.events)


def _count_terms(terms: list) -> int:
    """Recursively count terms (so we can assert that folding shrank a body)."""
    n = 0
    for t in terms:
        n += 1
        if isinstance(t, IfThen):
            n += _count_terms(t.then_body) + _count_terms(t.else_body)
        elif isinstance(t, Begin):
            n += _count_terms(t.body) + _count_terms(t.cond_body)
        elif isinstance(t, DoLoop):
            n += _count_terms(t.body)
    return n


# ---------------------------------------------------------------------------
# Arithmetic chains
# ---------------------------------------------------------------------------


def test_fold_simple_add():
    prog = _fold("5 1 +")
    assert prog.main == [LitInt(6, prog.main[0].src_loc)]


def test_fold_arith_chain():
    # 2 3 + 4 *  ->  20
    prog = _fold("2 3 + 4 *")
    assert len(prog.main) == 1
    assert isinstance(prog.main[0], LitInt)
    assert prog.main[0].value == 20


def test_fold_subtraction_order():
    # Forth order: 10 3 -  ->  7  (a-b, not b-a)
    prog = _fold("10 3 -")
    assert prog.main[0].value == 7


def test_fold_division_yields_float():
    # `/` is float division (mlog op div) — result must be a LitFloat so the
    # pushed runtime type matches the host `/` primitive (mforth-dlr).
    prog = _fold("7 2 /")
    assert len(prog.main) == 1
    assert isinstance(prog.main[0], LitFloat)
    assert prog.main[0].value == 3.5


def test_fold_division_exact_still_float():
    # 6 2 / -> 3.0 as a LitFloat (NOT LitInt 3): the host pushes a float, and
    # PRINT/dot strip the trailing .0 — folding to LitInt would change nothing
    # observable for PRINT but WOULD change it for `=` comparisons, so we keep
    # the float type to stay faithful.
    prog = _fold("6 2 /")
    assert isinstance(prog.main[0], LitFloat)
    assert prog.main[0].value == 3.0


def test_fold_mod():
    prog = _fold("10 3 MOD")
    assert prog.main[0].value == 1


# ---------------------------------------------------------------------------
# Comparisons — produce mlog 0/1 encoding as LitInt
# ---------------------------------------------------------------------------


def test_fold_less_than_true():
    prog = _fold("3 4 <")
    assert prog.main == [LitInt(1, prog.main[0].src_loc)]


def test_fold_less_than_false():
    prog = _fold("4 3 <")
    assert prog.main[0].value == 0


def test_fold_equal():
    prog = _fold("5 5 =")
    assert isinstance(prog.main[0], LitInt)
    assert prog.main[0].value == 1


def test_fold_greater_equal_false():
    prog = _fold("2 9 >=")
    assert prog.main[0].value == 0


# ---------------------------------------------------------------------------
# Logical / bitwise
# ---------------------------------------------------------------------------


def test_fold_and():
    prog = _fold("1 0 AND")
    assert prog.main[0].value == 0


def test_fold_or():
    prog = _fold("1 0 OR")
    assert prog.main[0].value == 1


def test_fold_not():
    prog = _fold("0 NOT")
    assert prog.main[0].value == 1


# ---------------------------------------------------------------------------
# Stack-op folding on the symbolic constant stack
# ---------------------------------------------------------------------------


def test_fold_dup():
    # 5 DUP +  ->  10
    prog = _fold("5 DUP +")
    assert len(prog.main) == 1
    assert prog.main[0].value == 10


def test_fold_swap():
    # 10 3 SWAP -  ->  3 10 -  ->  -7
    prog = _fold("10 3 SWAP -")
    assert prog.main[0].value == -7


def test_fold_over():
    # 7 2 OVER + +  ->  ( 7 2 7 ) + +  ->  16
    prog = _fold("7 2 OVER + +")
    assert prog.main[0].value == 16


def test_fold_rot():
    # 1 2 3 ROT  ->  2 3 1  ; sum-collapse via + +  -> 6
    prog = _fold("1 2 3 ROT + +")
    assert prog.main[0].value == 6


def test_fold_drop():
    # 1 2 DROP  ->  1
    prog = _fold("1 2 DROP")
    assert len(prog.main) == 1
    assert prog.main[0].value == 1


def test_fold_drop_to_empty():
    # 1 DROP  ->  (nothing)
    prog = _fold("1 DROP")
    assert prog.main == []


# ---------------------------------------------------------------------------
# Side-effects break the chain; surviving constants are flushed in order
# ---------------------------------------------------------------------------


def test_print_breaks_chain_but_folds_before():
    # 2 3 + PRINT  ->  5 PRINT  (one folded literal, then the PRINT)
    prog = _fold("2 3 + PRINT")
    assert len(prog.main) == 2
    assert isinstance(prog.main[0], LitInt)
    assert prog.main[0].value == 5
    assert isinstance(prog.main[1], WordCall)
    assert prog.main[1].name.upper() == "PRINT"


def test_fetch_breaks_chain_constants_flushed_in_order():
    # VARIABLE x  1 2 + x !   ->   the `1 2 +` folds to 3, then `x ! ` stays.
    prog = _fold("VARIABLE x  1 2 + x !")
    # main: WordCall VARIABLE, WordCall x, LitInt 3, WordCall x, WordCall !
    names_and_lits = [
        ("lit", t.value) if isinstance(t, LitInt) else ("word", t.name)
        for t in prog.main
    ]
    assert ("lit", 3) in names_and_lits
    # The `+` is gone (folded away).
    assert all(
        not (isinstance(t, WordCall) and t.name == "+") for t in prog.main
    )


def test_unknown_user_word_flushes_constants():
    # A user word with unknown effect is opaque — constants before it are
    # flushed unchanged and folding does not cross it.
    src = ": triple DUP DUP + + ;\n4 5 + triple"
    prog = _fold(src)
    # main: LitInt 9 (folded), WordCall triple
    assert any(
        isinstance(t, LitInt) and t.value == 9 for t in prog.main
    )
    assert any(
        isinstance(t, WordCall) and t.name == "triple" for t in prog.main
    )


def test_partial_constants_below_runtime_value_not_folded():
    # x @ pushes a runtime value; `1 2 +` above it still folds, but the word
    # `+` that would consume the runtime value cannot fold.
    # `VARIABLE x  x @ 1 2 + +`  : the inner `1 2 +` -> 3, leaving `x @ 3 +`.
    prog = _fold("VARIABLE x  x @ 1 2 + +")
    # Exactly one `+` survives (the one consuming the runtime fetch); the
    # inner `1 2 +` folded to 3.
    plus_words = [
        t for t in prog.main if isinstance(t, WordCall) and t.name == "+"
    ]
    assert len(plus_words) == 1
    assert any(isinstance(t, LitInt) and t.value == 3 for t in prog.main)


# ---------------------------------------------------------------------------
# Definitions are folded too
# ---------------------------------------------------------------------------


def test_fold_inside_definition():
    src = ": const 2 3 + ;"
    prog = _fold(src)
    body = prog.definitions[0].body
    assert len(body) == 1
    assert isinstance(body[0], LitInt)
    assert body[0].value == 5


# ---------------------------------------------------------------------------
# Control flow breaks the chain (folding does not reach across IF/loop)
# ---------------------------------------------------------------------------


def test_control_flow_not_folded_across():
    # The flag for IF is a runtime decision in general; here it IS a constant
    # but the conservative pass flushes the constant stack at the control-flow
    # boundary. We assert folding happens INSIDE each branch but the IF node
    # itself survives.
    src = "1 IF 2 3 + PRINT ELSE 4 5 + PRINT THEN"
    prog = _fold(src)
    ifs = [t for t in prog.main if isinstance(t, IfThen)]
    assert len(ifs) == 1
    then_lits = [t for t in ifs[0].then_body if isinstance(t, LitInt)]
    assert any(t.value == 5 for t in then_lits)
    else_lits = [t for t in ifs[0].else_body if isinstance(t, LitInt)]
    assert any(t.value == 9 for t in else_lits)


# ---------------------------------------------------------------------------
# EQUIVALENCE PRESERVATION — the headline obligation
# ---------------------------------------------------------------------------


def _assert_events_preserved(src: str) -> None:
    """Run un-folded vs folded through the host executor; events must match."""
    original = parse(src, file="<test>")
    folded = fold_constants(parse(src, file="<test>"))
    events_orig = _run_events(original)
    events_folded = _run_events(folded)
    assert events_orig == events_folded, (
        f"folding changed observable events for {src!r}\n"
        f"  orig:   {events_orig!r}\n"
        f"  folded: {events_folded!r}"
    )


def test_fold_preserves_events_arith():
    _assert_events_preserved("2 3 + 4 * PRINT")


def test_fold_preserves_events_division():
    # `7 2 /` folds to a float; PRINT must still render "3.5".
    _assert_events_preserved("7 2 / PRINT")


def test_fold_preserves_events_exact_division():
    # `6 2 /` folds to 3.0; PRINT strips the .0 -> "3" on both paths.
    _assert_events_preserved("6 2 / PRINT")


def test_fold_preserves_events_comparison():
    _assert_events_preserved("3 4 < PRINT")


def test_fold_preserves_events_stack_ops():
    _assert_events_preserved("5 DUP + PRINT")


def test_fold_preserves_events_dot():
    _assert_events_preserved("2 3 + .")


def test_fold_preserves_events_variable_roundtrip():
    _assert_events_preserved("VARIABLE x  1 2 + x !  x @ .")


def test_fold_preserves_events_multiple_sinks():
    _assert_events_preserved("1 2 + . 3 4 + . 5 6 * .")


def test_fold_preserves_events_logical():
    _assert_events_preserved("1 0 AND . 1 0 OR . 0 NOT .")


def test_fold_preserves_events_inside_if():
    _assert_events_preserved("1 IF 2 3 + PRINT ELSE 4 5 + PRINT THEN")


def test_fold_preserves_events_inside_definition():
    _assert_events_preserved(": const 2 3 + ;\nconst .")


def test_fold_preserves_events_negative_and_zero():
    _assert_events_preserved("0 5 - . 0 0 = .")


def test_fold_shrinks_arith_heavy_program():
    # Acceptance: goldens for arith-heavy programs shrink by >= 25%.
    src = "1 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + ."
    original = parse(src, file="<test>")
    folded = fold_constants(parse(src, file="<test>"))
    before = _count_terms(original.main)
    after = _count_terms(folded.main)
    assert after <= before * 0.75, (before, after)
    # And it still prints the same thing.
    _assert_events_preserved(src)
