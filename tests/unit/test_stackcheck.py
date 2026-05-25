"""Unit tests for the mforth static stack-checker.

Bead mforth-10t.7. The stack-checker is the load-bearing gate that
makes pragmatic mforth viable (per CLAUDE.md: "Static stack analysis
is mandatory"). It also feeds LSP diagnostics.
"""

from __future__ import annotations

import pytest

from mforth.dictionary import StackEffect, resolve, standard_dictionary
from mforth.parse import parse
from mforth.stackcheck import StackError, StackcheckResult, stackcheck


def check(src: str, file: str = "<test>") -> StackcheckResult:
    prog = parse(src, file=file)
    return stackcheck(prog)


# ---------------------------------------------------------------------------
# Built-in stack effects propagate
# ---------------------------------------------------------------------------


def test_check_empty_program():
    result = check("")
    assert result.effects == {}


def test_check_literal_only():
    check("42")


def test_check_simple_arithmetic():
    check("1 2 +")


def test_check_drop_underflow_at_main_level_errors():
    # main starts at depth 0; DROP needs 1 → underflow
    with pytest.raises(StackError) as exc:
        check("DROP")
    assert "underflow" in str(exc.value).lower()
    assert exc.value.src_loc.line == 1
    assert exc.value.src_loc.col == 1


def test_check_repeated_drop_underflow_reports_first_underflow_site():
    src = "1 DROP DROP"  # second DROP underflows
    with pytest.raises(StackError) as exc:
        check(src)
    # Second DROP is at col 8
    assert exc.value.src_loc.col == 8


def test_check_dup_then_arith():
    check("5 DUP *")  # 5, dup→5 5, *→25 ; final depth 1


# ---------------------------------------------------------------------------
# User definitions: per-word stack effect inference
# ---------------------------------------------------------------------------


def test_def_effect_of_square():
    result = check(": square dup * ;")
    assert result.effects["square"] == StackEffect(1, 1)


def test_def_effect_of_const_pusher():
    result = check(": one 1 ;")
    assert result.effects["one"] == StackEffect(0, 1)


def test_def_effect_of_const_consumer():
    result = check(": eat-two drop drop ;")
    assert result.effects["eat-two"] == StackEffect(2, 0)


def test_def_effect_of_swap_call_chain():
    result = check(": rotate-pair swap ;")
    assert result.effects["rotate-pair"] == StackEffect(2, 2)


def test_def_effect_composition():
    # increment = ( n -- n+1 )
    result = check(": incr 1 + ;")
    assert result.effects["incr"] == StackEffect(1, 1)


def test_def_calling_user_def_inherits_effect():
    result = check(": incr 1 + ; : incr2 incr incr ;")
    assert result.effects["incr2"] == StackEffect(1, 1)


def test_def_recursive_call_raises():
    # 'loop' is a control keyword; use 'loopy' to avoid parser collision.
    with pytest.raises(StackError) as exc:
        check(": loopy loopy ;")
    assert "recursive" in str(exc.value).lower()


def test_def_mutually_recursive_raises():
    with pytest.raises(StackError) as exc:
        check(": a b ; : b a ;")
    assert "recursive" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# IF/ELSE/THEN: branch depth must match
# ---------------------------------------------------------------------------


def test_if_branches_matching_depth_ok():
    # Both branches push 1 item
    check(": pickone IF 1 ELSE 2 THEN ;")


def test_if_branches_mismatched_depth_raises():
    # then-branch pushes 1, else-branch pushes 2 → mismatch
    with pytest.raises(StackError) as exc:
        check(": broken IF 1 ELSE 2 3 THEN ;")
    assert "branch" in str(exc.value).lower() or "depth" in str(exc.value).lower()


def test_if_without_else_must_be_stack_neutral():
    # body pushes 1 — would change post-THEN depth → error (else_body is [])
    with pytest.raises(StackError):
        check(": broken IF 1 THEN ;")


def test_if_without_else_neutral_ok():
    # body consumes 1 produces 1 — neutral
    check(": noop-if IF 1 + THEN ;")


# ---------------------------------------------------------------------------
# BEGIN / UNTIL: body must net-produce a flag for UNTIL
# ---------------------------------------------------------------------------


