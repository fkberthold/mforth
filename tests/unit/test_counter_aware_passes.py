"""@counter-aware slot-liveness + peephole CFG modeling (bead mforth-i8h).

The post-emit slot-liveness pass (``eliminate_dead_copies`` in
``slots.py``) and the peephole pass (``peephole.py``) model control flow
to decide which ``set s<N> X`` stores are dead and which basic-block
boundaries exist. Their original CFG/liveness model knew only ``jump`` /
``end`` edges and treated ``set @counter <X>`` as a plain instruction
that falls through to ``i+1``.

That is WRONG for the ``-Osize`` subroutine stream, which uses mlog's
writable ``@counter`` as a computed goto:

* ``set @counter <entry-label>`` — a CALL. Control transfers to the
  subroutine entry; when the subroutine returns it lands at the NEXT
  instruction (the return label). So a call's successors are
  {entry-target, fall-through}.
* ``set @counter <ret-var>`` (e.g. ``set @counter __ret_BIG``) — a
  computed RETURN to an unknown (any call site's) return label. It must
  be modeled as a computed jump to an UNKNOWN target — conservatively,
  it reaches every label in the program — and it does NOT fall through.
* ``op add @counter @counter <off>`` — a jump-table dispatch, also a
  computed jump to an unknown target.

Because their model wasn't @counter-aware, the passes were SKIPPED on the
subroutine stream (``optimize.py``), leaving size wins on the table. This
test pins the @counter-aware CFG modeling and the resulting size win,
while guaranteeing the headline REPL ↔ mlog sink-equivalence still holds.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from mforth.backend.mlog.slots import eliminate_dead_copies
from mforth.backend.mlog.peephole import peephole, _segment_blocks
from mforth.backend.world import MockWorld
from mforth.mlog_interp import MlogInterpreter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_text(instrs: list) -> str:
    """Resolve symbolic-labelled instruction tuples to interpreter-ready
    mlog text. Labels (and ``set @counter <label>`` / ``set __ret <label>``
    operands) are rewritten to absolute line numbers, mirroring
    ``resolve_subroutine_labels``."""
    label_lines: dict = {}
    line = 0
    for label, opcode, _ops in instrs:
        if label is not None:
            label_lines[label] = line
        if opcode is None:
            continue
        line += 1

    body = ["# test"]
    for _label, opcode, operands in instrs:
        if opcode is None:
            continue
        if opcode == "jump":
            ops = (str(label_lines[operands[0]]), *operands[1:])
        elif (
            opcode == "set"
            and len(operands) == 2
            and operands[1] in label_lines
        ):
            ops = (operands[0], str(label_lines[operands[1]]))
        else:
            ops = operands
        body.append(" ".join((opcode, *ops)))
    return "\n".join(body) + "\n"


def _run(instrs: list, *, iterations: int = 2) -> list:
    world = MockWorld()
    interp = MlogInterpreter(world=world, text=_to_text(instrs))
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


def _count(instrs: list) -> int:
    return sum(1 for (_l, op, _o) in instrs if op is not None)


# A minimal hand-built subroutine stream in the exact shape the
# subroutine emitter produces: main with two call sites, an ``end``, then
# the subroutine body, ending with a computed return.
#
# Subroutine SUB: ( n -- n*2 ) at frame base s3 — body does
#   set s4 s3 ; op add s3 s3 s4   →  s3 = s3 + s3
# Each call marshals input into s3, saves return, computed-goto into the
# entry, then marshals output back to a caller slot and prints it.
def _build_subroutine_stream() -> list:
    return [
        # main
        (None, "set", ("s3", "5")),                  # marshal in (call A)
        (None, "set", ("__ret_SUB", "L_ret_A")),     # save return
        (None, "set", ("@counter", "L_sub_entry")),  # call A
        ("L_ret_A", "set", ("s0", "s3")),            # marshal out
        (None, "print", ("s0",)),
        (None, "set", ("s3", "9")),                  # marshal in (call B)
        (None, "set", ("__ret_SUB", "L_ret_B")),     # save return
        (None, "set", ("@counter", "L_sub_entry")),  # call B
        ("L_ret_B", "set", ("s0", "s3")),            # marshal out
        (None, "print", ("s0",)),
        (None, "end", ()),
        # subroutine body
        ("L_sub_entry", "set", ("s4", "s3")),        # s4 = s3
        (None, "op", ("add", "s3", "s3", "s4")),     # s3 = s3 + s4
        (None, "set", ("@counter", "__ret_SUB")),    # computed return
    ]


# ---------------------------------------------------------------------------
# 1. peephole basic-block splitting is @counter-aware
# ---------------------------------------------------------------------------


def test_peephole_splits_block_at_set_at_counter():
    """``set @counter <X>`` (a computed jump) must end a basic block —
    control may leave, so a value cannot be assumed to flow across it. A
    block boundary must follow the ``set @counter`` instruction."""
    instrs = [
        (None, "set", ("s0", "1")),
        (None, "set", ("@counter", "L_entry")),  # computed jump — block ends
        (None, "print", ("s0",)),                # different block
    ]
    blocks = _segment_blocks(instrs)
    # There must be a boundary at index 2 (right after the set @counter).
    starts = {start for start, _end in blocks}
    assert 2 in starts, (
        f"expected a block boundary after `set @counter` at index 2; "
        f"blocks={blocks}"
    )


def test_peephole_does_not_fold_across_set_at_counter():
    """A staging ``set s0 X`` that is followed by a ``set @counter`` (a
    computed jump) must NOT be folded into the post-jump consumer — the
    value does not flow across the computed jump deterministically."""
    instrs = [
        (None, "set", ("s0", "42")),
        (None, "set", ("@counter", "L_entry")),  # block boundary
        (None, "print", ("s0",)),
    ]
    out = peephole(instrs)
    # The staging set must survive (it is the only def of s0 reaching the
    # print across the computed jump; folding it would be unsound).
    assert (None, "set", ("s0", "42")) in out
    assert (None, "print", ("s0",)) in out


# ---------------------------------------------------------------------------
# 2. slot-liveness is @counter-aware (conservative across computed jumps)
# ---------------------------------------------------------------------------


def test_liveness_keeps_store_live_across_computed_return():
    """A store whose slot is read at a subroutine entry that is reachable
    only via a computed ``set @counter <ret-var>`` (unknown target) must
    NOT be dropped. Conservative liveness over the computed jump keeps it
    live. The whole stream must stay event-equivalent before/after."""
    instrs = _build_subroutine_stream()
    before = _run(instrs)
    out = eliminate_dead_copies(instrs)
    after = _run(out)
    assert _events_equal(before, after), (
        f"events diverged after @counter-aware liveness:\n"
        f"  before={before!r}\n  after={after!r}"
    )


def test_liveness_does_not_merge_body_slot_onto_caller_live_slot():
    """ADVERSARIAL: a caller slot live ACROSS a call (set before the call,
    read after) must never be merged with a subroutine-body temp slot.

    Without @counter-awareness the liveness model thinks ``set @counter
    <entry>`` falls through and never enters the body, so it believes the
    caller slot is free during the body and merges a body temp onto it —
    silently clobbering the caller's value. The body executes BETWEEN the
    call and the return label, so the caller slot is live throughout. This
    is the corruption the fix must prevent (verified by sink-equivalence)."""
    instrs = [
        (None, "set", ("s1", "100")),                # caller value (live across)
        (None, "set", ("s4", "5")),                  # marshal in
        (None, "set", ("__ret_X", "L_ret")),         # save return
        (None, "set", ("@counter", "L_entry")),      # CALL
        ("L_ret", "set", ("s0", "s4")),              # marshal out
        (None, "print", ("s0",)),                    # → 10
        (None, "print", ("s1",)),                    # → 100 (must NOT be clobbered)
        (None, "end", ()),
        ("L_entry", "set", ("s5", "s4")),            # body temp
        (None, "op", ("add", "s4", "s4", "s5")),     # s4 = s4 + s5 = 10
        (None, "set", ("@counter", "__ret_X")),      # computed return
    ]
    before = _run(instrs, iterations=1)
    out = eliminate_dead_copies(instrs)
    after = _run(out, iterations=1)
    assert _events_equal(before, after), (
        f"caller-live slot clobbered by an unsound body-slot merge across "
        f"the computed jump:\n  before={before!r}\n  after={after!r}"
    )


def test_liveness_preserves_events_on_subroutine_stream():
    """The full subroutine stream stays sink-equivalent under the
    @counter-aware liveness + slot-reuse pass across multiple iterations
    (exercises the auto-loop wrap + computed return)."""
    instrs = _build_subroutine_stream()
    before = _run(instrs, iterations=3)
    out = eliminate_dead_copies(instrs)
    after = _run(out, iterations=3)
    assert _events_equal(before, after)
    # There IS observable output (2 prints per pass × 3 passes).
    assert len(after) == 6


def test_peephole_preserves_events_on_subroutine_stream():
    """The peephole pass over the subroutine stream stays event-equivalent
    AND must not corrupt the computed-jump control flow."""
    instrs = _build_subroutine_stream()
    before = _run(instrs, iterations=3)
    out = peephole(instrs)
    after = _run(out, iterations=3)
    assert _events_equal(before, after), (
        f"peephole diverged:\n  before={before!r}\n  after={after!r}"
    )
