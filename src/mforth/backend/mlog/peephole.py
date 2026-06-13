"""Post-emit peephole optimizer — bead mforth-10t.33 (v2, Tier B).

A standalone pass over the ``(label, opcode, operands)`` instruction
tuples produced by :func:`mforth.backend.mlog.emit` (the SAME shape
:mod:`mforth.backend.mlog.finalize` consumes). It collapses
``set``/use round-trips: when an emitter-internal stack slot ``s<N>``
is *set* to some value and that value is consumed by exactly the next
instruction(s), the staging ``set`` can be folded into the consumer
and dropped — provided the slot is **dead after the use**.

Why fast > small here
=====================

Every collapsed pair is one FEWER instruction executed per processor
tick (a ``set`` that never runs) AND one fewer line of mlog text. It
wins on BOTH axes — the rare optimization where mforth's
fast-over-small priority (``mforth-opt-priority``) has no tension. The
pass is purely local: it never reorders observable side-effects
(``print`` / ``printflush`` / ``sensor`` / ``wait`` / ``control``
stay in place and in order), it only removes a dead copy and rewrites
a downstream operand to reference the copy's source directly.

Patterns collapsed (v1 instruction set)
=======================================

Let ``s`` be an emitter stack slot (matches ``s\\d+``) that is DEAD
after the consumer:

1. **Constant / copy inlining at a use** —
   ``set s X ; <instr reads s>`` → ``<instr reads X>``.
   The consumer may be ``op`` (either read position), ``print``,
   ``printflush``, ``wait``, ``sensor`` (block or prop), ``getlink``
   (index), ``control``, ``jump`` (either compared operand), or another
   ``set`` (single-use copy elimination: ``set s X ; set R s`` →
   ``set R X``). ``X`` may be a literal, an ``@``-magic var, a user
   variable name, or another slot.

2. **Read-after-write fold into an op** —
   ``set s X ; op <f> s s Y`` → ``op <f> s X Y`` (and the symmetric
   ``op <f> s Y s`` → ``op <f> s Y X``). The destination slot reuse is
   safe because the ``set`` value is consumed in the very next
   instruction and the slot is rewritten by that same ``op``.

These are special cases of pattern (1): in every case the staging
``set`` writes a slot whose ONLY subsequent reference is the immediately
following instruction, after which the slot is dead.

Liveness (intra-block, self-contained)
======================================

"Dead after the use" is decided by a self-contained intra-block
liveness scan implemented in this module (NO dependency on the slots
allocator or the O2 liveness bead .34). The instruction stream is split
into basic blocks at every label boundary (a labelled instruction or a
``(label, None, None)`` sentinel can be a jump target, so values cannot
be assumed to flow across it) and after every ``jump`` (control may
leave). Within a block a slot is *dead after instruction k* iff, scanning
the rest of the block, it is never read before being written, AND it is
not read by any instruction in any LATER block (conservative
live-out: a slot referenced anywhere downstream is treated as live-out,
so the fold is suppressed). Only emitter slots (``s\\d+``) are ever
folded — named variables (``__swap_tmp``, ``@time``, user vars,
``__do_idx_<N>``) are left untouched because their lifetimes are not
intra-block-local.

Equivalence
===========

The pass is behaviour-preserving by construction: it removes only dead
``set`` instructions and substitutes a slot operand with the exact token
the dead ``set`` copied into it. The headline REPL ↔ mlog equivalence
property therefore holds — ``tests/unit/test_peephole.py`` ships an
equivalence assertion that compiles a program, runs the un-optimized and
the peephole-optimized instruction streams through the in-repo mlog
interpreter, and asserts an IDENTICAL event sequence (plus a strict
instruction-count shrink).

Standalone / not wired
======================

This module is intentionally NOT wired into ``finalize`` / ``emit`` /
the CLI. Bead mforth-10t.40 owns the ``-O`` level wiring. Until then the
pass is dead code exercised only by its own unit test.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence


# A slot is an emitter-allocated data-stack variable: ``s`` followed by
# one or more digits. ONLY these are candidates for folding — named
# variables (user VARIABLEs, ``__swap_tmp``, ``@``-magic, ``__do_idx_*``)
# have non-block-local lifetimes the intra-block scan can't reason about.
_SLOT_RE = re.compile(r"^s\d+$")


def _is_slot(token: str) -> bool:
    return bool(_SLOT_RE.match(token))


# For each opcode: (write_pos, read_positions). ``write_pos`` is the
# operand index the instruction WRITES (or None if it writes nothing).
# ``read_positions`` are the operand indices the instruction READS as
# values. Operand positions that are neither (opcode-secondary tokens
# like ``op``'s operation name or ``jump``'s condition, the ``jump``
# target line, the ``control`` sub-command) are excluded from both —
# they are never slots and must not be folded.
#
# Shapes (post-emit, pre-finalize):
#   set <dst> <src>                       write=0  reads=(1,)
#   op <operation> <dst> <a> <b>          write=1  reads=(2,3)
#   jump <target> <cond> <a> <b>          write=None reads=(2,3)
#   print <value>                         write=None reads=(0,)
#   printflush <block>                    write=None reads=(0,)
#   wait <seconds>                        write=None reads=(0,)
#   sensor <dst> <block> <prop>           write=0  reads=(1,2)
#   getlink <dst> <i>                     write=0  reads=(1,)
#   control <sub> <block> <a> <b> <c> <d> write=None reads=(1,2,3,4,5)
#   end                                   write=None reads=()
_OPCODE_RW: dict[str, tuple[Optional[int], tuple[int, ...]]] = {
    "set": (0, (1,)),
    "op": (1, (2, 3)),
    "jump": (None, (2, 3)),
    "print": (None, (0,)),
    "printflush": (None, (0,)),
    "wait": (None, (0,)),
    "sensor": (0, (1, 2)),
    "getlink": (0, (1,)),
    "control": (None, (1, 2, 3, 4, 5)),
    "end": (None, ()),
}


def _rw(opcode: str, operands: Sequence[str]) -> tuple[Optional[int], tuple[int, ...]]:
    """Return ``(write_pos, read_positions)`` for an instruction, clamped
    to the actual operand count.

    Unknown opcodes are treated maximally conservatively: every operand
    is a potential read and there is no known write. That guarantees the
    pass never folds across an instruction it doesn't model (it will see
    the slot as still-live and refuse the collapse).
    """
    spec = _OPCODE_RW.get(opcode)
    if spec is None:
        return (None, tuple(range(len(operands))))
    write_pos, reads = spec
    reads = tuple(p for p in reads if p < len(operands))
    if write_pos is not None and write_pos >= len(operands):
        write_pos = None
    return (write_pos, reads)


def _reads_slots(opcode: str, operands: Sequence[str]) -> set[str]:
    """The set of slot tokens this instruction reads as values."""
    _, read_positions = _rw(opcode, operands)
    return {operands[p] for p in read_positions if _is_slot(operands[p])}


def _writes_slot(opcode: str, operands: Sequence[str]) -> Optional[str]:
    """The slot token this instruction writes, or None."""
    write_pos, _ = _rw(opcode, operands)
    if write_pos is None:
        return None
    tok = operands[write_pos]
    return tok if _is_slot(tok) else None


# ---------------------------------------------------------------------------
# Basic-block segmentation
# ---------------------------------------------------------------------------


def _is_block_leader(instr: tuple) -> bool:
    """An instruction begins a new basic block if it carries a label
    (it can be a jump target). Sentinel label tuples ``(label, None,
    None)`` are themselves block leaders."""
    label, _opcode, _operands = instr
    return label is not None


def _is_computed_jump(opcode: str, operands: Sequence[str]) -> bool:
    """True iff the instruction WRITES ``@counter`` — a computed jump
    (bead mforth-i8h). ``-Osize`` subroutine streams use mlog's writable
    ``@counter`` for calls/returns:

      * ``set @counter <entry-or-ret>`` — call / computed return.
      * ``op <f> @counter ...`` — jump-table dispatch.

    Control may LEAVE the block here exactly as it does after a ``jump``,
    so a block boundary must follow. Treating the computed jump as block-
    terminating keeps the intra-block liveness scan from assuming a slot
    value flows across the computed transfer (which would be unsound)."""
    if opcode == "set" and len(operands) >= 1 and operands[0] == "@counter":
        return True
    if opcode == "op" and len(operands) >= 2 and operands[1] == "@counter":
        return True
    return False


def _segment_blocks(instrs: Sequence[tuple]) -> list[tuple[int, int]]:
    """Partition ``instrs`` into ``[start, end)`` index ranges.

    A new block starts at index 0, at any labelled instruction (jump
    target), and immediately after any ``jump`` OR any computed jump
    (``set @counter`` / ``op <f> @counter`` — control may leave; bead
    mforth-i8h). Sentinel tuples ``(label, None, None)`` are kept inside
    the stream and start a block (they mark a jump target's position) but
    are never themselves rewritten.
    """
    if not instrs:
        return []
    boundaries: set[int] = {0, len(instrs)}
    for i, instr in enumerate(instrs):
        if i > 0 and _is_block_leader(instr):
            boundaries.add(i)
        _label, opcode, operands = instr
        if opcode == "jump" or (
            opcode is not None and _is_computed_jump(opcode, operands)
        ):
            boundaries.add(i + 1)
    ordered = sorted(b for b in boundaries if 0 <= b <= len(instrs))
    return [
        (ordered[k], ordered[k + 1]) for k in range(len(ordered) - 1)
    ]


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


def _slots_read_in_range(instrs: Sequence[tuple], start: int, end: int) -> set[str]:
    """Every slot read by any instruction in ``instrs[start:end]``."""
    live: set[str] = set()
    for idx in range(start, end):
        _label, opcode, operands = instrs[idx]
        if opcode is None:
            continue
        live |= _reads_slots(opcode, operands)
    return live


def _dead_after(
    instrs: Sequence[tuple],
    use_idx: int,
    slot: str,
    block_end: int,
) -> bool:
    """Is ``slot`` dead immediately AFTER the instruction at ``use_idx``?

    "Dead" means: from ``use_idx + 1`` to the end of the block, the slot
    is never read before it is (re)written, AND it is not read by any
    later block (conservative live-out — any downstream read anywhere
    suppresses the fold).
    """
    # Intra-block: scan forward; a write before any read kills it (dead),
    # a read before any write keeps it live (not dead).
    for idx in range(use_idx + 1, block_end):
        _label, opcode, operands = instrs[idx]
        if opcode is None:
            continue
        if slot in _reads_slots(opcode, operands):
            return False
        if _writes_slot(opcode, operands) == slot:
            # Re-written before any read inside the block — dead from
            # here intra-block; still must be dead across later blocks.
            return slot not in _slots_read_in_range(
                instrs, block_end, len(instrs)
            )
    # Reached block end without an intra-block read or rewrite: liveness
    # is decided entirely by the conservative cross-block live-out.
    return slot not in _slots_read_in_range(instrs, block_end, len(instrs))


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def peephole(instrs: Sequence[tuple]) -> list:
    """Collapse ``set``/use round-trips over the instruction tuples.

    Returns a NEW ``list`` of ``(label, opcode, operands)`` tuples with
    dead staging ``set``s folded into their consumers. Input is not
    mutated. Idempotent up to a single forward pass: applying it again
    may collapse newly-adjacent pairs, but one pass handles the shapes
    the v1 emitter produces.

    The algorithm walks each basic block. At a ``set s<N> X`` whose
    destination is an emitter slot, it looks at the immediately
    following instruction in the same block; if that instruction READS
    ``s<N>`` and ``s<N>`` is dead after that instruction, the ``set`` is
    dropped and the consumer's matching read operand(s) are rewritten to
    ``X``. The ``set``'s own value token ``X`` is copied verbatim, so a
    literal stays a literal, a magic var stays a magic var, and a slot
    stays a slot.
    """
    instrs = list(instrs)
    n = len(instrs)
    blocks = _segment_blocks(instrs)
    # Indices of `set` instructions we will drop, plus the rewritten
    # replacement for the consumer instruction at a given index.
    drop: set[int] = set()
    rewrite: dict[int, tuple] = {}

    for start, end in blocks:
        i = start
        while i < end:
            if i in drop:
                i += 1
                continue
            label, opcode, operands = instrs[i]
            if opcode != "set" or len(operands) != 2:
                i += 1
                continue
            dst, value = operands
            if not _is_slot(dst):
                i += 1
                continue
            # The consumer is the next NON-sentinel instruction in-block.
            j = i + 1
            while j < end and instrs[j][1] is None:
                j += 1
            if j >= end:
                i += 1
                continue
            # A labelled consumer is reachable from elsewhere — folding a
            # staging set into it would lose the value on the other
            # entry edge. Refuse. (Block segmentation already starts a
            # new block at a label, so j>start of next block; but the
            # in-block guard via `end` already excludes it. This is a
            # belt-and-suspenders check for the sentinel-skip case.)
            c_label, c_opcode, c_operands = instrs[j]
            if c_label is not None:
                i += 1
                continue
            # Current (possibly already-rewritten) consumer operands.
            cons = rewrite.get(j, (c_label, c_opcode, c_operands))
            c_label2, c_opcode2, c_operands2 = cons
            _, read_positions = _rw(c_opcode2, c_operands2)
            hit_positions = [
                p for p in read_positions if c_operands2[p] == dst
            ]
            if not hit_positions:
                i += 1
                continue
            # The staging set's VALUE must be dead after the consumer
            # reads it. Two ways that holds:
            #   (a) the consumer itself (re)writes ``dst`` — then the
            #       staging definition's last use is exactly this read
            #       (read-after-write fold, e.g. ``op add s0 s0 y``);
            #       any LATER read of ``dst`` sees the consumer's NEW
            #       write, so the staging value is dead regardless; or
            #   (b) ``dst`` is dead strictly after the consumer (no
            #       later read before a rewrite, and not live-out).
            consumer_rewrites_dst = _writes_slot(c_opcode2, c_operands2) == dst
            if not (
                consumer_rewrites_dst or _dead_after(instrs, j, dst, end)
            ):
                i += 1
                continue
            # The `set`'s destination must not also be a read operand of
            # the consumer at a position we are NOT folding (defensive:
            # all matching positions are folded, so this is automatic),
            # and the consumer must not depend on dst surviving for a
            # later in-block read (already guaranteed by _dead_after).
            new_operands = list(c_operands2)
            for p in hit_positions:
                new_operands[p] = value
            rewrite[j] = (c_label2, c_opcode2, tuple(new_operands))
            drop.add(i)
            i += 1

    out: list = []
    for idx in range(n):
        if idx in drop:
            # The folded staging `set` is removed — but if it carried a
            # label (e.g. a DO/LOOP fall-through target `L_do_N_end`),
            # re-queue that label as a standalone sentinel so a
            # `jump <label>` aimed at it still resolves to the right line.
            # Mirrors the dead-copy pass's label-preservation contract;
            # without this, folding a labelled staging set silently
            # orphans the jump target (regression found by the
            # mforth-10t.40 benchmark harness on a DO/LOOP fixture).
            dropped_label = instrs[idx][0]
            if dropped_label is not None:
                out.append((dropped_label, None, None))
            continue
        out.append(rewrite.get(idx, instrs[idx]))
    return out


__all__ = ["peephole"]
