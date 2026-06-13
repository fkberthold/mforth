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


# ---------------------------------------------------------------------------
# v2 liveness + dead-copy elimination (bead mforth-10t.34)
# ---------------------------------------------------------------------------
#
# This is an OPT-IN pass over the *emitted instruction stream* (the
# ``list[MlogInstr]`` tuples ``(label, opcode, operands)`` produced by
# :func:`mforth.backend.mlog.emit.emit`). It is intentionally NOT wired
# into the default pipeline — bead mforth-10t.40 turns it on under the
# ``-O`` flags. Calling ``allocate_slots`` and ``emit`` with the default
# path stays byte-identical (so golden/equivalence tests are unaffected).
#
# Priority is fast > small (CLAUDE.md): a dead ``set`` is a runtime cycle
# spent writing a value nobody reads, and collapsing never-simultaneously-
# live slots onto one mlog variable keeps a program inside mlog's variable
# budget. Both axes win here, which is why this pass is part of the
# default ``-Ofast`` tier (see the v2 optimization roadmap drawer).
#
# Correctness contract (the headline equivalence property): the optimized
# stream MUST produce a byte-identical observable event sequence. To hold
# that under arbitrary control flow we compute liveness with a proper
# backward CFG fixpoint (labels are block leaders; ``jump`` edges + the
# fall-through edge), and we only ever (a) drop a ``set s<N> X`` whose
# destination slot is provably dead-out, and (b) merge two stack slots
# that never interfere (are never simultaneously live). Neither rewrite
# can change what any surviving instruction reads.


def _is_slot(token: str) -> bool:
    """True iff ``token`` is a stack-slot variable name ``s<digits>``.

    Only stack slots are tracked by the liveness pass. Named variables
    (user ``VARIABLE``s, ``__swap_tmp``, ``@``-prefixed magic vars),
    numeric/quoted literals, and bare Mindustry identifiers live in
    mlog's flat global namespace and are never eliminated or renumbered
    — they may be observed (events) or shared across scopes.
    """
    return (
        len(token) >= 2
        and token[0] == "s"
        and token[1:].isdigit()
    )


def _slot_index_of(token: str) -> int:
    return int(token[1:])


# Per-opcode operand roles. Each entry maps an opcode to
# ``(def_positions, use_positions)`` — operand indices that *define*
# (write) a slot and that *use* (read) a slot, respectively. Positions
# outside the actual operand tuple length are ignored. Opcodes absent
# from this table are treated as "uses every operand, defines nothing"
# (the conservative default — keeps any unknown future opcode safe).
_OPERAND_ROLES: dict = {
    # set <dst> <src>
    "set": ((0,), (1,)),
    # op <operation> <result> <a> <b>  (operand 0 is the op name, not a slot)
    "op": ((1,), (2, 3)),
    # print <value>
    "print": ((), (0,)),
    # printflush <block>
    "printflush": ((), (0,)),
    # wait <seconds>
    "wait": ((), (0,)),
    # sensor <result> <block> <prop>
    "sensor": ((0,), (1, 2)),
    # getlink <result> <i>
    "getlink": ((0,), (1,)),
    # control <sub> <block> <a> <b> <c> <d>  (operand 0 is the sub-command)
    "control": ((), (1, 2, 3, 4, 5)),
    # jump <target> <cond> <a> <b>  (operand 0 is a label, 1 is the cond)
    "jump": ((), (2, 3)),
    # end — no operands, no slot effect
    "end": ((), ()),
}


def _defs_uses(opcode: str, operands: tuple) -> tuple:
    """Return ``(defs, uses)`` as ``set[str]`` of stack-slot tokens for
    one instruction. Non-slot operands are filtered out."""
    roles = _OPERAND_ROLES.get(opcode)
    if roles is None:
        # Unknown opcode: be conservative — every operand is a use,
        # nothing is a def. This can only ever KEEP instructions /
        # slots alive, never wrongly drop them.
        defs: set = set()
        uses = {op for op in operands if _is_slot(op)}
        return defs, uses
    def_pos, use_pos = roles
    defs = {
        operands[p]
        for p in def_pos
        if p < len(operands) and _is_slot(operands[p])
    }
    uses = {
        operands[p]
        for p in use_pos
        if p < len(operands) and _is_slot(operands[p])
    }
    return defs, uses