def test_begin_until_body_with_flag_ok():
    # body must end one deeper than start (the flag for UNTIL)
    check(": loop1 BEGIN 1 UNTIL ;")


def test_begin_until_body_without_flag_raises():
    with pytest.raises(StackError) as exc:
        check(": broken BEGIN UNTIL ;")
    assert "flag" in str(exc.value).lower() or "neutral" in str(exc.value).lower() or "delta" in str(exc.value).lower()


def test_begin_until_body_two_deeper_raises():
    with pytest.raises(StackError):
        check(": broken BEGIN 1 2 UNTIL ;")


# ---------------------------------------------------------------------------
# BEGIN / WHILE / REPEAT: test produces flag; body stack-neutral
# ---------------------------------------------------------------------------


def test_begin_while_repeat_ok():
    # test pushes 1 (the flag); body consumes 1 produces 1
    check(": loopw BEGIN 1 WHILE 1 + REPEAT ;")


def test_begin_while_test_must_produce_flag():
    with pytest.raises(StackError):
        check(": broken BEGIN WHILE 1 REPEAT ;")


def test_begin_while_body_must_be_neutral():
    # body pushes extra item → non-neutral
    with pytest.raises(StackError):
        check(": broken BEGIN 1 WHILE 99 REPEAT ;")


# ---------------------------------------------------------------------------
# DO / LOOP: body must be stack-neutral; DO consumes 2 (limit, start)
# ---------------------------------------------------------------------------


def test_do_loop_consumes_limit_and_start():
    # body uses I (which pushes 1) then drops it → stack-neutral inside DO
    check(": pr-i 10 0 DO I DROP LOOP ;")


def test_do_loop_body_not_neutral_raises():
    with pytest.raises(StackError):
        check(": broken 10 0 DO I LOOP ;")  # I pushes 1 with no consumer


def test_do_loop_without_limit_start_inputs_inferred():
    # ': drives ( limit start -- ) ' should have effect (2, 0)
    result = check(": drives DO LOOP ;")
    assert result.effects["drives"] == StackEffect(2, 0)


# ---------------------------------------------------------------------------
# Variables: VARIABLE adds a (0, 1) entry (pushes address); @ and ! consume
# ---------------------------------------------------------------------------


def test_variable_fetch_chain():
    # VARIABLE n (0,0); n (0,1); @ (1,1) → net 1 produced
    check("VARIABLE n n @")


def test_variable_store_chain():
    # VARIABLE n; 42 (0,1); n (0,1); ! (2,0) → net 0
    check("VARIABLE n 42 n !")


def test_variable_used_in_definition():
    result = check("VARIABLE counter : bump counter @ 1 + counter ! ;")
    assert result.effects["bump"] == StackEffect(0, 0)


# ---------------------------------------------------------------------------
# Annotated AST: every term has stack_depth_in recorded
# ---------------------------------------------------------------------------


def test_depth_in_recorded_for_each_term():
    src = "1 2 +"
    prog = parse(src)
    result = stackcheck(prog)
    # main has three terms: LitInt(1), LitInt(2), WordCall(+)
    assert result.depth_in(prog.main[0]) == 0
    assert result.depth_in(prog.main[1]) == 1
    assert result.depth_in(prog.main[2]) == 2


def test_depth_in_recorded_inside_definition():
    # Per the simulator, depth_in is measured relative to body-start
    # (depth 0). For `dup *`: dup at depth 0; `*` at depth 1 (dup pushed
    # net 1 item: -1 +2 = +1). Codegen reasoning about absolute slots can
    # add the inferred in_arity back in.
    src = ": square dup * ;"
    prog = parse(src)
    result = stackcheck(prog)
    body = prog.definitions[0].body
    assert result.depth_in(body[0]) == 0
    assert result.depth_in(body[1]) == 1


# ---------------------------------------------------------------------------
# Error src_loc fidelity
# ---------------------------------------------------------------------------


def test_underflow_src_loc_points_at_offending_term():
    src = "1\n  DROP\n  DROP"  # second DROP underflows
    with pytest.raises(StackError) as exc:
        check(src, file="x.fs")
    err = exc.value
    assert err.src_loc.file == "x.fs"
    assert err.src_loc.line == 3
    assert err.src_loc.col == 3


