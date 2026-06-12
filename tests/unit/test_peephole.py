"""Unit tests for the post-emit peephole optimizer — bead mforth-10t.33.

The peephole pass (:func:`mforth.backend.mlog.peephole.peephole`)
collapses ``set``/use round-trips over the ``(label, opcode, operands)``
instruction tuples the emitter produces. This module pins:

* each collapse pattern from the bead spec (constant inlining, the
  read-after-write op fold, single-use copy elimination);
* the intra-block liveness guard (no fold when the slot stays live —
  read again, or read across a label / jump boundary);
* operand-position safety (never fold an opcode-secondary token like
  ``op``'s operation name or ``jump``'s condition);
* a >=20% instruction shrink on a typical arithmetic-heavy program; and
* THE EQUIVALENCE PROPERTY (CLAUDE.md headline test class): the
  optimized instruction stream, run through the in-repo mlog
  interpreter, produces an IDENTICAL event sequence to the un-optimized
  stream. The pass only counts if events are identical AND the
  instruction count strictly drops.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

import pytest

from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.finalize import finalize
from mforth.backend.mlog.peephole import peephole
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.sidecar import WorldConfig
from mforth.backend.world import MockWorld
from mforth.dictionary import resolve, standard_dictionary
from mforth.mlog_interp import MlogInterpreter
from mforth.parse import parse
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compile(src: str) -> list:
    """Parse → resolve → stackcheck → allocate → emit; return raw tuples."""
    program = parse(src, file="<peephole-test>")
    dictionary = resolve(program, dictionary=standard_dictionary())
    result = stackcheck(program, dictionary=dictionary)
    slots = allocate_slots(result)
    return emit(result, slots)


def _to_text(instrs: list, *, user_vars: set) -> tuple[str, set]:
    """Finalize an instruction list to mlog text + the user-variable set
    the interpreter needs (source-declared VARIABLEs)."""
    text = finalize(
        instrs,
        world_config=WorldConfig(),
        source_path="<peephole-test>.fs",
        sidecar_path=None,
    )
    return text, user_vars


def _user_vars(src: str) -> set:
    program = parse(src, file="<peephole-test>")
    dictionary = resolve(program, dictionary=standard_dictionary())
    from mforth.dictionary import UserVariable

    return {
        e.name
        for e in dictionary._entries.values()  # noqa: SLF001
        if isinstance(e, UserVariable)
    }


def _run(text: str, user_vars: set, *, iterations: int = 1) -> list:
    world = MockWorld()
    interp = MlogInterpreter(world=world, text=text, user_variables=user_vars)
    interp.run(iterations=iterations)
    return list(world.events)


def _payload_eq(a, b) -> bool:
    """Class + payload equality, ignoring ``timestamp`` (mirrors the
    headline equivalence runner)."""
    if type(a) is not type(b):
        return False
    if not (is_dataclass(a) and is_dataclass(b)):
        return a == b
    for f in fields(a):
        if f.name == "timestamp":
            continue
        if getattr(a, f.name) != getattr(b, f.name):
            return False
    return True


def _assert_equivalent(src: str, *, iterations: int = 2) -> tuple[int, int]:
    """Compile ``src``, run un-optimized vs peephole-optimized through the
    interpreter, assert identical events, return ``(orig_count,
    opt_count)`` (post-finalize executable line counts)."""
    raw = _compile(src)
    opt = peephole(raw)
    uv = _user_vars(src)

    raw_text, _ = _to_text(raw, user_vars=uv)
    opt_text, _ = _to_text(opt, user_vars=uv)

    ev_raw = _run(raw_text, uv, iterations=iterations)
    ev_opt = _run(opt_text, uv, iterations=iterations)

    assert len(ev_raw) == len(ev_opt), (
        f"event count diverged: un-opt={len(ev_raw)} opt={len(ev_opt)}\n"
        f"  un-opt: {ev_raw!r}\n  opt:    {ev_opt!r}"
    )
    for i, (r, o) in enumerate(zip(ev_raw, ev_opt)):
        assert _payload_eq(r, o), (
            f"event[{i}] diverged:\n  un-opt={r!r}\n  opt={o!r}"
        )
    return len(raw), len(opt)


# ---------------------------------------------------------------------------
# Pattern tests — direct on tuples
# ---------------------------------------------------------------------------


def test_constant_inlining_at_op_read() -> None:
    """``set s0 LIT ; op add s1 s2 s0`` → ``op add s1 s2 LIT``."""
    instrs = [
        (None, "set", ("s0", "5")),
        (None, "op", ("add", "s1", "s2", "s0")),
        (None, "print", ("s1",)),
    ]
    out = peephole(instrs)
    assert out == [
        (None, "op", ("add", "s1", "s2", "5")),
        (None, "print", ("s1",)),
    ]


def test_read_after_write_op_fold() -> None:
    """``set s0 X ; op add s0 s0 Y`` → ``op add s0 X Y`` (the consumer's
    own write to s0 kills the staging definition even though s0 is read
    afterwards)."""
    instrs = [
        (None, "set", ("s0", "x")),
        (None, "op", ("add", "s0", "s0", "y")),
        (None, "print", ("s0",)),
    ]
    out = peephole(instrs)
    assert out == [
        (None, "op", ("add", "s0", "x", "y")),
        (None, "print", ("s0",)),
    ]


def test_single_use_copy_elimination() -> None:
    """``set s0 X ; set RESULT s0`` → ``set RESULT X``."""
    instrs = [
        (None, "set", ("s0", "x")),
        (None, "set", ("result", "s0")),
    ]
    out = peephole(instrs)
    assert out == [(None, "set", ("result", "x"))]


def test_double_read_both_positions_fold() -> None:
    """``set s0 7 ; op add s1 s0 s0`` → ``op add s1 7 7`` (both read
    positions rewritten, slot then dead)."""
    instrs = [
        (None, "set", ("s0", "7")),
        (None, "op", ("add", "s1", "s0", "s0")),
    ]
    out = peephole(instrs)
    assert out == [(None, "op", ("add", "s1", "7", "7"))]


def test_fold_into_jump_compared_operand() -> None:
    """``set s0 3 ; jump 5 equal s0 s1`` folds into the compared operand,
    NOT the target line or the condition token."""
    instrs = [
        (None, "set", ("s0", "3")),
        (None, "jump", ("5", "equal", "s0", "s1")),
    ]
    out = peephole(instrs)
    assert out == [(None, "jump", ("5", "equal", "3", "s1"))]


def test_fold_magic_var_value() -> None:
    """The staging value may be an ``@``-magic var, not just a literal."""
    instrs = [
        (None, "set", ("s0", "@time")),
        (None, "print", ("s0",)),
    ]
    out = peephole(instrs)
    assert out == [(None, "print", ("@time",))]


# ---------------------------------------------------------------------------
# Liveness / safety guards
# ---------------------------------------------------------------------------


def test_no_fold_when_slot_read_again() -> None:
    """A slot used by two later instructions is live — no fold."""
    instrs = [
        (None, "set", ("s0", "5")),
        (None, "print", ("s0",)),
        (None, "print", ("s0",)),
    ]
    assert peephole(instrs) == instrs


def test_no_fold_when_consumer_keeps_slot_live() -> None:
    """Consumer reads s0 but writes a DIFFERENT slot, and s0 is read
    later — the staging def is still live, no fold."""
    instrs = [
        (None, "set", ("s0", "5")),
        (None, "op", ("add", "s1", "s0", "y")),
        (None, "print", ("s0",)),
    ]
    assert peephole(instrs) == instrs


def test_no_fold_across_label_boundary() -> None:
    """A labelled consumer is a jump target reachable from elsewhere —
    folding would lose the value on the other entry edge. No fold."""
    instrs = [
        (None, "set", ("s0", "5")),
        ("L_loop", "print", ("s0",)),
    ]
    assert peephole(instrs) == instrs


def test_no_fold_across_label_sentinel() -> None:
    """A ``(label, None, None)`` sentinel sits between the set and its
    apparent consumer — they are in different blocks; no fold."""
    instrs = [
        (None, "set", ("s0", "5")),
        ("L_end", None, None),
        (None, "print", ("s0",)),
    ]
    assert peephole(instrs) == instrs


def test_no_fold_into_non_adjacent_consumer() -> None:
    """An unrelated instruction sits between the set and the s0 read —
    the set's consumer is not the immediately following instruction, so
    this simple peephole leaves it alone."""
    instrs = [
        (None, "set", ("s0", "5")),
        (None, "print", ("other",)),
        (None, "print", ("s0",)),
    ]
    # The intervening print reads `other` (a bare name, not a slot); s0
    # is first read two instructions later. The pass only folds the
    # immediately-adjacent consumer, so no change.
    assert peephole(instrs) == instrs


def test_named_variable_set_not_folded() -> None:
    """A ``set`` whose destination is a named variable (not ``s<N>``) is
    never the trigger for a fold — only emitter slots are folded."""
    instrs = [
        (None, "set", ("__swap_tmp", "s0")),
        (None, "set", ("s1", "__swap_tmp")),
    ]
    # __swap_tmp is not an s<N> slot, so it is not a fold trigger and not
    # a foldable value-into-slot target here.
    assert peephole(instrs) == instrs


def test_does_not_mutate_input() -> None:
    instrs = [
        (None, "set", ("s0", "5")),
        (None, "print", ("s0",)),
    ]
    snapshot = list(instrs)
    peephole(instrs)
    assert instrs == snapshot


# ---------------------------------------------------------------------------
# Shrink + equivalence on compiled programs
# ---------------------------------------------------------------------------


def test_shrink_on_arithmetic_heavy_program() -> None:
    """Acceptance: a typical arithmetic-heavy program shrinks by >=20%."""
    src = ": sq DUP * ; 3 sq 4 + . 10 2 / . 2 3 * 4 + ."
    raw = _compile(src)
    opt = peephole(raw)
    assert len(opt) < len(raw)
    shrink = (len(raw) - len(opt)) / len(raw)
    assert shrink >= 0.20, (
        f"expected >=20% shrink, got {shrink:.0%} ({len(raw)}→{len(opt)})"
    )


@pytest.mark.parametrize(
    "src",
    [
        # arithmetic + comparison sinks through `.`
        "3 4 + . 10 2 / . 7 2 MOD .",
        # DUP/stack ops that emit set-copies
        "5 DUP * . 2 3 SWAP - .",
        # comparison
        "5 3 > . 4 4 = .",
        # user-def inlining
        ": dbl DUP + ; 21 dbl .",
        # VARIABLE round-trip (exercises VariableRead/Write events)
        "VARIABLE c 0 c ! c @ 1 + c ! c @ .",
        # nested arithmetic
        "2 3 * 4 5 * + .",
    ],
    ids=[
        "arith",
        "stack_ops",
        "compare",
        "user_def",
        "variable",
        "nested",
    ],
)
def test_peephole_preserves_events(src: str) -> None:
    """THE EQUIVALENCE PROPERTY: optimized stream yields IDENTICAL events
    to the un-optimized stream when both run through the interpreter."""
    orig, opt = _assert_equivalent(src)
    # Most of these shrink; at minimum the pass must never GROW the stream
    # and must preserve events (the equivalence assertion above).
    assert opt <= orig


def test_peephole_strictly_shrinks_and_preserves() -> None:
    """A program that definitely contains a foldable round-trip: events
    identical AND instruction count strictly drops."""
    src = "3 sq_unused_marker" if False else "2 3 + 4 * ."
    orig, opt = _assert_equivalent(src)
    assert opt < orig