def _is_droppable_set(opcode: str, operands: tuple) -> bool:
    """True iff the instruction is a pure ``set s<N> X`` copy whose only
    effect is writing a stack slot (so it is safe to drop when dead).

    ``sensor`` / ``getlink`` also write a slot but carry observable
    side effects (events) — never droppable. ``op`` is pure but the bead
    scopes elimination to ``set`` copies, so we keep ``op`` results even
    if dead (conservative; still correct)."""
    return (
        opcode == "set"
        and len(operands) == 2
        and _is_slot(operands[0])
    )


# ---------------------------------------------------------------------------
# @counter-aware control flow (bead mforth-i8h)
# ---------------------------------------------------------------------------
#
# ``-Osize`` emits user words as subroutines via mlog's WRITABLE ``@counter``
# (CLAUDE.md hard rule):
#
#   * ``set @counter <entry-label>``     — a CALL (computed goto to a
#     statically-known subroutine entry; control RETURNS to the next line).
#   * ``set @counter <ret-var>`` (e.g.   — a computed RETURN to an UNKNOWN
#     ``set @counter __ret_BIG``)          target (any call site's return
#                                          label); does NOT fall through.
#   * ``op add @counter @counter <off>`` — a jump-table dispatch (computed
#     goto to an unknown target).
#
# The original successor model treated every ``set`` as a plain
# fall-through, so it never followed these edges — which is exactly why the
# post-emit passes were SKIPPED on the subroutine stream. Modeling them
# correctly (and CONSERVATIVELY when the target is not statically known) is
# what lets ``-Osize`` run slot-liveness + peephole over subroutine bodies
# for extra size wins without breaking the headline equivalence property.


def _at_counter_write(opcode: str, operands: tuple):
    """Classify an instruction that WRITES ``@counter`` (a computed jump).

    Returns one of:

    * ``None`` — not a write to ``@counter`` (ordinary control flow).
    * ``("call", <label>)`` — ``set @counter <X>`` where ``<X>`` is a
      symbolic label (a statically-known subroutine entry). Control
      transfers to ``<X>`` and, because the callee returns, ALSO to the
      fall-through line.
    * ``("computed", None)`` — a computed jump whose target is NOT
      statically a label: ``set @counter <ret-var>`` (computed return,
      e.g. ``__ret_*``) or ``op add @counter @counter <off>`` (jump
      table). Conservatively reaches every labelled position; no
      fall-through.

    The "call vs computed" split is decided by the caller, which knows
    whether the operand resolves to a label. Here we only detect the
    @counter-write shape and surface the candidate target token.
    """
    if opcode == "set" and len(operands) == 2 and operands[0] == "@counter":
        return ("set@counter", operands[1])
    if (
        opcode == "op"
        and len(operands) >= 2
        and operands[1] == "@counter"
    ):
        # op <f> @counter ... — jump-table dispatch, unknown target.
        return ("computed", None)
    return None


