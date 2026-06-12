"""Unit tests for the `0=` word ( n -- flag ) — bead mforth-0fd.

`0=` returns 1 when the top of stack equals 0, else 0 (mlog's 0/1
boolean encoding — NOT Forth's -1/0; see primitives.py docstring). It
is the zero-test predicate the v1 blink demo uses to toggle a flag.

Three surfaces are pinned here:

* dictionary — `0=` registered with stack effect ( n -- flag ) = (1, 1).
* host REPL — pop n, push 1 if n == 0 else 0 (same 0/1 encoding the
  other comparisons use).
* mlog emit — lowers to `op equal s<out> s<in> 0` (mirrors how `=`
  lowers, with the literal `0` as the second comparand). `0=` is unary
  on the stack but binary on the mlog side (compare against constant 0),
  exactly like NOT's `op not s<i> s<i> 0` shape.
"""

from __future__ import annotations

from mforth.backend.host import Executor
from mforth.backend.mlog.emit import MlogInstr, emit
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.primitives import register_all
from mforth.backend.world import MockWorld
from mforth.dictionary import StackEffect, standard_dictionary
from mforth.parse import LitInt, Program, SrcLoc, WordCall, parse
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _L() -> SrcLoc:
    return SrcLoc("<test>", 1, 1)


def stack_after(terms: list) -> list:
    program = Program(definitions=[], main=terms)
    result = stackcheck(program)
    ex = Executor(world=MockWorld())
    register_all(ex)
    ex.execute(result)
    return ex.data_stack


def compile_to_tuples(src: str) -> list[MlogInstr]:
    prog = parse(src, file="<test>")
    result = stackcheck(prog)
    sm = allocate_slots(result)
    return emit(result, sm)


# ---------------------------------------------------------------------------
# Dictionary
# ---------------------------------------------------------------------------


def test_zero_equals_registered_with_unary_effect():
    entry = standard_dictionary().lookup("0=")
    assert entry is not None, "0= must be in the v1 dictionary"
    assert entry.stack_effect == StackEffect(1, 1)


# ---------------------------------------------------------------------------
# Host REPL primitive
# ---------------------------------------------------------------------------


def test_zero_equals_true_when_top_is_zero():
    assert stack_after([LitInt(0, _L()), WordCall("0=", _L())]) == [1]


def test_zero_equals_false_when_top_is_nonzero():
    assert stack_after([LitInt(5, _L()), WordCall("0=", _L())]) == [0]


def test_zero_equals_uses_mlog_zero_one_encoding_not_forth():
    # Must never return -1 (Forth tradition) — that would break the
    # REPL <-> mlog equivalence property.
    for n in (0, 5, -3):
        result = stack_after([LitInt(n, _L()), WordCall("0=", _L())])[0]
        assert result in (0, 1), f"0= returned {result!r}, must be 0 or 1"


# ---------------------------------------------------------------------------
# mlog emit
# ---------------------------------------------------------------------------


def test_zero_equals_emits_op_equal_against_literal_zero():
    """`5 0=` → push 5 into s0, then `op equal s0 s0 0`."""
    instrs = compile_to_tuples("5 0=")
    assert instrs == [
        (None, "set", ("s0", "5")),
        (None, "op", ("equal", "s0", "s0", "0")),
    ]