def test_if_mismatch_src_loc_points_at_if():
    src = "1 IF 1 ELSE 2 3 THEN"
    with pytest.raises(StackError) as exc:
        check(src)
    # Loc of the IF keyword (col 3) or somewhere in the construct
    assert exc.value.src_loc.line == 1
    # The mismatch is reported on the IfThen node; src_loc points at the IF
    assert exc.value.src_loc.col == 3


# ---------------------------------------------------------------------------
# Declared stack effect enforcement (mforth-6dh)
# ---------------------------------------------------------------------------
# A `( in -- out )` comment after the `:` name is a declared stack effect.
# Stackcheck verifies the inferred effect matches the declared one. Mismatch
# raises StackError naming the word, declared, and inferred effects.
#
# A definition with NO declared effect retains the current permissive
# behavior (additive change, not breaking).


def test_declared_effect_matches_inferred_no_error():
    # square: ( a -- b ), dup * → in_arity=1, out_arity=1. Matches.
    result = check(": square ( a -- b ) dup * ;")
    assert result.effects["square"] == StackEffect(1, 1)


def test_declared_effect_zero_zero_matches_noop():
    result = check(": noop ( -- ) ;")
    assert result.effects["noop"] == StackEffect(0, 0)


def test_declared_zero_outputs_but_body_produces_one_errors():
    # body pushes 1 → inferred ( -- a ); declared ( -- ) leaks a value.
    # This is the blink.fs class of bug.
    with pytest.raises(StackError) as exc:
        check(": leak ( -- ) 1 ;")
    msg = str(exc.value).lower()
    assert "leak" in msg
    assert "declared" in msg
    assert "inferred" in msg


def test_declared_one_input_but_body_consumes_none_errors():
    # declared ( a -- ); body consumes nothing.
    with pytest.raises(StackError) as exc:
        check(": noop ( a -- ) ;")
    assert "noop" in str(exc.value)


def test_declared_more_outputs_than_body_produces_errors():
    # declared ( -- a b ); body produces only 1
    with pytest.raises(StackError) as exc:
        check(": one ( -- a b ) 1 ;")
    assert "one" in str(exc.value)


def test_declared_inputs_mismatch_errors():
    # declared ( a b -- c ); body is dup * which is (1, 1)
    with pytest.raises(StackError) as exc:
        check(": badsquare ( a b -- c ) dup * ;")
    assert "badsquare" in str(exc.value)


def test_no_declared_effect_permissive_behavior_preserved():
    # Without a declared effect a definition with leftover stack still
    # checks fine — this is the v1 permissive behavior and we don't
    # break it.
    result = check(": leak 1 ;")
    assert result.effects["leak"] == StackEffect(0, 1)


def test_blink_pattern_caught_by_declared_effect():
    # The exact symptom from mforth-6dh: a definition declares ( -- )
    # but the body pushes a string literal that's never consumed.
    src = ': tick ( -- ) ." count=" ;'
    with pytest.raises(StackError) as exc:
        check(src)
    msg = str(exc.value)
    assert "tick" in msg
    # The error should communicate the actual leak shape.
    assert "0" in msg  # declared 0 outputs
    assert "1" in msg  # inferred 1 output


def test_declared_effect_matches_inputs_only():
    # ( a -- ) declares a one-in, zero-out word; matches drop.
    result = check(": eat ( a -- ) drop ;")
    assert result.effects["eat"] == StackEffect(1, 0)


def test_declared_effect_matches_outputs_only():
    # ( -- a ) declares a zero-in, one-out word; matches a const pusher.
    result = check(": one ( -- a ) 1 ;")
    assert result.effects["one"] == StackEffect(0, 1)


def test_declared_effect_matches_two_in_one_out():
    # ( a b -- c ) declares a binary operator-shaped word.
    result = check(": plus ( a b -- c ) + ;")
    assert result.effects["plus"] == StackEffect(2, 1)


def test_declared_effect_error_carries_src_loc():
    src = ': tick ( -- ) ." leak" ;'
    with pytest.raises(StackError) as exc:
        check(src, file="blink.fs")
    err = exc.value
    assert err.src_loc.file == "blink.fs"
