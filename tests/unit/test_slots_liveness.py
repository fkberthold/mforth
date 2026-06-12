"""Unit tests for the v2 slot-allocator liveness + dead-copy pass.

Bead mforth-10t.34. The pass extends the C1 slot allocator
(``src/mforth/backend/mlog/slots.py``) with per-instruction liveness
analysis over the *emitted instruction stream* (the
``list[MlogInstr]`` tuples produced by
:func:`mforth.backend.mlog.emit.emit`).

It does two things, opt-in (the default pipeline never calls it — bead
mforth-10t.40 wires the ``-O`` levels):

1. **Dead-store elimination** — a ``set s<N> X`` whose destination slot
   ``s<N>`` is not live-out at that instruction is a wasted runtime
   cycle; drop it.
2. **Slot reuse (renumbering)** — two stack slots that are never
   simultaneously live can share one mlog variable, shrinking the
   max-slot count so the program stays inside mlog's variable budget.

The headline constraint (CLAUDE.md hard rule) is **equivalence
preservation**: the optimized instruction stream must produce a
BYTE-IDENTICAL observable event sequence when executed through the
in-repo mlog interpreter. The ``test_equivalence_*`` tests below execute
BOTH the un-optimized and the optimized streams and compare events.

The public entry point under test is
:func:`mforth.backend.mlog.slots.eliminate_dead_copies`.
"""

from __future__ import annotations

from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.finalize import finalize
from mforth.backend.mlog.slots import (
    allocate_slots,
    eliminate_dead_copies,
    max_slot_index_of_instrs,
)
from mforth.backend.sidecar import WorldConfig
from mforth.backend.world import MockWorld
from mforth.mlog_interp import MlogInterpreter
from mforth.parse import parse
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize(instrs: list) -> str:
    """Render a straight-line (label-free, jump-free) instruction list as
    mlog text the interpreter can execute. Mirrors
    ``finalize.write_mlog``'s body shape but skips label resolution
    (these test fixtures have no control flow)."""
    body = []
    for label, opcode, operands in instrs:
        assert label is None, f"unexpected label sentinel: {label!r}"
        assert opcode is not None, "unexpected (label, None, None) sentinel"
        body.append(" ".join((opcode, *operands)))
    return "# test\n" + "\n".join(body) + "\n"


def _events(instrs: list, *, user_variables: set | None = None) -> list:
    world = MockWorld()
    interp = MlogInterpreter(
        world=world,
        text=_serialize(instrs),
        user_variables=user_variables or set(),
    )
    interp.run(iterations=1)
    return list(world.events)


def _events_for_source(src: str, instrs: list) -> list:
    world = MockWorld()
    interp = MlogInterpreter(world=world, text=_serialize(instrs))
    interp.run(iterations=1)
    return list(world.events)


def _emit_instrs(src: str) -> list:
    prog = parse(src, file="<test>")
    result = stackcheck(prog)
    slots = allocate_slots(result)
    return emit(result, slots)


# ---------------------------------------------------------------------------
# Dead-store elimination
# ---------------------------------------------------------------------------


def test_drops_a_single_dead_store():
    """A ``set s0 1`` whose slot is overwritten before any read is dead."""
    instrs = [
        (None, "set", ("s0", "1")),   # dead: s0 overwritten below, never read
        (None, "set", ("s0", "2")),
        (None, "print", ("s0",)),
    ]
    out = eliminate_dead_copies(instrs)
    assert (None, "set", ("s0", "1")) not in out
    assert (None, "set", ("s0", "2")) in out
    assert (None, "print", ("s0",)) in out


def test_keeps_a_live_store():
    """A ``set s0 X`` that IS read downstream must survive."""
    instrs = [
        (None, "set", ("s0", "7")),
        (None, "print", ("s0",)),
    ]
    out = eliminate_dead_copies(instrs)
    assert out == instrs


def test_never_drops_a_user_variable_store():
    """``set <uservar> s0`` is an observable VARIABLE write (Forth ``!``)
    — never a stack-slot dead store, must always survive even though the
    destination is not an ``s<N>`` slot."""
    instrs = [
        (None, "set", ("s0", "5")),
        (None, "set", ("counter", "s0")),  # store to a user variable
        (None, "set", ("counter", "s0")),  # second store, also kept
    ]
    out = eliminate_dead_copies(instrs)
    kept = [i for i in out if i[1] == "set" and i[2][0] == "counter"]
    assert len(kept) == 2


# ---------------------------------------------------------------------------
# Acceptance: 10+ dead stores dropped, max slot count drops >= 30%.
# ---------------------------------------------------------------------------


