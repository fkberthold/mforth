"""Unit tests for the host REPL built-in primitives (bead mforth-10t.11).

Each test drives a small annotated AST through the executor with the
full primitive table registered and asserts on the resulting data
stack (or, for `.`, on the world's EventStream).

## Comparison-encoding contract (LOAD-BEARING)

Comparison primitives push **mlog's 0/1 encoding** (NOT Forth's -1/0).
This matches what bead mforth-10t.16 emits on the mlog side; the
REPL ↔ mlog equivalence property (CLAUDE.md headline test class)
requires identical observable values across both backends. The bead
text originally recommended Forth's -1/0; the dispatch context locked
the 0/1 choice. Logical AND/OR/NOT operate bitwise on this encoding.

If you find yourself "fixing" a test here to expect -1, stop and
re-read the module docstring of src/mforth/backend/primitives.py.
"""

from __future__ import annotations

import math

import pytest

from mforth.backend.host import Executor
from mforth.backend.primitives import register_all
from mforth.backend.world import MessagePrintEvent, MockWorld
from mforth.parse import LitInt, Program, SrcLoc, WordCall
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def L(line: int = 1, col: int = 1) -> SrcLoc:
    return SrcLoc("<test>", line, col)


def run(terms: list, *, defs: list | None = None, world: MockWorld | None = None) -> Executor:
    """Build a Program from a list of main-terms, stackcheck it, register
    every primitive on a fresh Executor, run it, and return the executor.
    """
    program = Program(definitions=defs or [], main=terms)
    result = stackcheck(program)
    w = world if world is not None else MockWorld()
    ex = Executor(world=w)
    register_all(ex)
    ex.execute(result)
    return ex


def lit(n: int, col: int = 1) -> LitInt:
    return LitInt(value=n, src_loc=L(1, col))


def call(name: str, col: int = 1) -> WordCall:
    return WordCall(name=name, src_loc=L(1, col))


def stack_after(terms: list) -> list:
    return run(terms).data_stack


# ---------------------------------------------------------------------------
# Arithmetic: + - * / MOD
# ---------------------------------------------------------------------------


def test_plus_adds_two_ints():
    assert stack_after([lit(2), lit(3), call("+")]) == [5]


def test_minus_subtracts_in_forth_order():
    # ( a b -- a-b )
    assert stack_after([lit(10), lit(3), call("-")]) == [7]


def test_times_multiplies():
    assert stack_after([lit(6), lit(7), call("*")]) == [42]


def test_divide_floats():
    # mlog `op div` is float division; mforth REPL matches.
    result = stack_after([lit(10), lit(4), call("/")])
    assert result == [2.5]


def test_divide_by_zero_yields_inf_or_nan_no_exception():
    # mlog returns inf/nan on /0; the REPL must not raise — equivalence.
    result = stack_after([lit(1), lit(0), call("/")])
    assert len(result) == 1
    v = result[0]
    assert math.isinf(v) or math.isnan(v)


def test_mod_modulo():
    assert stack_after([lit(17), lit(5), call("MOD")]) == [2]


def test_mod_by_zero_yields_nan_no_exception():
    result = stack_after([lit(1), lit(0), call("MOD")])
    assert len(result) == 1
    v = result[0]
    assert math.isnan(v) or math.isinf(v)


# ---------------------------------------------------------------------------
# Comparison: = <> < > <= >=    (mlog 0/1 encoding)
# ---------------------------------------------------------------------------


def test_equal_true_pushes_one():
    assert stack_after([lit(5), lit(5), call("=")]) == [1]


def test_equal_false_pushes_zero():
    assert stack_after([lit(5), lit(6), call("=")]) == [0]


def test_not_equal_true_pushes_one():
    assert stack_after([lit(5), lit(6), call("<>")]) == [1]


def test_not_equal_false_pushes_zero():
    assert stack_after([lit(5), lit(5), call("<>")]) == [0]


def test_less_than_true_pushes_one():
    assert stack_after([lit(3), lit(5), call("<")]) == [1]


def test_less_than_false_pushes_zero():
    assert stack_after([lit(5), lit(3), call("<")]) == [0]


def test_less_than_equal_is_one_at_boundary():
    assert stack_after([lit(5), lit(5), call("<=")]) == [1]


def test_greater_than_true_pushes_one():
    assert stack_after([lit(7), lit(2), call(">")]) == [1]


def test_greater_than_equal_is_one_at_boundary():
    assert stack_after([lit(5), lit(5), call(">=")]) == [1]