def _build_successors(instrs: list) -> tuple:
    """Build the control-flow successor lists over real (non-sentinel)
    instructions.

    Returns ``(real, succ, label_to_real)`` where ``real`` is the list
    of ``(opcode, operands)`` for instructions that consume a line
    (sentinels ``(_, None, None)`` are dropped), ``succ`` is a parallel
    list of successor-index lists, and ``label_to_real`` maps every
    label (attached or sentinel) to the index in ``real`` it precedes.

    Mirrors :func:`finalize.resolve_labels`' line-counting so a
    ``jump <label>`` resolves to the same instruction the finalize pass
    would target. A label sitting past the last instruction maps to the
    auto-loop wrap target (index 0).

    ``set @counter <X>`` / ``op <f> @counter ...`` are modeled as computed
    jumps (bead mforth-i8h) — see :func:`_at_counter_write`."""
    real: list = []
    label_to_real: dict = {}
    for label, opcode, _operands in instrs:
        if label is not None:
            label_to_real.setdefault(label, len(real))
        if opcode is None:
            continue  # sentinel — consumes no line
        real.append((opcode, _operands))
    n = len(real)

    def resolve(label: str) -> int:
        idx = label_to_real.get(label)
        if idx is None or idx >= n:
            # Label at/after program end → auto-loop wrap to line 0.
            return 0
        return idx

    # Every distinct line that some label resolves to — the conservative
    # successor set for a computed jump whose target isn't statically known
    # (a ``set @counter <ret-var>`` return, or an ``op add @counter`` table
    # dispatch). Unioning all label targets guarantees liveness never drops
    # a store that is read at ANY reachable computed-jump landing site.
    all_label_targets = sorted({label_to_real[lbl] for lbl in label_to_real
                                if label_to_real[lbl] < n})

    succ: list = []
    for i, (opcode, operands) in enumerate(real):
        outs: list = []
        atc = _at_counter_write(opcode, operands)
        if atc is not None:
            kind, target_tok = atc
            if kind == "set@counter" and target_tok in label_to_real:
                # CALL: ``set @counter <entry-label>``. Control enters the
                # subroutine AND (because it returns) the next line.
                outs.append(resolve(target_tok))
                if i + 1 < n:
                    outs.append(i + 1)
            else:
                # Computed jump to an unknown target (return via __ret_*,
                # jump table, or any non-label @counter write): reach every
                # labelled position; no deterministic fall-through.
                outs.extend(all_label_targets)
        elif opcode == "jump":
            target = operands[0] if operands else None
            cond = operands[1] if len(operands) > 1 else "always"
            if target is not None:
                outs.append(resolve(target))
            if cond != "always":
                # conditional — fall through too
                if i + 1 < n:
                    outs.append(i + 1)
        elif opcode == "end":
            # Auto-loop wrap to the first instruction.
            outs.append(0)
        else:
            if i + 1 < n:
                outs.append(i + 1)
        succ.append(outs)
    return real, succ, label_to_real


def _compute_liveness(real: list, succ: list) -> tuple:
    """Backward CFG fixpoint. Returns ``(live_in, live_out)`` parallel
    lists of ``set[str]`` slot names.

    ``live_out[i] = ∪ live_in[s] for s in succ[i]``
    ``live_in[i]  = (live_out[i] - defs[i]) ∪ uses[i]``
    """
    n = len(real)
    defs_l: list = []
    uses_l: list = []
    for opcode, operands in real:
        d, u = _defs_uses(opcode, operands)
        defs_l.append(d)
        uses_l.append(u)

    live_in = [set() for _ in range(n)]
    live_out = [set() for _ in range(n)]

    changed = True
    while changed:
        changed = False
        # Iterate in reverse for faster convergence on mostly-linear code.
        for i in range(n - 1, -1, -1):
            new_out: set = set()
            for s in succ[i]:
                new_out |= live_in[s]
            new_in = (new_out - defs_l[i]) | uses_l[i]
            if new_out != live_out[i] or new_in != live_in[i]:
                live_out[i] = new_out
                live_in[i] = new_in
                changed = True
    return live_in, live_out


def _renumber_slots(real: list, live_in: list, live_out: list) -> dict:
    """Build a slot-merge map that collapses never-simultaneously-live
    stack slots onto a minimal set of reused slot names.

    Interference rule: two slots interfere iff both appear in the same
    ``live_in`` or ``live_out`` set, OR a def at instruction ``i``
    coincides with another slot that is live-out there (so a write does
    not clobber a still-live value). Greedy coloring then assigns each
    slot the lowest-indexed reuse slot that none of its interferers
    already hold.

    Returns ``{old_slot: new_slot}`` (identity for slots that keep their
    name). Renumbering is order-stable: slots are colored in ascending
    original index so the lowest live values land in the lowest reuse
    slots — keeping output readable and deterministic.
    """
    # Collect all slots and their pairwise interferences.
    interfere: dict = {}
    all_slots: set = set()

    def note(slot: str) -> None:
        all_slots.add(slot)
        interfere.setdefault(slot, set())

    for i, (opcode, operands) in enumerate(real):
        d, _u = _defs_uses(opcode, operands)
        for grp in (live_in[i], live_out[i]):
            for s in grp:
                note(s)
            grp_list = list(grp)
            for a_idx in range(len(grp_list)):
                for b_idx in range(a_idx + 1, len(grp_list)):
                    a, b = grp_list[a_idx], grp_list[b_idx]
                    interfere[a].add(b)
                    interfere[b].add(a)
        # A def must not reuse a slot that is live-out alongside it.
        for dslot in d:
            note(dslot)
            for s in live_out[i]:
                if s != dslot:
                    interfere[dslot].add(s)
                    interfere.setdefault(s, set()).add(dslot)

    if not all_slots:
        return {}

    # Greedy coloring over ascending original slot index → reuse slot.
    ordered = sorted(all_slots, key=_slot_index_of)
    color: dict = {}
    for slot in ordered:
        forbidden = {
            color[nb] for nb in interfere.get(slot, ()) if nb in color
        }
        c = 0
        while c in forbidden:
            c += 1
        color[slot] = c

    return {slot: _slot(color[slot]) for slot in ordered}


