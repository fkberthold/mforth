"""Static stack-slot allocator for the mlog backend.

Bead mforth-10t.15. Consumes a `StackcheckResult` (depth-annotated AST
plus user-definition `StackEffect`s) and produces a `SlotMap` that names
the mlog variables (`s0`, `s1`, ...) each term reads from and writes to.

Why this exists
---------------

mlog has no operand stack. The Forth-shaped data stack is realised at
codegen time as a set of mlog variables `s0..sN` whose indices are fixed
statically by the depth of the operand at each program point. The
stack-checker has already proved that the depth is statically known
everywhere (branches merge, loop bodies are stack-neutral), so the
allocator can answer "which slot?" with a pure walk — no abstract
interpretation, no fixpoint.

Slot rule
---------

If the data stack has depth `D` at a program point, the live operands
occupy `s0, s1, ..., s(D-1)` bottom-to-top. A *push* at depth `D` writes
`s<D>`. A *pop* at depth `D` reads `s<D-1>`. A word with stack effect
`(in_arity, out_arity)` at depth `D` reads
`s<D-in_arity>, ..., s<D-1>` (bottom-to-top order, matching the input
order) and writes `s<D-in_arity>, ..., s<D-in_arity+out_arity-1>`.

Frame offset for user definitions
---------------------------------

The stack-checker simulates each `Definition` body with `initial_depth=0`
and tolerates the depth dipping negative (that dip becomes the inferred
`in_arity`). Inside a definition body the actual stack-slot indices must
be offset by the entry frame — the caller's `in_arity` items live in
`s0..s(in_arity-1)` and the body's local-depth-0 terms see them.

So for a term inside a `Definition` body, this allocator computes the
stack-relative depth as `entry_depth + local_depth_in` where
`entry_depth = definition.effect.in_arity`. For `main`, `entry_depth=0`.

What this layer does *not* do
-----------------------------

* No mlog text emission — that is bead `mforth-10t.16`.
* No DO/LOOP counter variable allocation — `I`/`J` (which push the
  counter onto the data stack) take a normal stack slot for the pushed
  value; the underlying loop-counter register is a codegen concern.
* No subroutine emission for user-defined word *calls* — at the call
  site a `WordCall` is treated exactly like a builtin of the inferred
  effect (reads its inputs, writes its outputs to the same slots). The
  inlining/`@counter`-trampoline decision belongs to `.16` / v2 codegen.

Key shape
---------

`SlotMap` is keyed by `id(term)`. The AST uses `@dataclass(frozen=True)`
for leaf terms, which derives `__hash__` and `__eq__` from field values,
so two `LitInt(1)` terms in different positions compare equal. Identity
keying keeps every occurrence distinct.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mforth.dictionary import (
    BuiltinWord,
    Definition,
    StackEffect,
    UnresolvedWordError,
    UserVariable,
)
from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    LitStr,
    VarRef,
    WordCall,
)
from mforth.stackcheck import StackcheckResult


# ---------------------------------------------------------------------------
# SlotMap — the allocator's output.
# ---------------------------------------------------------------------------


@dataclass
class SlotMap:
    """Per-term slot assignments produced by `allocate_slots`.

    Stored as two parallel dicts keyed by `id(term)`. The
    `(read_slots, write_slots)` pair is the headline output described in
    bead mforth-10t.15. `read_slots` is in bottom-to-top stack order
    (matching Forth input convention); `write_slots` is in the same
    order, so for an effect-`(2, 1)` word the single output overwrites
    the bottom input slot — which is the natural in-place codegen.

    `max_slot_index()` returns the largest slot index referenced
    anywhere in the program (or `-1` for an empty program). The codegen
    will materialise `s0..s<max_slot_index>` as declared mlog variables.
    """

    _reads: dict = field(default_factory=dict)   # id(term) -> tuple[str,...]
    _writes: dict = field(default_factory=dict)  # id(term) -> tuple[str,...]
    _max_slot_index: int = -1

    def reads(self, term) -> tuple:
        return self._reads[id(term)]

    def writes(self, term) -> tuple:
        return self._writes[id(term)]

    def has(self, term) -> bool:
        return id(term) in self._reads

    def max_slot_index(self) -> int:
        return self._max_slot_index

    # Convenience for the .16 codegen: iterate every term's assignment.
    def items(self):
        for tid, reads in self._reads.items():
            yield tid, reads, self._writes[tid]


# ---------------------------------------------------------------------------
# Allocation walk.
# ---------------------------------------------------------------------------


def _slot(idx: int) -> str:
    if idx < 0:
        raise AssertionError(
            f"slot index {idx} is negative — stackcheck should have caught this"
        )
    return f"s{idx}"


def _slots_range(start: int, count: int) -> tuple:
    return tuple(_slot(i) for i in range(start, start + count))


def allocate_slots(result: StackcheckResult) -> SlotMap:
    """Walk the stack-checked AST and produce a `SlotMap`.

    Pre-condition: `result` came out of `stackcheck()` cleanly (no
    `StackError` raised). Every term reachable through `result.program`
    has its incoming local depth recorded in `result._depths_in`.
    """
    sm = SlotMap()
    dictionary = result.dictionary

    def effect_of(call: WordCall) -> StackEffect:
        entry = dictionary.lookup(call.name)
        if entry is None:
            # stackcheck should have caught this; keep a defensive raise.
            raise UnresolvedWordError(call.name, call.src_loc)
        if isinstance(entry, BuiltinWord):
            return entry.stack_effect
        if isinstance(entry, UserVariable):
            return StackEffect(0, 1)  # pushes address
        if isinstance(entry, Definition):
            return result.effects[entry.name]
        raise TypeError(f"unknown dictionary entry type {type(entry).__name__}")

    def record(term, reads: tuple, writes: tuple) -> None:
        sm._reads[id(term)] = reads
        sm._writes[id(term)] = writes
        for s in reads + writes:
            idx = int(s[1:])
            if idx > sm._max_slot_index:
                sm._max_slot_index = idx

    def walk(body: list, frame_offset: int) -> None:
        """Annotate every term in `body`. `frame_offset` is the slot
        index of the bottom of this scope's view of the stack — 0 for
        `main`, `definition.in_arity` for a user-def body."""
        for term in body:
            local_depth = result.depth_in(term)
            absolute_depth = frame_offset + local_depth

            if isinstance(term, (LitInt, LitFloat, LitStr)):
                # Pure push: writes one slot at the current top.
                record(term, (), (_slot(absolute_depth),))
                continue

            if isinstance(term, VarRef):
                # Reserved for future resolver output; treat by mode.
                if term.mode == "fetch":
                    # ( -- value )
                    record(term, (), (_slot(absolute_depth),))
                elif term.mode == "store":
                    # ( value -- )
                    record(term, (_slot(absolute_depth - 1),), ())
                else:
                    raise ValueError(f"unknown VarRef mode {term.mode!r}")
                continue

            if isinstance(term, WordCall):
                eff = effect_of(term)
                read_start = absolute_depth - eff.in_arity
                reads = _slots_range(read_start, eff.in_arity)
                writes = _slots_range(read_start, eff.out_arity)
                record(term, reads, writes)
                continue

            if isinstance(term, IfThen):
                # The IF pops the flag at the current top; the THEN/ELSE
                # branches start at `absolute_depth - 1`.
                flag_slot = _slot(absolute_depth - 1)
                record(term, (flag_slot,), ())
                # Each branch body sees the same frame offset; the
                # branches' first terms have local_depth == absolute-1
                # already (stackcheck recorded that), so walking with
                # the same frame_offset DTRT.
                walk(term.then_body, frame_offset)
                walk(term.else_body, frame_offset)
                continue

            if isinstance(term, Begin):
                # The Begin container itself has no immediate
                # read/write at its program point — the loop's
                # condition lives at the end of the body (UNTIL) or
                # after a WHILE keyword inside the body, both of which
                # are reached via the body's own terms.
                record(term, (), ())
                walk(term.body, frame_offset)
                if term.kind == "while-repeat":
                    walk(term.cond_body, frame_offset)
                continue

            if isinstance(term, DoLoop):
                # DO consumes ( limit start -- ). Reads the top two
                # slots; writes nothing onto the data stack (the loop
                # counter is a codegen-managed register, surfaced via
                # the `I` / `J` builtins which take their own slot
                # entries when invoked).
                if absolute_depth < 2:
                    raise AssertionError(
                        "DO at depth < 2 — stackcheck should have caught this"
                    )
                reads = (_slot(absolute_depth - 2), _slot(absolute_depth - 1))
                record(term, reads, ())
                walk(term.body, frame_offset)
                continue

            raise TypeError(f"unknown Term type {type(term).__name__}")

    # Walk every user definition with its entry-frame offset.
    for defn in result.program.definitions:
        eff = result.effects.get(defn.name)
        if eff is None:
            # stackcheck always populates effects for every definition;
            # defensive fallback.
            entry_depth = 0
        else:
            entry_depth = eff.in_arity
        # Pre-bump max_slot_index so the codegen materialises the input
        # frame even if the body never pushes higher than its entry top.
        if entry_depth - 1 > sm._max_slot_index:
            sm._max_slot_index = entry_depth - 1
        walk(defn.body, frame_offset=entry_depth)

    # Walk main with offset 0.
    walk(result.program.main, frame_offset=0)

    return sm


__all__ = ["SlotMap", "allocate_slots"]
