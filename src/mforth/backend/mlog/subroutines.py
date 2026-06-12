"""Subroutine emission via the ``@counter`` trick — bead mforth-10t.39.

A Tier C **size-only fallback** codegen pass. v1's default strategy is
inline-everything (``mforth.backend.mlog.emit``): every user-word call
site expands the callee's body in place, which is fastest at runtime but
multiplies code size by the call count. When a program's estimated
inlined size would blow past the per-processor instruction budget, this
pass instead emits *selected* user words **once**, as subroutines, and
replaces each call site with a short call sequence built on mlog's
writable ``@counter`` (CLAUDE.md hard rule: ``@counter`` is writable —
``set @counter <var>`` is a computed goto).

This module is INTENTIONALLY standalone in this wave: it is not wired
into the default pipeline (``finalize.py`` / ``cli.py`` / ``emit.py``).
Bead mforth-10t.40 owns the ``-O`` level wiring. Until then the pass is
dead code exercised only by ``tests/unit/test_subroutines.py``, whose
equivalence test proves the subroutine-emitted program yields an
IDENTICAL :class:`MockWorld` event stream to the inlined program AND
fewer total instructions.

Optimization priority
=====================

mforth's priority is **fast > small** (``mforth-opt-priority``).
Inlining wins on speed *always* (no call overhead), so subroutine
emission is a fallback, never a default. Each call costs ~3 extra
instructions (save return address; ``set @counter`` to enter; the
callee's ``set @counter`` to return). For a word of body size ``W``
called ``C`` times: inline = ``C·W``; subroutine = ``W + C·k`` where
``k`` is the per-call overhead. Subroutine wins on *size* once
``W > k`` and ``C >= 2``; it always loses on *speed*. So this pass only
fires under ``-Osize`` (bead .40) or when the inline estimate exceeds
``--max-instructions``.

Calling convention (cell-free, no recursion)
============================================

v1 is cell-free — no return stack. Recursion is unsupported (and
impossible in the dialect: no ``DOES>`` / ``EXECUTE``). Each promoted
word gets its OWN fixed slot frame, stacked ABOVE the inline program's
maximum slot, so a subroutine body never aliases a caller-live slot
(caller-saved data-stack slots are all *below* the subroutine frames).

For a promoted word with effect ``(in_arity, out_arity)`` assigned base
slot ``B``:

* The body is emitted ONCE with every body slot ``s<k>`` rewritten to
  ``s<k + B>`` (the body was compiled at frame_offset = in_arity, so its
  inputs live at body slots ``s0..s<in_arity-1>``).
* Each call site at caller depth ``D`` (so inputs occupy caller slots
  ``s<D-in_arity>..s<D-1>``) emits:

    set s<B+j>        s<D-in_arity+j>   for j in 0..in_arity-1   (marshal in)
    set <ret-var>     <return-label>                            (save return)
    set @counter      <entry-label>                             (computed goto)
  <return-label>:
    set s<D-in_arity+j> s<B+j>          for j in 0..out_arity-1  (marshal out)

* The body ends with ``set @counter <ret-var>`` (computed return).

The return-address variable is per-subroutine (``__ret_<name>``); because
recursion is impossible, a single slot per word suffices — no save stack.
The return label resolves to an absolute line number by the existing
:func:`mforth.backend.mlog.finalize.resolve_labels` pass (the call uses a
``set @counter <label>`` whose label operand is rewritten there).

Interaction with finalize / resolve_labels
==========================================

:func:`resolve_labels` rewrites ``("jump", (label, ...))`` operand-0 to a
line number. Subroutine entry/return use ``set @counter <label>`` — a
``set`` instruction whose 2nd operand is a label. So this pass cannot
rely on the existing jump-only resolver: it resolves its OWN labels into
absolute line numbers BEFORE handing the tuple list off, emitting
``("set", ("@counter", "<line>"))`` with the line already numeric. The
emitted stream therefore round-trips through the normal finalize chain
(those ``set`` ops carry no symbolic label) AND through the in-repo mlog
interpreter, whose :func:`mforth.mlog_interp` ``set`` handler treats a
write to ``@counter`` as a computed jump (no PC auto-advance).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from mforth.backend.mlog.emit import _Emitter, _fuse_variable_patterns
from mforth.backend.mlog.slots import allocate_slots
from mforth.dictionary import Definition
from mforth.parse import WordCall
from mforth.stackcheck import StackcheckResult, stackcheck


# Per-call instruction overhead NOT counting marshalling: save-return +
# set-@counter-enter + the callee's single set-@counter-return amortised.
# Used only by the size estimate / heuristic, never by codegen.
_CALL_OVERHEAD = 3


@dataclass
class SubroutineConfig:
    """Knobs for the promotion heuristic.

    Attributes
    ----------
    max_instructions
        Inline-size budget. The pass only promotes words when the
        estimated fully-inlined program exceeds this. Default 800
        (per the bead spec; v1 leaves headroom under mlog's 1000-line
        processor limit).
    min_body_instructions
        A word is promotion-eligible only if its body emits at least
        this many instructions (small words aren't worth a call frame).
        Default 10.
    min_call_count
        A word is promotion-eligible only if called at least this many
        times. Default 2 (a once-called word is never worth promoting —
        inline = subroutine size, but inline is faster).
    """

    max_instructions: int = 800
    min_body_instructions: int = 10
    min_call_count: int = 2


# ---------------------------------------------------------------------------
# Call-count + body-size analysis
# ---------------------------------------------------------------------------


def _count_calls(result: StackcheckResult) -> dict:
    """Return ``{def_name: call_count}`` counting WordCall references to
    user definitions across main AND every definition body.

    A call inside another definition's body counts once per textual
    occurrence (the inline expansion would duplicate it once per
    *dynamic* expansion, but for the promotion heuristic the textual
    count is the right denominator: a word called twice textually is
    inlined twice).
    """
    def_names = {d.name for d in result.program.definitions}
    counts: dict = {name: 0 for name in def_names}

    def walk(body: list) -> None:
        for term in body:
            if isinstance(term, WordCall):
                if term.name in counts:
                    counts[term.name] += 1
            children = _child_bodies(term)
            for child in children:
                walk(child)

    for defn in result.program.definitions:
        walk(defn.body)
    walk(result.program.main)
    return counts


def _child_bodies(term) -> list:
    """Return the nested term-lists of a control-flow term (empty for
    leaf terms)."""
    from mforth.parse import Begin, DoLoop, IfThen

    if isinstance(term, IfThen):
        return [term.then_body, term.else_body]
    if isinstance(term, Begin):
        return [term.body, term.cond_body]
    if isinstance(term, DoLoop):
        return [term.body]
    return []


def _body_instruction_count(emitter: _Emitter, defn: Definition) -> int:
    """Emit ``defn``'s body in isolation and return the instruction count
    (excluding label-sentinel tuples, which consume no line).

    Reuses the real emitter so the count reflects the actual lowering
    (control flow, lifting, stack-op expansion) rather than a guess.
    """
    out: list = []
    emitter._emit_body(out, defn.body, slot_rewrite=None)
    emitter._flush_pending_labels(out)
    return sum(1 for (_lab, op, _ops) in out if op is not None)


# ---------------------------------------------------------------------------
# Promotion decision
# ---------------------------------------------------------------------------


def select_promotions(
    result: StackcheckResult,
    slot_map,
    config: Optional[SubroutineConfig] = None,
) -> list:
    """Return the list of definition names to emit as subroutines.

    Heuristic (per bead .39):

    1. Estimate the fully-inlined program size.
    2. If it fits within ``config.max_instructions``, promote nothing
       (inline-everything is faster and the program fits).
    3. Otherwise promote every eligible word — body size
       ``>= min_body_instructions`` AND call count ``>= min_call_count``
       — preferring the words whose promotion saves the most
       instructions, until the estimate fits (or no more candidates).
    """
    config = config or SubroutineConfig()
    # A probe emitter (never produces final output) used to measure body
    # sizes against the real lowering.
    probe = _Emitter(result, slot_map)
    counts = _count_calls(result)
    body_sizes = {
        d.name: _body_instruction_count(probe, d)
        for d in result.program.definitions
    }

    inline_estimate = _estimate_inline_size(probe, result)
    if inline_estimate <= config.max_instructions:
        return []

    candidates = [
        name
        for name in counts
        if body_sizes.get(name, 0) >= config.min_body_instructions
        and counts[name] >= config.min_call_count
    ]
    # Savings = (C-1)*W - C*overhead - W  →  inlining C copies vs one
    # subroutine body + C call sites. Sort biggest-savings first.
    def savings(name: str) -> int:
        w = body_sizes[name]
        c = counts[name]
        inline_cost = c * w
        sub_cost = w + c * _CALL_OVERHEAD
        return inline_cost - sub_cost

    candidates.sort(key=savings, reverse=True)

    promoted: list = []
    estimate = inline_estimate
    for name in candidates:
        if estimate <= config.max_instructions:
            break
        if savings(name) <= 0:
            continue
        promoted.append(name)
        estimate -= savings(name)
    return promoted


def _estimate_inline_size(emitter: _Emitter, result: StackcheckResult) -> int:
    """Estimate the fully-inlined instruction count for the program.

    Walks main, expanding each user-def call to its body size times the
    (recursive) expansion. Cheap approximation good enough for the
    promotion gate — it ignores control-flow nesting subtleties but
    counts each textual call's body once per occurrence.
    """
    body_sizes: dict = {}

    def size_of_def(name: str) -> int:
        if name in body_sizes:
            return body_sizes[name]
        body_sizes[name] = 0  # guard (no recursion in v1 anyway)
        defn = next(d for d in result.program.definitions if d.name == name)
        total = _measure(defn.body)
        body_sizes[name] = total
        return total

    def_names = {d.name for d in result.program.definitions}

    def _measure(body: list) -> int:
        total = 0
        for term in body:
            if isinstance(term, WordCall) and term.name in def_names:
                total += size_of_def(term.name)
                continue
            children = _child_bodies(term)
            if children:
                for child in children:
                    total += _measure(child)
                total += 1  # the control-flow term's own jump(s) ≈ 1
                continue
            total += 1
        return total

    return _measure(result.program.main)


# ---------------------------------------------------------------------------
# Subroutine emitter
# ---------------------------------------------------------------------------


class _SubroutineEmitter(_Emitter):
    """An ``_Emitter`` that lowers selected user words as subroutines.

    Promoted words are emitted once after ``main``; their call sites
    become a marshal-in / save-return / computed-goto / marshal-out
    sequence. Non-promoted words still inline via the base class.
    """

    def __init__(self, result, slot_map, promoted: list, base_slot: int):
        super().__init__(result, slot_map)
        self._promoted = list(promoted)
        # Assign each promoted word a non-overlapping slot frame above the
        # inline program's maximum slot, so a subroutine body never
        # clobbers a caller-live slot. Frames are stacked by max body slot.
        self._sub_base: dict = {}
        cursor = base_slot
        self._sub_frames: dict = {}
        for name in self._promoted:
            defn = self._defn(name)
            frame = self._measure_body_max_slot(defn)
            self._sub_base[name] = cursor
            self._sub_frames[name] = frame
            cursor += frame + 1
        self._ret_label_counter = 0

    def _defn(self, name: str) -> Definition:
        return next(
            d for d in self.result.program.definitions if d.name == name
        )

    def _measure_body_max_slot(self, defn: Definition) -> int:
        """Largest body-local slot index referenced when ``defn`` is
        emitted at its natural frame (frame_offset = in_arity, body slots
        s0..). Returns at least in_arity-1 so the input frame is covered.
        """
        out: list = []
        self._emit_body(out, defn.body, slot_rewrite=None)
        self._flush_pending_labels(out)
        max_idx = -1
        eff = self.result.effects[defn.name]
        max_idx = max(max_idx, eff.in_arity - 1, eff.out_arity - 1)
        for (_lab, op, ops) in out:
            if op is None or ops is None:
                continue
            for tok in ops:
                if isinstance(tok, str) and tok.startswith("s") and tok[1:].isdigit():
                    idx = int(tok[1:])
                    if idx > max_idx:
                        max_idx = idx
        return max(max_idx, 0)

    # ---- override the call-site lowering -------------------------------

    def _emit_user_def_call(self, out, defn, call, outer_rewrite) -> None:
        if defn.name not in self._promoted:
            super()._emit_user_def_call(out, defn, call, outer_rewrite)
            return
        eff = self.result.effects[defn.name]
        base = self._sub_base[defn.name]
        call_reads = self.slots.reads(call)
        call_writes = self.slots.writes(call)

        # Resolve the caller's frame base (slot index of the call's first
        # input / output), composing the outer rewrite if we are inside an
        # inlined word.
        def outer(slot: str) -> str:
            return outer_rewrite(slot) if outer_rewrite is not None else slot

        entry_label = f"L_sub_{defn.name}_entry"
        ret_label = f"L_subret_{defn.name}_{self._ret_label_counter}"
        self._ret_label_counter += 1
        ret_var = f"__ret_{defn.name}"

        # Marshal inputs: caller input slots → body frame slots.
        for j in range(eff.in_arity):
            src = outer(call_reads[j])
            dst = f"s{base + j}"
            self._emit(out, "set", (dst, src))
        # Save return address + computed goto into the subroutine entry.
        self._emit(out, "set", (ret_var, ret_label))
        self._emit(out, "set", ("@counter", entry_label))
        # Return lands here.
        self._queue_label(ret_label)
        # Marshal outputs: body frame slots → caller output slots.
        for j in range(eff.out_arity):
            src = f"s{base + j}"
            dst = outer(call_writes[j])
            self._emit(out, "set", (dst, src))
        # If the word has zero outputs the return label still needs an
        # instruction to attach to; the next emitted instruction (or a
        # flushed sentinel) carries it. Emit a harmless no-op anchor only
        # when nothing follows is handled by _flush at program end.

    # ---- subroutine body emission --------------------------------------

    def emit_with_subroutines(self) -> list:
        """Emit ``main`` followed by each promoted subroutine body."""
        out: list = []
        self._emit_body(out, self.result.program.main, slot_rewrite=None)
        self._flush_pending_labels(out)
        # Terminate main so execution does not fall through into the
        # subroutine bodies (the auto-loop would otherwise run them).
        self._emit(out, "end", ())
        self._flush_pending_labels(out)

        for name in self._promoted:
            defn = self._defn(name)
            base = self._sub_base[name]
            ret_var = f"__ret_{name}"

            def rewrite(slot: str, _base=base) -> str:
                if slot.startswith("s") and slot[1:].isdigit():
                    return f"s{int(slot[1:]) + _base}"
                return slot

            self._queue_label(f"L_sub_{name}_entry")
            self._emit_body(out, defn.body, slot_rewrite=rewrite)
            self._flush_pending_labels(out)
            # Computed return: jump back to the saved address.
            self._emit(out, "set", ("@counter", ret_var))
            self._flush_pending_labels(out)
        return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def emit_with_subroutines(
    result: StackcheckResult,
    slot_map=None,
    *,
    config: Optional[SubroutineConfig] = None,
    force_promote: Optional[list] = None,
) -> list:
    """Emit the program, promoting eligible user words to subroutines.

    Mirrors :func:`mforth.backend.mlog.emit.emit`'s front matter (fuse
    VARIABLE patterns, re-stackcheck, re-allocate) so the slot indices
    line up with the fused instruction stream, then runs the subroutine
    lowering.

    Parameters
    ----------
    result
        A clean :class:`StackcheckResult`.
    slot_map
        Accepted for API symmetry; recomputed internally (matching
        ``emit``).
    config
        Promotion heuristic knobs. Defaults to :class:`SubroutineConfig`.
    force_promote
        Test/`-Osize` hook: explicit list of definition names to promote
        regardless of the heuristic. ``None`` runs the heuristic.

    Returns
    -------
    list[MlogInstr]
        The instruction tuples, with promoted words emitted once as
        subroutines and call sites lowered to the ``@counter`` call
        sequence. ``set @counter <label>`` operands are LEFT symbolic
        here; :func:`finalize_subroutine_labels` (or the normal finalize
        chain extended in bead .40) resolves them. For the standalone
        unit test the helper :func:`resolve_subroutine_text` does the
        full chain.
    """
    config = config or SubroutineConfig()
    fused_program = _fuse_variable_patterns(result.program, result.dictionary)
    fused_result = stackcheck(fused_program, dictionary=result.dictionary)
    fused_slots = allocate_slots(fused_result)

    if force_promote is not None:
        promoted = [
            name
            for name in force_promote
            if any(d.name == name for d in fused_result.program.definitions)
        ]
    else:
        promoted = select_promotions(fused_result, fused_slots, config)

    base_slot = fused_slots.max_slot_index() + 1
    emitter = _SubroutineEmitter(
        fused_result, fused_slots, promoted, base_slot
    )
    return emitter.emit_with_subroutines()


# ---------------------------------------------------------------------------
# Label resolution for the standalone path
# ---------------------------------------------------------------------------


def resolve_subroutine_labels(instrs: list) -> list:
    """Resolve symbolic labels (jumps AND ``set @counter <label>``) to
    absolute 0-indexed line numbers, then strip label sentinels.

    The stock :func:`mforth.backend.mlog.finalize.resolve_labels` only
    rewrites ``jump`` operand-0. Subroutine call/return use
    ``set @counter <label>``; this resolver rewrites BOTH so the
    standalone test can run the emitted stream through the in-repo
    interpreter without the bead .40 pipeline wiring.
    """
    label_lines: dict = {}
    line = 0
    for label, opcode, _operands in instrs:
        if label is not None:
            label_lines[label] = line
        if opcode is None:
            continue
        line += 1

    out: list = []
    for _label, opcode, operands in instrs:
        if opcode is None:
            continue
        if opcode == "jump":
            target = operands[0]
            new_ops = (str(label_lines[target]), *operands[1:])
            out.append((None, opcode, new_ops))
            continue
        if opcode == "set" and len(operands) == 2 and operands[1] in label_lines:
            # `set @counter <label>` (computed goto) OR
            # `set __ret_<name> <label>` (save return address). Both carry
            # a symbolic label in operand-1 that must become a line number
            # so the in-repo interpreter's @counter jump lands correctly.
            out.append(
                (None, "set", (operands[0], str(label_lines[operands[1]])))
            )
            continue
        out.append((None, opcode, operands))
    return out


__all__ = [
    "SubroutineConfig",
    "select_promotions",
    "emit_with_subroutines",
    "resolve_subroutine_labels",
]