def test_comparison_never_returns_minus_one():
    """LOAD-BEARING: confirms we did NOT pick Forth's -1/0 encoding.

    If this test fails because the result is -1, you've broken the
    REPL ↔ mlog equivalence property. Re-read the module docstring.
    """
    for terms in [
        [lit(5), lit(5), call("=")],
        [lit(3), lit(5), call("<")],
        [lit(5), lit(3), call(">")],
        [lit(5), lit(5), call("<=")],
        [lit(5), lit(5), call(">=")],
        [lit(5), lit(6), call("<>")],
    ]:
        result = stack_after(terms)[0]
        assert result in (0, 1), f"comparison returned {result!r}, must be 0 or 1"


# ---------------------------------------------------------------------------
# Logical: AND OR NOT  (bitwise on the 0/1 boolean encoding)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b,expected",
    [(0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 1)],
)
def test_and_truth_table(a: int, b: int, expected: int):
    assert stack_after([lit(a), lit(b), call("AND")]) == [expected]


@pytest.mark.parametrize(
    "a,b,expected",
    [(0, 0, 0), (0, 1, 1), (1, 0, 1), (1, 1, 1)],
)
def test_or_truth_table(a: int, b: int, expected: int):
    assert stack_after([lit(a), lit(b), call("OR")]) == [expected]


@pytest.mark.parametrize("a,expected", [(0, 1), (1, 0)])
def test_not_truth_table(a: int, expected: int):
    assert stack_after([lit(a), call("NOT")]) == [expected]


def test_not_nonzero_is_zero():
    """NOT of any nonzero value is 0 (mlog op not semantics on 0/1
    encoding generalize: anything truthy → 0)."""
    assert stack_after([lit(42), call("NOT")]) == [0]


# ---------------------------------------------------------------------------
# Stack: DUP DROP SWAP OVER ROT NIP TUCK
# ---------------------------------------------------------------------------


def test_dup_duplicates_top():
    assert stack_after([lit(7), call("DUP")]) == [7, 7]


def test_drop_discards_top():
    assert stack_after([lit(1), lit(2), call("DROP")]) == [1]


def test_swap_swaps_top_two():
    assert stack_after([lit(1), lit(2), call("SWAP")]) == [2, 1]


def test_over_copies_second_to_top():
    # ( a b -- a b a )
    assert stack_after([lit(1), lit(2), call("OVER")]) == [1, 2, 1]


def test_rot_rotates_top_three():
    # ( a b c -- b c a )
    assert stack_after([lit(1), lit(2), lit(3), call("ROT")]) == [2, 3, 1]


def test_nip_removes_second():
    # ( a b -- b )
    assert stack_after([lit(1), lit(2), call("NIP")]) == [2]


def test_tuck_copies_top_under_second():
    # ( a b -- b a b )
    assert stack_after([lit(1), lit(2), call("TUCK")]) == [2, 1, 2]


# ---------------------------------------------------------------------------
# Variables: VARIABLE @ !
# ---------------------------------------------------------------------------


def test_variable_initializes_to_zero_and_fetches():
    # VARIABLE counter   counter @  →  ( -- 0 )
    terms = [
        call("VARIABLE"),
        call("counter"),
        call("counter"),
        call("@"),
    ]
    ex = run(terms)
    assert ex.data_stack == [0.0]
    assert ex.variables["counter"] == 0.0


def test_store_then_fetch_round_trip():
    # VARIABLE x   42 x !   x @  →  ( -- 42 )
    terms = [
        call("VARIABLE"),
        call("x"),
        lit(42),
        call("x"),
        call("!"),
        call("x"),
        call("@"),
    ]
    ex = run(terms)
    assert ex.data_stack == [42.0]
    assert ex.variables["x"] == 42.0


def test_store_overwrites_prior_value():
    terms = [
        call("VARIABLE"),
        call("v"),
        lit(1), call("v"), call("!"),
        lit(2), call("v"), call("!"),
        call("v"), call("@"),
    ]
    assert run(terms).data_stack == [2.0]


# ---------------------------------------------------------------------------
# IO: .   (print via world.print → MessagePrintEvent)
# ---------------------------------------------------------------------------