def test_acceptance_ten_dead_stores_and_slot_reduction():
    """Contrived program: twelve ``set s<N> <lit>`` whose slots are
    overwritten (or never read) before use, plus one live chain.

    The un-optimized stream uses s0..s11 (12 slots, max index 11). Each
    push lands in a fresh slot and is immediately clobbered by the next
    push into the same logical top, so eleven of them are dead. After the
    pass the dead stores are gone and the surviving live values collapse
    onto a single reused slot (max index 0), a 100% reduction (>= 30%).
    """
    instrs = []
    # Twelve dead stores into distinct slots, none ever read.
    for n in range(12):
        instrs.append((None, "set", (f"s{n}", str(n))))
    # One live value the program actually observes, in a high slot.
    instrs.append((None, "set", ("s12", "99")))
    instrs.append((None, "print", ("s12",)))

    before_max = max_slot_index_of_instrs(instrs)
    out = eliminate_dead_copies(instrs)

    dropped = len(instrs) - len(out)
    assert dropped >= 10, f"expected >=10 dead stores dropped, got {dropped}"

    after_max = max_slot_index_of_instrs(out)
    # Slots count = max_index + 1. Require >= 30% reduction.
    before_count = before_max + 1
    after_count = after_max + 1
    reduction = (before_count - after_count) / before_count
    assert reduction >= 0.30, (
        f"slot count reduction {reduction:.0%} < 30% "
        f"(before={before_count}, after={after_count})"
    )

    # The observable print must still reference a live slot.
    prints = [i for i in out if i[1] == "print"]
    assert len(prints) == 1


# ---------------------------------------------------------------------------
# EQUIVALENCE PRESERVATION (the hard rule) — optimized stream yields an
# IDENTICAL event sequence to the un-optimized stream.
# ---------------------------------------------------------------------------


def test_equivalence_handwritten_dead_store_program():
    """Execute the contrived dead-store program both ways; the event
    sequences must be identical."""
    instrs = []
    for n in range(12):
        instrs.append((None, "set", (f"s{n}", str(n))))
    instrs.append((None, "set", ("s12", "99")))
    instrs.append((None, "print", ("s12",)))

    out = eliminate_dead_copies(instrs)

    events_before = _events(instrs)
    events_after = _events(out)
    assert events_before == events_after
    assert len(events_after) == 1  # the single print


def test_equivalence_arithmetic_pipeline_from_emit():
    """Real emit() output for an arithmetic + print program survives the
    pass with an identical event stream."""
    src = "1 2 + 3 * 4 - . 10 20 + ."
    instrs = _emit_instrs(src)

    before = _events_for_source(src, instrs)
    out = eliminate_dead_copies(instrs)
    after = _events_for_source(src, out)

    assert before == after
    assert len(after) >= 2  # two `.` prints


def test_equivalence_stack_churn_from_emit():
    """A program with lots of intermediate dead values (DROP-heavy) keeps
    identical events after the pass and uses no more slots than before."""
    src = "1 DROP 2 DROP 3 DROP 4 DROP 5 . 6 7 + 8 + ."
    instrs = _emit_instrs(src)

    before = _events_for_source(src, instrs)
    out = eliminate_dead_copies(instrs)
    after = _events_for_source(src, out)

    assert before == after
    assert max_slot_index_of_instrs(out) <= max_slot_index_of_instrs(instrs)


def _finalize_events(src: str, instrs: list) -> list:
    """Run a full ``finalize`` → interpreter pass on an instruction stream
    that may contain control flow (labels + jumps). Returns the events."""
    from pathlib import Path

    mlog_text = finalize(
        instrs,
        world_config=WorldConfig(),
        source_path=Path("<test>.fs"),
        sidecar_path=None,
    )
    world = MockWorld()
    interp = MlogInterpreter(world=world, text=mlog_text)
    interp.run(iterations=1)
    return list(world.events)


def test_equivalence_control_flow_if_else_from_emit():
    """A program with IF/ELSE/THEN (jumps + label sentinels) keeps an
    identical event stream after the pass. This exercises the CFG
    backward-liveness fixpoint, not just straight-line code — the
    load-bearing correctness mechanism for equivalence preservation."""
    src = "5 3 > IF 100 . ELSE 200 . THEN 7 ."
    instrs = _emit_instrs(src)

    before = _finalize_events(src, instrs)
    out = eliminate_dead_copies(instrs)
    after = _finalize_events(src, out)

    assert before == after
    assert len(after) >= 2  # the taken branch's `.` plus the trailing `7 .`


def test_pass_is_idempotent():
    """Applying the pass twice yields the same result as applying it
    once — a fixed point, which guards against partial elimination."""
    instrs = []
    for n in range(6):
        instrs.append((None, "set", (f"s{n}", str(n))))
    instrs.append((None, "set", ("s6", "42")))
    instrs.append((None, "print", ("s6",)))

    once = eliminate_dead_copies(instrs)
    twice = eliminate_dead_copies(once)
    assert once == twice