def max_slot_index_of_instrs(instrs: list) -> int:
    """Largest stack-slot index referenced anywhere in ``instrs`` (or
    ``-1`` if none). Mirrors :meth:`SlotMap.max_slot_index` but operates
    on the emitted instruction stream — used to measure the slot-count
    win the liveness pass delivers."""
    hi = -1
    for _label, opcode, operands in instrs:
        if operands is None:
            continue
        for tok in operands:
            if _is_slot(tok):
                idx = _slot_index_of(tok)
                if idx > hi:
                    hi = idx
    return hi


def eliminate_dead_copies(instrs: list, *, reuse_slots: bool = True) -> list:
    """Opt-in liveness pass: drop dead ``set`` copies and (optionally)
    reuse dead slots.

    Parameters
    ----------
    instrs
        The emitted instruction stream — a ``list`` of
        ``(label, opcode, operands)`` tuples (the output of
        :func:`mforth.backend.mlog.emit.emit`).
    reuse_slots
        When True (default), also renumber stack slots so never-
        simultaneously-live slots share one mlog variable, shrinking the
        max-slot count. When False, only dead-store elimination runs
        (slot names are left untouched) — useful for callers that want
        the smaller, purely-subtractive transform.

    Returns
    -------
    list
        A new instruction list. Label sentinels and attached labels are
        preserved on the instructions that survive. The default pipeline
        does NOT call this — bead mforth-10t.40 wires it under ``-O``.

    Notes
    -----
    Equivalence preservation is the hard rule: the returned stream yields
    a byte-identical observable event sequence under the in-repo mlog
    interpreter. Dead-store drops only remove pure ``set s<N> X`` writes
    whose slot is provably never read; slot reuse only merges slots that
    never interfere. See the module-level commentary above for the CFG
    liveness contract.
    """
    instrs = list(instrs)
    real, succ, _label_map = _build_successors(instrs)
    if not real:
        return instrs
    live_in, live_out = _compute_liveness(real, succ)

    # --- 1. Dead-store elimination. -----------------------------------
    # Walk the original list (with sentinels) alongside a cursor into the
    # `real` list so we can consult per-instruction liveness while still
    # carrying labels through.
    kept: list = []
    ri = 0
    for label, opcode, operands in instrs:
        if opcode is None:
            kept.append((label, opcode, operands))
            continue
        l_out = live_out[ri]
        drop = (
            _is_droppable_set(opcode, operands)
            and operands[0] not in l_out
        )
        ri += 1
        if drop:
            # If a label was attached to this instruction, re-queue it as
            # a sentinel so jump targets that pointed here still resolve.
            if label is not None:
                kept.append((label, None, None))
            continue
        kept.append((label, opcode, operands))

    if not reuse_slots:
        return kept

    # --- 2. Slot reuse (renumbering). ---------------------------------
    # Recompute liveness on the post-elimination stream so the
    # interference graph reflects the removed writes (a dropped dead store
    # can only shrink live ranges, never grow them).
    real2, succ2, _lm2 = _build_successors(kept)
    if not real2:
        return kept
    live_in2, live_out2 = _compute_liveness(real2, succ2)
    remap = _renumber_slots(real2, live_in2, live_out2)
    if not remap or all(k == v for k, v in remap.items()):
        return kept

    def rewrite(operands: tuple) -> tuple:
        return tuple(remap.get(tok, tok) for tok in operands)

    out: list = []
    for label, opcode, operands in kept:
        if opcode is None:
            out.append((label, opcode, operands))
            continue
        out.append((label, opcode, rewrite(operands)))
    return out


__all__ = [
    "SlotMap",
    "allocate_slots",
    "eliminate_dead_copies",
    "max_slot_index_of_instrs",
]