def test_dot_prints_integer_via_world():
    world = MockWorld()
    run([lit(7), call(".")], world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert len(prints) == 1
    assert prints[0].text == "7"


def test_dot_integer_float_renders_without_decimal():
    """`.` should print '3' not '3.0' for clean equivalence with mlog
    `print` which has no implicit decimal on integer values."""
    world = MockWorld()
    run([lit(1), lit(2), call("+"), call(".")], world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert prints[0].text == "3"


def test_dot_division_result_renders_as_number():
    world = MockWorld()
    run([lit(5), lit(2), call("/"), call(".")], world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    # 2.5 is non-integer; render with its Python repr.
    assert prints[0].text == "2.5"


# ---------------------------------------------------------------------------
# Negative cases (M5 — coverage for failure surfaces the contract exposes)
# ---------------------------------------------------------------------------


def test_minus_argument_order_matches_forth_convention():
    """( a b -- a-b ), NOT ( a b -- b-a ). Regression guard."""
    assert stack_after([lit(20), lit(5), call("-")]) == [15]
    assert stack_after([lit(5), lit(20), call("-")]) == [-15]


def test_divide_argument_order_matches_forth_convention():
    """( a b -- a/b ), NOT ( a b -- b/a ). Regression guard."""
    assert stack_after([lit(20), lit(4), call("/")]) == [5.0]


def test_mod_argument_order_matches_forth_convention():
    assert stack_after([lit(17), lit(5), call("MOD")]) == [2]


def test_rot_does_not_reverse():
    """ROT is ( a b c -- b c a ), NOT ( a b c -- c b a )."""
    assert stack_after([lit(10), lit(20), lit(30), call("ROT")]) == [20, 30, 10]


def test_over_does_not_swap_then_dup():
    """OVER is ( a b -- a b a ), confirm OVER preserves both originals."""
    assert stack_after([lit(99), lit(1), call("OVER")]) == [99, 1, 99]


# ---------------------------------------------------------------------------
# Integration: the canonical v1 demo shape (counter increment)
# ---------------------------------------------------------------------------


def test_integration_counter_increment_via_variable():
    """Mini end-to-end: declare counter, increment, fetch, print.

    Exercises VARIABLE + @ + + + ! + . in one program — the shape the
    blink/counter v1 demo will eventually compile to mlog.
    """
    world = MockWorld()
    terms = [
        call("VARIABLE"), call("counter"),
        # counter @ 1 +  counter !
        call("counter"), call("@"), lit(1), call("+"),
        call("counter"), call("!"),
        # counter @ .
        call("counter"), call("@"), call("."),
    ]
    ex = run(terms, world=world)
    assert ex.variables["counter"] == 1.0
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert prints[-1].text == "1"


def test_integration_comparison_and_logical_chain():
    """5 5 = 1 AND  → should leave [1] on the stack."""
    assert stack_after([lit(5), lit(5), call("="), lit(1), call("AND")]) == [1]


def test_integration_full_stack_op_dance():
    """1 2 3 ROT SWAP DUP +
       After ROT: [2, 3, 1]
       After SWAP: [2, 1, 3]
       After DUP: [2, 1, 3, 3]
       After +: [2, 1, 6]
    """
    terms = [
        lit(1), lit(2), lit(3),
        call("ROT"), call("SWAP"), call("DUP"), call("+"),
    ]
    assert stack_after(terms) == [2, 1, 6]


# ---------------------------------------------------------------------------
# register_all wiring contract
# ---------------------------------------------------------------------------


def test_register_all_installs_every_arith_stack_logical_var_io_primitive():
    """The register_all() helper must install Python callables for every
    BuiltinWord whose tag is arith, stack, var, or io. Mindustry-tagged
    primitives (PRINT, PRINTFLUSH, WAIT, SENSOR, GETLINK) are the
    responsibility of bead mforth-10t.12 and are intentionally excluded.

    Control-tag I and J ship with the .10 executor's default primitive
    set; primitives.py does not override them.
    """
    from mforth.dictionary import standard_dictionary, BuiltinWord

    d = standard_dictionary()
    ex = Executor()
    register_all(ex)

    in_scope_tags = {"arith", "stack", "var", "io"}
    for name in [
        # arith
        "+", "-", "*", "/", "MOD",
        "=", "<>", "<", ">", "<=", ">=",
        "AND", "OR", "NOT",
        # stack
        "DUP", "DROP", "SWAP", "OVER", "ROT", "NIP", "TUCK",
        # var (VARIABLE is handled by the executor itself, not a callable)
        "@", "!",
        # io
        ".",
    ]:
        entry = d.lookup(name)
        assert isinstance(entry, BuiltinWord), f"{name} should be a BuiltinWord"
        assert entry.tag in in_scope_tags
        assert ex._primitives.get(name.upper()) is not None, (
            f"primitive {name!r} not registered by register_all"
        )
