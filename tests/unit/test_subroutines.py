"""Unit tests for subroutine emission via the ``@counter`` trick.

Bead mforth-10t.39 — a Tier C size-only fallback codegen pass. The
headline assertion (CLAUDE.md hard rule) is EQUIVALENCE PRESERVATION:
a user word called 2+ times, emitted ONCE as a subroutine, must produce
an IDENTICAL :class:`MockWorld` event stream to the fully-inlined
program — AND fewer total instructions.

The pass is intentionally NOT wired into the default pipeline (bead
mforth-10t.40 owns the ``-O`` levels), so these tests drive it directly:
build a clean :class:`StackcheckResult`, compile it both ways (inlined
via :func:`mforth.backend.mlog.emit.emit`, subroutine via
:func:`mforth.backend.mlog.subroutines.emit_with_subroutines`), run both
through the in-repo mlog interpreter, and compare events.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.mlog.subroutines import (
    SubroutineConfig,
    emit_with_subroutines,
    select_promotions,
)
from mforth.backend.world import MockWorld
from mforth.dictionary import resolve, standard_dictionary
from mforth.mlog_interp import MlogInterpreter
from mforth.parse import parse
from mforth.stackcheck import stackcheck


# A word whose body lowers to many instructions (each `a b + PRINT` group
# emits `set s0 a`, `set s1 b`, `op add s0 s0 s1`, `print s0` = 4 instrs;
# five groups = 20 instructions), called twice in main.
BIG_PROGRAM = """
: BIG
  1 2 + PRINT
  3 4 + PRINT
  5 6 + PRINT
  7 8 + PRINT
  9 10 + PRINT
;
BIG
BIG
"""


def _compile(source: str):
    """Parse → resolve → stackcheck → allocate. Returns (result, slots)."""
    program = parse(source, file="<test>")
    dictionary = resolve(program, dictionary=standard_dictionary())
    result = stackcheck(program, dictionary=dictionary)
    slots = allocate_slots(result)
    return result, slots


def _count_instrs(instrs: list) -> int:
    """Count executable instructions (label sentinels consume no line)."""
    return sum(1 for (_lab, op, _ops) in instrs if op is not None)


def _to_text(instrs: list) -> str:
    """Serialize resolved instruction tuples (no symbolic labels left)
    to interpreter-ready mlog text."""
    lines = ["# test"]
    for _label, opcode, operands in instrs:
        if opcode is None:
            continue
        lines.append(" ".join((opcode, *operands)))
    return "\n".join(lines) + "\n"


def _run_text(text: str, iterations: int = 1) -> list:
    world = MockWorld()
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=iterations)
    return list(world.events)


def _events_equal(a_list: list, b_list: list) -> bool:
    if len(a_list) != len(b_list):
        return False
    for a, b in zip(a_list, b_list):
        if type(a) is not type(b):
            return False
        if not (is_dataclass(a) and is_dataclass(b)):
            if a != b:
                return False
            continue
        for f in fields(a):
            if f.name == "timestamp":
                continue
            if getattr(a, f.name) != getattr(b, f.name):
                return False
    return True


def test_subroutine_emission_preserves_events_and_shrinks() -> None:
    """EQUIVALENCE: the subroutine-emitted program yields the SAME event
    stream as the inlined one AND fewer total instructions."""
    result, slots = _compile(BIG_PROGRAM)

    inlined = emit(result, slots)
    from mforth.backend.mlog.finalize import resolve_labels

    inlined_resolved = resolve_labels(inlined)
    inlined_text = _to_text(inlined_resolved)

    from mforth.backend.mlog.subroutines import resolve_subroutine_labels

    sub = emit_with_subroutines(result, slots, force_promote=["BIG"])
    sub_resolved = resolve_subroutine_labels(sub)
    sub_text = _to_text(sub_resolved)

    # The metric improves: fewer instructions when BIG is emitted once.
    n_inlined = _count_instrs(inlined_resolved)
    n_sub = _count_instrs(sub_resolved)
    assert n_sub < n_inlined, (
        f"subroutine emission did not shrink the program: "
        f"inlined={n_inlined} sub={n_sub}\n--- inlined ---\n{inlined_text}\n"
        f"--- sub ---\n{sub_text}"
    )

    # EQUIVALENCE: identical observable events across several iterations
    # so the auto-loop wrap (and the `end` that terminates main before
    # the subroutine bodies) is exercised.
    events_inlined = _run_text(inlined_text, iterations=3)
    events_sub = _run_text(sub_text, iterations=3)
    assert _events_equal(events_inlined, events_sub), (
        f"event streams diverge.\n  inlined={events_inlined!r}\n"
        f"  sub={events_sub!r}\n--- inlined mlog ---\n{inlined_text}\n"
        f"--- sub mlog ---\n{sub_text}"
    )
    # And there must actually BE observable events (guards against both
    # paths silently emitting nothing): 5 prints per BIG call × 2 calls ×
    # 3 iterations.
    assert len(events_sub) == 30


def test_set_at_counter_is_a_computed_jump() -> None:
    """The interpreter must treat `set @counter <line>` as a computed
    goto: the PC takes the written value WITHOUT auto-advancing. This is
    the lever the subroutine-call/return convention rests on."""
    from mforth.backend.world import MessagePrintEvent

    # Line 0: jump over the 'trap' print into the body.
    # `set @counter 3` must jump to line 3 (print good), NOT fall through
    # to line 2 (print bad).
    text = (
        "# header\n"
        "set @counter 3\n"      # line 0 → jump to line 3
        'print "skipped1"\n'    # line 1 (skipped)
        'print "skipped2"\n'    # line 2 (skipped)
        'print "good"\n'        # line 3
        "end\n"                 # line 4
    )
    world = MockWorld()
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert [p.text for p in prints] == ["good"]


def test_heuristic_promotes_only_above_budget() -> None:
    """select_promotions promotes nothing when the inline estimate fits
    the budget, and promotes the big repeated word when it does not."""
    result, slots = _compile(BIG_PROGRAM)
    from mforth.backend.mlog.emit import _fuse_variable_patterns

    fused = _fuse_variable_patterns(result.program, result.dictionary)
    fused_result = stackcheck(fused, dictionary=result.dictionary)
    fused_slots = allocate_slots(fused_result)

    # Generous budget: inline fits → promote nothing.
    big_budget = SubroutineConfig(max_instructions=10_000)
    assert select_promotions(fused_result, fused_slots, big_budget) == []

    # Tight budget below the ~40-instruction inline estimate → promote BIG.
    tight_budget = SubroutineConfig(max_instructions=5)
    promoted = select_promotions(fused_result, fused_slots, tight_budget)
    assert "BIG" in promoted


def test_subroutine_word_emitted_once() -> None:
    """The promoted word's body appears exactly ONCE in the output even
    though it is called twice — that is the size win."""
    result, slots = _compile(BIG_PROGRAM)
    sub = emit_with_subroutines(result, slots, force_promote=["BIG"])
    # The subroutine body's distinctive instruction (`op add` of 9 and 10)
    # should appear once. Count `op add ... 9 10`-style adds by counting
    # the literal-9 sets, which only occur in BIG's body.
    set_nine = sum(
        1
        for (_lab, op, ops) in sub
        if op == "set" and len(ops) == 2 and ops[1] == "9"
    )
    assert set_nine == 1
