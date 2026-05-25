"""mforth → mlog instruction emitter.

Bead mforth-10t.16.  Consumes the stack-checked AST plus the `SlotMap`
produced by `mforth.backend.mlog.slots.allocate_slots` and walks the AST
in source order, producing a flat list of `MlogInstr` 3-tuples
``(label, opcode, operands)`` where ``operands`` is a ``tuple[str, ...]``
of mlog tokens.  ``label`` is currently always ``None`` — label
resolution and jump fix-up belong to a later bead (mforth-10t.17 for
control flow; final serialization joins the tuples to ``op add s0 s0 s1``
lines later still).

Scope of this bead
==================

In:

* Literals (``LitInt``, ``LitStr``).
* Arithmetic: ``+ - * / MOD``.
* Comparison: ``= <> < > <= >=``.
* Logical: ``AND OR NOT``.
* Stack ops: ``DUP DROP SWAP OVER ROT NIP TUCK``.
* Variables: ``VARIABLE`` (no instruction), and the fused patterns
  ``<name> @`` → ``set s<i> <name>`` and ``<value> <name> !`` →
  ``set <name> s<value-slot>``.
* User-defined word call sites — inlined.  v1 strategy.

Also in (bead .17): control flow — `IfThen`, `Begin(kind="until")`,
`Begin(kind="while-repeat")`, `DoLoop`.  See the .17 ship drawer for
the label-encoding contract.

Also in (bead .18): Mindustry primitives — `PRINT`, `PRINTFLUSH`,
`WAIT`, `SENSOR`, `GETLINK`.  See `_try_lift_mindustry_primitive` and
`_emit_mindustry_slot_form` for the literal-lifting vs slot-reference
forms.  The lifting pass is the .19 handoff: it produces the bead-
spec's literal operand forms when the value is known at compile time,
and falls back to slot references for runtime-valued operands (which
.19 will substitute against the sidecar where applicable).

Out (raise `NotImplementedError`):

* The IO printing word `.` (``( n -- )``) — still deferred; the host
  REPL has it, mlog needs a target buffer + printflush sequence.
* Loose variable addresses on the stack (e.g. ``foo DUP @``) — v1 is
  cell-free and has no addressable-value semantics.  Error here so the
  user sees a clear message rather than malformed mlog.

Two deliberate dialect choices
==============================

**Comparison encoding: mlog-native 0/1, not Forth-traditional 0/-1.**
mlog's ``op equal``/``op lessThan``/etc. all write ``0`` or ``1`` into
the result slot.  We keep that encoding rather than translating to
Forth's traditional ``0`` (false) / ``-1`` (true).  Reasons:

1. Zero overhead.  Translating would add an extra ``op`` per comparison
   on the mlog side, and an extra branch on the host side.  Optimization
   priority in mforth is fast > small (`mforth-opt-priority`).
2. mlog's own conditional jumps consume the 0/1 encoding natively, so
   ``IF`` in a future bead can use the comparison result directly.
3. Pragmatic-Forth dialects (Gforth's optional-flag mode, Mecrisp,
   pforth in some configurations) already accept 0/1 as the boolean
   encoding; we are not violating an unbreakable Forth invariant.

The host REPL's built-in primitives (bead .11, in flight) MUST use the
same 0/1 encoding so the REPL ↔ mlog equivalence property
(CLAUDE.md hard rule) holds.  The cross-cutting decision is documented
here AND in the .11 bead text; if the two ever diverge, equivalence
fixtures will catch it loudly.

**SWAP scratch: a single named variable ``__swap_tmp``, not a reserved
stack slot.**  Alternatives considered:

* Reserve a slot above the current `max_slot_index`.  Rejected: every
  SWAP/ROT/TUCK occurrence would inflate the worst-case slot count even
  though only one swap is ever live at a time (codegen is linear, not
  re-entrant).
* Per-occurrence fresh slot.  Same problem, slightly worse.
* Single named variable.  Accepted: mlog has a flat global namespace,
  one extra name costs one (uninitialized) mlog variable, and the
  double-underscore prefix matches the Python convention to signal
  "do not touch".  Mforth refuses to register user words starting with
  ``__`` (enforced by the lexer's word-validity check), so collisions
  are impossible.

VARIABLE/@/! fusion
===================

The dictionary models a `UserVariable` reference as ``StackEffect(0, 1)``
"pushes address" and ``@`` as ``StackEffect(1, 1)`` "reads slot, writes
slot".  In v1 there is no addressable cell — mforth variables ARE mlog
variables; the "address" is just the name.  So at emit time the address-
push WordCall produces no instruction (it is fused into the following
``@`` or ``!``), and the ``@``/``!`` reaches back to the deferred name.

The walker therefore carries a single ``_pending_var`` slot.  Setting it
costs one term lookahead.  If a non-``@``/``!`` term consumes it (the
classic ``foo DUP @`` mistake), we raise — there is no way to model an
on-stack address in cell-free v1.

The user is free to write ``foo @ DUP`` (push value, duplicate it) —
which is what they almost always mean.

User-definition inlining
========================

A call site for ``: name ... ;`` expands inline as the body's emitted
instructions with every ``s<k>`` rewritten to ``s<k + offset>`` where
``offset = caller_depth_at_call_site - callee_in_arity``.  The body has
already been compiled with frame offset ``callee_in_arity`` (the slot
allocator's contract) so the rewrite is a pure string-substitution on
the operand tokens.  Named variables (``foo``, ``__swap_tmp``) are not
rewritten — they live in the flat global mlog namespace.

This is the strategy from the v1 design drawer; the @counter-trick
subroutine emission is a Tier C size-only fallback (bead mforth-10t.39),
opt-in via ``-Osize`` or auto-triggered on instruction-budget overflow.
Inlining wins on speed always — that's the priority.
"""

from __future__ import annotations

from typing import Optional

from dataclasses import replace as dc_replace

from mforth.backend.mlog.slots import SlotMap, allocate_slots
from mforth.dictionary import (
    BuiltinWord,
    Definition,
    UserVariable,
)
from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    LitStr,
    Program,
    VarRef,
    WordCall,
)
from mforth.stackcheck import StackcheckResult, stackcheck


# ---------------------------------------------------------------------------
# Wire shape
# ---------------------------------------------------------------------------

# A single mlog instruction in the emitter's intermediate form.
#   (label, opcode, operands)
# `label` is a string label or None (always None in this bead — labels
# are introduced by control-flow emission in .17).
# `opcode` is the mlog mnemonic ("set", "op", "jump", ...).
# `operands` is a tuple of strings, each a mlog token (variable name,
# literal, opcode-secondary like "add" or "equal", etc.).
MlogInstr = tuple  # type alias: tuple[Optional[str], str, tuple[str, ...]]


# Forth word name (UPPER) → mlog `op` opcode.  These are the binary ops
# that take (read_slots=(a, b), write_slots=(c,)) and emit
# `op <mlog_op> c a b`.
_BINARY_OP_MAP: dict[str, str] = {
    # Arithmetic
    "+": "add",
    "-": "sub",
    "*": "mul",
    # mforth-dlr (2026-05-23): Forth `/` emits mlog `op div` (float
    # division), NOT `op idiv` (integer). The host REPL primitive uses
    # Python's `/` which is float division, so emitting `idiv` here
    # diverged on every program using `/` and violated CLAUDE.md's
    # headline REPL ↔ mlog equivalence property. Documented divergence
    # from Forth tradition (which uses integer `/`) — mforth's pragmatic
    # dialect choice favors the Python-natural feel of the REPL.
    "/": "div",
    "MOD": "mod",
    # Comparison — mlog's 0/1 result is kept verbatim (see docstring)
    "=": "equal",
    "<>": "notEqual",
    "<": "lessThan",
    ">": "greaterThan",
    "<=": "lessThanEq",
    ">=": "greaterThanEq",
    # Logical
    "AND": "land",
    "OR": "or",
}


# Mindustry primitives still deferred to a later bead.  Bead .18 shipped
# the five v1 Mindustry primitives (PRINT, PRINTFLUSH, WAIT, SENSOR,
# GETLINK); only the generic IO printing word `.` remains deferred (the
# host REPL has it; mlog needs a target buffer + printflush sequence
# that v1 does not yet codegen).  We enumerate the deferred set so the
# error message names the user's word accurately and so the catch-all
# NotImplementedError doesn't swallow typos that should have been
# resolver errors.
_DEFERRED_MINDUSTRY: frozenset[str] = frozenset({
    ".",
})


# Mindustry primitives implemented by bead .18.  See `_emit_mindustry`
# and the literal-lifting handling in `_emit_body`.  The dictionary
# tags these `"mindustry"`; we enumerate them explicitly for dispatch
# so a new mindustry word added to the dictionary fails loudly instead
# of being silently treated as a "WordCall with no emit handler".
_MINDUSTRY_PRIMITIVES: frozenset[str] = frozenset({
    "PRINT", "PRINTFLUSH", "WAIT", "SENSOR", "GETLINK",
    # CONTROL block-instructions (bead mforth-cto).
    "CONTROL-ENABLED", "CONTROL-CONFIG", "CONTROL-SHOOT",
    "CONTROL-SHOOTP", "CONTROL-COLOR",
})


# CONTROL-* word → (mlog sub-command, total operand count after the
# sub-command). mlog's `control` instruction is always 5 operands after
# the sub-command; unused tail slots are padded with "0" in the emit.
# `block_arity` is how many operands come from the data stack (block +
# extras); `pad` is how many zero-padding operands follow.
_CONTROL_SHAPES: dict[str, tuple[str, int]] = {
    # name: (sub, total_stack_ops)
    "CONTROL-ENABLED": ("enabled", 2),  # block + flag, then 3 zeros
    "CONTROL-CONFIG":  ("config",  2),  # block + value, then 3 zeros
    "CONTROL-SHOOT":   ("shoot",   4),  # block + x + y + shoot, no pad
    "CONTROL-SHOOTP":  ("shootp",  3),  # block + unit + shoot, 1 zero
    "CONTROL-COLOR":   ("color",   4),  # block + r + g + b, no pad
}


# Bead mforth-eaz: every BuiltinWord whose tag is in this set AND whose
# name starts with "@" is a 0-in/1-out @-identifier (magic var, content
# name, sensor prop, or tile sentinel). Standalone emit: `set s<i>
# @name`. Lifting fast path: when one such word immediately precedes
# SENSOR / PRINTFLUSH / PRINT, the bare @name folds into the operand
# and the otherwise-required `set s<i> @name` is elided. See
# `_emit_at_identifier_slot_form` and `_try_lift_mindustry_primitive`.
_MINDUSTRY_AT_TAGS: frozenset[str] = frozenset({
    "mindustry-magic",
    "mindustry-item",
    "mindustry-liquid",
    "mindustry-unit",
    "mindustry-block",
    "mindustry-sensor-prop",
})


# Primitives whose operand positions accept a *bare* mlog identifier
# (no quotes) when a LitStr immediately precedes them at compile time.
# The lifting pass strips outer quotes for these positions.  Cross-ref
# the mlog reference drawer: `printflush` takes a block handle (bare
# identifier in mlog source); `sensor` takes a block and a property
# (both bare).  PRINT, in contrast, accepts any value — including a
# quoted string literal — so its lifted operand keeps the quotes.
_STRIP_QUOTES_ON_LIFT: frozenset[str] = frozenset({"PRINTFLUSH", "SENSOR"})


# The single scratch variable used by SWAP/ROT/TUCK.  See module
# docstring for the rationale on "named scratch, not reserved slot".
_SWAP_SCRATCH = "__swap_tmp"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def emit(result: StackcheckResult, slot_map: Optional[SlotMap] = None) -> list:
    """Emit mlog instructions for `result.program`.

    Returns a flat ``list[MlogInstr]`` (i.e. ``list[tuple[Optional[str],
    str, tuple[str, ...]]]``) in execution order.  User definitions are
    inlined at their call sites; no top-level prologue/epilogue is
    emitted at this layer (label resolution + ``end`` insertion are
    later beads).

    The `slot_map` argument is accepted for API symmetry with bead .15
    but the emitter recomputes it internally — first running a small AST
    fusion pass (VARIABLE-name pairs are removed; ``<varname> @`` becomes
    ``VarRef(fetch)``; ``<value> <varname> !`` becomes ``VarRef(store)``)
    then re-running stackcheck + slot allocation on the fused AST.  Two
    reasons it has to work this way:

    * The slot allocator (bead .15) treats a UserVariable WordCall as a
      ``(0, 1)`` push, so without fusion every ``VARIABLE foo`` consumes
      a stack slot that the emitted instructions never actually use.
      The slot indices in the original ``SlotMap`` would be off-by-one
      (or more) compared to the post-fusion instruction stream.
    * Fusion at the AST level keeps the slot allocator pure — it doesn't
      need to special-case the dictionary's UserVariable entries.  The
      stack-checker grew a tiny VarRef arm for this purpose (and as
      forward-compat for a future resolver pass that synthesises VarRefs
      directly).

    For callers wanting the *unfused* behaviour (debugging / inspection),
    construct an `_Emitter` directly with a matching `SlotMap`.
    """
    fused_program = _fuse_variable_patterns(result.program, result.dictionary)
    fused_result = stackcheck(fused_program, dictionary=result.dictionary)
    fused_slots = allocate_slots(fused_result)
    emitter = _Emitter(fused_result, fused_slots)
    return emitter.emit_program()


def _fuse_variable_patterns(program: Program, dictionary) -> Program:
    """Return a copy of `program` with VARIABLE / ``<varname> @`` /
    ``<varname> !`` patterns fused.

    Three rewrites, applied recursively to every term sequence:

    1. ``WordCall("VARIABLE"), WordCall(<name>)`` → removed.
    2. ``WordCall(<varname>), WordCall("@")`` → ``VarRef(name, "fetch")``.
    3. ``WordCall(<varname>), WordCall("!")`` → ``VarRef(name, "store")``.

    A WordCall is a "varname" iff `dictionary.lookup(name)` returns a
    `UserVariable`.  The fused VarRef takes the original WordCall's
    ``src_loc`` so error messages still point at the right place.

    If a ``<varname>`` WordCall is followed by anything other than @/!
    (or appears at the end of a body), it survives as a plain WordCall;
    the emit pass then raises ``NotImplementedError`` because v1 has no
    addressable cell to represent the bare address on the stack.
    """
    def is_var(name: str) -> bool:
        entry = dictionary.lookup(name)
        return isinstance(entry, UserVariable)

    def fuse(terms: list) -> list:
        out: list = []
        i = 0
        while i < len(terms):
            t = terms[i]
            if (
                isinstance(t, WordCall)
                and t.name.upper() == "VARIABLE"
                and i + 1 < len(terms)
                and isinstance(terms[i + 1], WordCall)
            ):
                i += 2
                continue
            if (
                isinstance(t, WordCall)
                and is_var(t.name)
                and i + 1 < len(terms)
                and isinstance(terms[i + 1], WordCall)
                and terms[i + 1].name == "@"
            ):
                out.append(VarRef(name=t.name, mode="fetch", src_loc=t.src_loc))
                i += 2
                continue
            if (
                isinstance(t, WordCall)
                and is_var(t.name)
                and i + 1 < len(terms)
                and isinstance(terms[i + 1], WordCall)
                and terms[i + 1].name == "!"
            ):
                out.append(VarRef(name=t.name, mode="store", src_loc=t.src_loc))
                i += 2
                continue
            if isinstance(t, IfThen):
                out.append(dc_replace(t, then_body=fuse(t.then_body), else_body=fuse(t.else_body)))
            elif isinstance(t, Begin):
                out.append(dc_replace(t, body=fuse(t.body), cond_body=fuse(t.cond_body)))
            elif isinstance(t, DoLoop):
                out.append(dc_replace(t, body=fuse(t.body)))
            else:
                out.append(t)
            i += 1
        return out

    new_defs = [
        dc_replace(d, body=fuse(d.body)) for d in program.definitions
    ]
    return Program(definitions=new_defs, main=fuse(program.main))


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class _Emitter:
    def __init__(self, result: StackcheckResult, slot_map: SlotMap) -> None:
        self.result = result
        self.slots = slot_map
        self.dictionary = result.dictionary
        # Monotonic per-construct counters used to mint unique labels.
        # See bead .17 — labels live in mlog's flat global line-address
        # namespace, so the counter is program-global (not per-scope).
        # Bead .19's line-resolver replaces label tokens with absolute
        # 0-indexed line numbers; until then, labels appear in tuples'
        # first slot ("attached to the next instruction") or as
        # ``(label, None, None)`` sentinels when no instruction follows.
        self._if_counter = 0
        self._begin_counter = 0
        self._do_counter = 0
        # Pending labels queued by the control-flow arms; flushed onto
        # the next emitted instruction by ``_emit`` (or as sentinels by
        # ``_flush_pending_labels`` at block / program end).
        self._pending_labels: list = []
        # Active DO/LOOP nesting — innermost on top. Each entry is the
        # DO instance counter ``N``; ``I`` reads ``__do_idx_<stack[-1]>``
        # and ``J`` reads ``__do_idx_<stack[-2]>``. Per-N indexing means
        # nested DO/LOOPs never collide on the counter variable.
        self._loop_stack: list = []

    # ---- top-level ------------------------------------------------------

    def emit_program(self) -> list:
        out: list = []
        # Definitions are not emitted in line — they expand at call sites.
        # Only `main` produces instructions at the top level.
        self._emit_body(out, self.result.program.main, slot_rewrite=None)
        # Flush any labels that ended up trailing the last instruction in
        # main (e.g. ``1 IF THEN`` has a ``L_if_0_end`` after the body).
        self._flush_pending_labels(out)
        return out

    # ---- label plumbing -------------------------------------------------

    def _queue_label(self, label: str) -> None:
        """Mark `label` to attach to the next emitted instruction.

        Multiple labels can stack at the same position (degenerate
        ``1 IF ELSE THEN`` puts ``L_if_0_else`` and ``L_if_0_end`` at
        the same line). The flush rule in ``_emit`` keeps source order:
        earlier-queued labels become sentinel tuples preceding the
        instruction; the latest-queued label attaches to it. When no
        instruction follows, all pending labels are emitted as sentinels.
        """
        self._pending_labels.append(label)

    def _emit(self, out: list, opcode: str, operands: tuple) -> None:
        """Emit one instruction, attaching pending labels per the rule
        in ``_queue_label``."""
        if self._pending_labels:
            for lab in self._pending_labels[:-1]:
                out.append((lab, None, None))
            label = self._pending_labels[-1]
            self._pending_labels = []
        else:
            label = None
        out.append((label, opcode, operands))

    def _flush_pending_labels(self, out: list) -> None:
        for lab in self._pending_labels:
            out.append((lab, None, None))
        self._pending_labels = []

    # ---- body walker ----------------------------------------------------

    def _emit_body(self, out: list, body: list, slot_rewrite) -> None:
        """Walk a sequence of terms, emitting instructions.

        `slot_rewrite` is either None (top-level / main; no rewriting)
        or a callable mapping a slot name like ``"s2"`` → the caller's
        equivalent slot name.  Named variables (anything not matching
        ``s\\d+``) are passed through unchanged.

        The AST has already been fused by ``_fuse_variable_patterns``,
        so VARIABLE declarations and the ``<varname> @`` / ``<varname> !``
        patterns are gone from the input.  Any *remaining* WordCall that
        resolves to a UserVariable is a bare address-push — which v1
        cannot represent (no addressable cells).  The ``pending_var``
        check below catches those at emit time with a clear error
        message rather than producing malformed mlog.

        Mindustry-primitive literal lifting (bead .18) runs here as an
        index-based lookahead: when a ``LitStr`` / ``LitInt`` immediately
        precedes a lifting-eligible primitive (PRINT, PRINTFLUSH, SENSOR),
        the literal's value is folded into the primitive's operand and
        the otherwise-required ``set s<i> <value>`` is elided.  WAIT and
        GETLINK never lift — their operands are intrinsically runtime
        values.  See ``_try_lift_mindustry_primitive`` for the matcher.
        """
        pending_var: Optional[str] = None
        i = 0
        while i < len(body):
            consumed = self._try_lift_mindustry_primitive(
                out, body, i, slot_rewrite, pending_var
            )
            if consumed > 0:
                # A lifting pattern matched; advance past all consumed
                # terms.  pending_var stays None — the patterns never
                # start with a UserVariable WordCall.
                i += consumed
                continue
            term = body[i]
            pending_var = self._emit_term(out, term, slot_rewrite, pending_var)
            i += 1
        if pending_var is not None:
            raise NotImplementedError(
                f"VARIABLE address '{pending_var}' left on the stack — v1 mforth "
                f"is cell-free and does not support manipulating variable "
                f"addresses as stack values"
            )

    def _emit_term(
        self,
        out: list,
        term,
        slot_rewrite,
        pending_var: Optional[str],
    ) -> Optional[str]:
        """Emit one term; return the new ``pending_var`` state.

        If the term is a UserVariable WordCall, sets pending_var and
        emits nothing.  If the term is ``@`` / ``!`` and pending_var is
        set, emits the fused single ``set`` instruction.  Otherwise emits
        normally and asserts pending_var is None (consumed by something
        that isn't @/!).
        """
        # Literals — straight push.
        if isinstance(term, LitInt):
            self._guard_no_pending(pending_var, term)
            writes = self._rw(term, slot_rewrite).writes
            self._emit(out, "set", (writes[0], str(term.value)))
            return None

        if isinstance(term, LitFloat):
            # Float literal lowering (bead mforth-xk7). mlog accepts
            # Python's ``repr(float)`` verbatim — decimal form for
            # ordinary magnitudes (e.g. ``0.95``, ``3.14``, ``-2.5``)
            # and scientific form for very small/large magnitudes
            # (e.g. ``1e-05``). The in-game mlog tokenizer parses both.
            self._guard_no_pending(pending_var, term)
            writes = self._rw(term, slot_rewrite).writes
            self._emit(out, "set", (writes[0], repr(term.value)))
            return None

        if isinstance(term, LitStr):
            self._guard_no_pending(pending_var, term)
            writes = self._rw(term, slot_rewrite).writes
            # mlog string literals are inline-quoted; preserve the quotes.
            self._emit(out, "set", (writes[0], f'"{term.value}"'))
            return None

        if isinstance(term, VarRef):
            # Reserved for future resolver output; behave like a fused
            # @/! the resolver already worked out.
            self._guard_no_pending(pending_var, term)
            rw = self._rw(term, slot_rewrite)
            if term.mode == "fetch":
                self._emit(out, "set", (rw.writes[0], term.name))
            elif term.mode == "store":
                self._emit(out, "set", (term.name, rw.reads[0]))
            else:
                raise ValueError(f"unknown VarRef mode {term.mode!r}")
            return None

        if isinstance(term, WordCall):
            return self._emit_wordcall(out, term, slot_rewrite, pending_var)

        if isinstance(term, IfThen):
            self._guard_no_pending(pending_var, term)
            self._emit_if_then(out, term, slot_rewrite)
            return None

        if isinstance(term, Begin):
            self._guard_no_pending(pending_var, term)
            self._emit_begin(out, term, slot_rewrite)
            return None

        if isinstance(term, DoLoop):
            self._guard_no_pending(pending_var, term)
            self._emit_do_loop(out, term, slot_rewrite)
            return None

        raise TypeError(f"unknown Term type {type(term).__name__}")

    # ---- WordCall dispatch ---------------------------------------------

    def _emit_wordcall(
        self,
        out: list,
        term: WordCall,
        slot_rewrite,
        pending_var: Optional[str],
    ) -> Optional[str]:
        entry = self.dictionary.lookup(term.name)

        # UserVariable — defer; the following @/! will fuse.
        if isinstance(entry, UserVariable):
            self._guard_no_pending(pending_var, term)
            return entry.name

        # @ / ! with a pending variable — fuse to a single set.
        if isinstance(entry, BuiltinWord) and entry.name in ("@", "!") and pending_var is not None:
            rw = self._rw(term, slot_rewrite)
            if entry.name == "@":
                # ( addr -- value ): emit `set s<i> <name>`.  The slot
                # allocator gave @ a read AND a write at the same slot;
                # we want the write slot (where the value should land).
                self._emit(out, "set", (rw.writes[0], pending_var))
            else:  # "!"
                # ( value addr -- ): emit `set <name> s<value-slot>`.
                # The slot allocator gave ! reads = (value_slot, addr_slot)
                # but we fused the addr-push so the addr_slot was never
                # actually written; the value is in reads[0].
                self._emit(out, "set", (pending_var, rw.reads[0]))
            return None

        # VARIABLE declaration site — emits nothing.
        if isinstance(entry, BuiltinWord) and entry.name == "VARIABLE":
            self._guard_no_pending(pending_var, term)
            return None

        # Mindustry primitives still deferred (currently just `.`).
        if isinstance(entry, BuiltinWord) and entry.name in _DEFERRED_MINDUSTRY:
            self._guard_no_pending(pending_var, term)
            raise NotImplementedError(
                f"Mindustry primitive '{entry.name}' is not yet supported by "
                f"the mlog emitter — see bead mforth-10t.18 OUT OF SCOPE"
            )

        # Mindustry primitives implemented by .18 — slot-reference path
        # (the literal-lifting fast path is handled by
        # `_try_lift_mindustry_primitive` in `_emit_body` BEFORE we get
        # here).  Reaching this arm means the primitive consumed a
        # runtime value (computed result, fetched VARIABLE, etc.) — the
        # bead .19 sidecar-substitution pass will need to trace such
        # operands back to their literal sources if final-output bare
        # names are required.
        if isinstance(entry, BuiltinWord) and entry.name in _MINDUSTRY_PRIMITIVES:
            self._guard_no_pending(pending_var, term)
            self._emit_mindustry_slot_form(
                out, entry.name, self._rw(term, slot_rewrite)
            )
            return None

        # Builtin binary op.
        if isinstance(entry, BuiltinWord) and entry.name in _BINARY_OP_MAP:
            self._guard_no_pending(pending_var, term)
            mlog_op = _BINARY_OP_MAP[entry.name]
            rw = self._rw(term, slot_rewrite)
            self._emit(out, "op", (mlog_op, rw.writes[0], rw.reads[0], rw.reads[1]))
            return None

        # NOT — unary; mlog `op not` takes (result, a, 0).
        if isinstance(entry, BuiltinWord) and entry.name == "NOT":
            self._guard_no_pending(pending_var, term)
            rw = self._rw(term, slot_rewrite)
            self._emit(out, "op", ("not", rw.writes[0], rw.reads[0], "0"))
            return None

        # Stack ops.
        if isinstance(entry, BuiltinWord) and entry.tag == "stack":
            self._guard_no_pending(pending_var, term)
            self._emit_stack_op(out, entry.name, self._rw(term, slot_rewrite))
            return None

        # I / J — read the active DO/LOOP counter into the next stack
        # slot.  Bead .17 added the loop-stack tracking and the
        # ``__do_idx_<N>`` per-instance counter variable.  Out-of-context
        # use (I/J with no enclosing DO/LOOP, or J with only one) is a
        # contract violation stackcheck can't catch — raise here so the
        # error message names the word.
        if isinstance(entry, BuiltinWord) and entry.name in ("I", "J"):
            self._guard_no_pending(pending_var, term)
            depth_needed = 1 if entry.name == "I" else 2
            if len(self._loop_stack) < depth_needed:
                raise NotImplementedError(
                    f"DO/LOOP counter '{entry.name}' used outside the required "
                    f"enclosing DO/LOOP context (need {depth_needed} active "
                    f"loop(s), have {len(self._loop_stack)})"
                )
            loop_n = (
                self._loop_stack[-1]
                if entry.name == "I"
                else self._loop_stack[-2]
            )
            rw = self._rw(term, slot_rewrite)
            self._emit(out, "set", (rw.writes[0], f"__do_idx_{loop_n}"))
            return None

        # Mindustry @-identifier (magic var / content / sensor-prop /
        # tile sentinel) — bead mforth-eaz. Slot-form push: read the bare
        # `@name` into the next stack slot. The lifting fast path in
        # `_try_lift_mindustry_primitive` handles the common case where
        # the @-identifier feeds straight into SENSOR / PRINTFLUSH /
        # PRINT; reaching this arm means the value is being consumed by
        # something else (arithmetic, a stack op, etc.) and needs to
        # actually land in a slot.
        if (
            isinstance(entry, BuiltinWord)
            and entry.tag in _MINDUSTRY_AT_TAGS
            and entry.name.startswith("@")
        ):
            self._guard_no_pending(pending_var, term)
            rw = self._rw(term, slot_rewrite)
            self._emit(out, "set", (rw.writes[0], entry.name))
            return None

        # User-definition call — inline the body with offset rewriting.
        if isinstance(entry, Definition):
            self._guard_no_pending(pending_var, term)
            self._emit_user_def_call(out, entry, term, slot_rewrite)
            return None

        # Should not happen — the resolver pass would have raised on a
        # missing entry.  Defensive only.
        raise NotImplementedError(
            f"WordCall '{term.name}' has no emit handler "
            f"(dictionary entry: {type(entry).__name__})"
        )

    # ---- stack-op shapes ------------------------------------------------

    def _emit_stack_op(self, out: list, name: str, rw) -> None:
        """Emit one of DUP/DROP/SWAP/OVER/ROT/NIP/TUCK using `rw` slots
        already supplied by the slot allocator (and possibly rewritten
        by an inlined call)."""
        reads = rw.reads
        writes = rw.writes

        if name == "DUP":
            # (1, 2): reads (s_top,), writes (s_top, s_top+1).  Emit
            # one set: writes[1] = reads[0].
            self._emit(out, "set", (writes[1], reads[0]))
            return

        if name == "DROP":
            # (1, 0): nothing to emit; the slot becomes dead.
            return

        if name == "OVER":
            # (2, 3): reads (s_b, s_top), writes (s_b, s_top, s_new).
            # Emit one set: writes[2] = reads[0].
            self._emit(out, "set", (writes[2], reads[0]))
            return

        if name == "NIP":
            # (2, 1): reads (s_b, s_top), writes (s_b,).
            # Emit one set: writes[0] = reads[1].
            self._emit(out, "set", (writes[0], reads[1]))
            return

        if name == "SWAP":
            # (2, 2): reads (s_b, s_top), writes (s_b, s_top).
            # Three sets via __swap_tmp.
            self._emit(out, "set", (_SWAP_SCRATCH, reads[0]))
            self._emit(out, "set", (writes[0], reads[1]))
            self._emit(out, "set", (writes[1], _SWAP_SCRATCH))
            return

        if name == "ROT":
            # (3, 3): reads (a, b, c), writes (a', b', c') = (b, c, a).
            # Four sets via __swap_tmp.
            self._emit(out, "set", (_SWAP_SCRATCH, reads[0]))
            self._emit(out, "set", (writes[0], reads[1]))
            self._emit(out, "set", (writes[1], reads[2]))
            self._emit(out, "set", (writes[2], _SWAP_SCRATCH))
            return

        if name == "TUCK":
            # (2, 3): a b → b a b.  reads (a, b), writes (b, a, b).
            # tmp = b; b' = a; a' = tmp; new_top = tmp.
            self._emit(out, "set", (_SWAP_SCRATCH, reads[1]))
            self._emit(out, "set", (writes[1], reads[0]))
            self._emit(out, "set", (writes[0], _SWAP_SCRATCH))
            self._emit(out, "set", (writes[2], _SWAP_SCRATCH))
            return

        raise NotImplementedError(f"stack op '{name}' has no emit handler")

    # ---- Mindustry primitive emission (bead .18) -----------------------

    def _try_lift_mindustry_primitive(
        self,
        out: list,
        body: list,
        i: int,
        slot_rewrite,
        pending_var: Optional[str],
    ) -> int:
        """If `body[i:]` starts a lifting pattern, emit the fused form
        and return the number of terms consumed; otherwise return 0.

        Lifting patterns (bead .18 — see module docstring + test_emit_
        mindustry.py for the contract):

        * ``LitInt|LitStr  +  PRINT``        → ``print <value>``     (2 terms)
        * ``LitInt|LitStr  +  PRINTFLUSH``   → ``printflush <value>`` (2 terms,
          quotes stripped — block handles are bare identifiers in mlog)
        * ``X  +  LitInt|LitStr  +  SENSOR`` → ``sensor <write> <X-or-slot> <prop>``
          where ``X`` is either ANOTHER literal (then both lift) or a
          term that has already emitted normally and left its result in
          the block slot.  The prop literal always lifts when present.

        The lifting elides the ``set s<i> <value>`` that the literal
        would otherwise emit on its own.  Slot allocation is unaffected
        — the slot allocator still records the literal's slot, but the
        emitter just doesn't write to it.  Downstream instructions
        don't reference the elided slot (the only consumer was the
        primitive, which now has the value inline).

        WAIT and GETLINK do NOT lift — their operands are intrinsically
        runtime values (seconds counts, link indices) and the bead's
        stated emission for both uses slot references.

        The function does not consume `pending_var` paths — a lifting
        pattern starts with a LitInt/LitStr or another primitive's
        output, never with a UserVariable WordCall.  If `pending_var`
        is set when called, return 0 and let `_emit_term` raise the
        helpful "address-on-stack" error.
        """
        if pending_var is not None:
            return 0

        # Helper: does `term` at `body[k]` resolve to a Mindustry
        # primitive named `name`?
        def is_primitive(k: int, name: str) -> bool:
            if k >= len(body):
                return False
            t = body[k]
            if not isinstance(t, WordCall):
                return False
            entry = self.dictionary.lookup(t.name)
            return (
                isinstance(entry, BuiltinWord)
                and entry.name == name
            )

        # SENSOR: 3-term `LitStr LitStr SENSOR` (both lift) — check this
        # first so the 2-term PRINT/PRINTFLUSH match doesn't preempt it.
        if (
            i + 2 < len(body)
            and isinstance(body[i], LitStr)
            and isinstance(body[i + 1], LitStr)
            and is_primitive(i + 2, "SENSOR")
        ):
            block_value = body[i].value
            prop_value = body[i + 1].value
            sensor = body[i + 2]
            rw = self._rw(sensor, slot_rewrite)
            self._emit(out, "sensor", (rw.writes[0], block_value, prop_value))
            return 3

        # SENSOR: 2-term tail `(any) LitStr SENSOR` — only prop lifts.
        # The (any) term emits normally (handled by the outer loop
        # before we reach the SENSOR), THEN this matcher fires on a
        # 2-term window `LitStr + SENSOR`.  Block becomes the slot
        # reference at reads[0] (which is the slot the previous term
        # wrote into).
        if (
            i + 1 < len(body)
            and isinstance(body[i], LitStr)
            and is_primitive(i + 1, "SENSOR")
        ):
            prop_value = body[i].value
            sensor = body[i + 1]
            rw = self._rw(sensor, slot_rewrite)
            # reads = (block_slot, prop_slot); we substitute the lifted
            # bare prop for the prop_slot position.
            self._emit(
                out, "sensor", (rw.writes[0], rw.reads[0], prop_value)
            )
            return 2

        # PRINT: 2-term `LitInt|LitFloat|LitStr PRINT` → `print <value>`
        # (quotes preserved on LitStr because mlog `print` accepts
        # strings). LitFloat uses ``repr()`` so the operand matches the
        # ``set s<i>`` lowering verbatim — keeps the REPL ↔ mlog
        # equivalence event stream identical regardless of whether the
        # lift fires or the slot-form fallback runs.
        if (
            i + 1 < len(body)
            and isinstance(body[i], (LitStr, LitInt, LitFloat))
            and is_primitive(i + 1, "PRINT")
        ):
            lit = body[i]
            if isinstance(lit, LitStr):
                operand = f'"{lit.value}"'
            elif isinstance(lit, LitFloat):
                operand = repr(lit.value)
            else:
                operand = str(lit.value)
            self._emit(out, "print", (operand,))
            return 2

        # PRINTFLUSH: 2-term `LitStr PRINTFLUSH` → `printflush <name>`
        # (quotes stripped — mlog block handles are bare identifiers).
        # We do NOT lift LitInt → PRINTFLUSH because a numeric block
        # handle doesn't make sense in mlog; the slot-form fallback
        # would catch it as `printflush s<i>` which is also nonsense
        # but at least leaves room for .19 to flag the misuse.
        if (
            i + 1 < len(body)
            and isinstance(body[i], LitStr)
            and is_primitive(i + 1, "PRINTFLUSH")
        ):
            name = body[i].value
            self._emit(out, "printflush", (name,))
            return 2

        # LinkRef lifting (bead .19): a UserVariable WordCall (typically
        # a sidecar-pre-seeded link name like ``display`` or ``switch1``)
        # followed by PRINT / PRINTFLUSH / SENSOR lifts to the bare-name
        # form. Without this lift the `display PRINTFLUSH` pattern would
        # trip the cell-free guard (v1 has no addressable cells), even
        # though the program is well-formed against a sidecar.
        #
        # This is the .18 cross-bead note #5 ("Resolver synthesis of a
        # LinkRef Term") realized — instead of synthesizing a new Term
        # type, we recognize the existing WordCall(UserVariable) shape
        # and emit the lifted form directly. The .19 finalize pass then
        # substitutes the in-game name for Mode A links.
        def is_uservar(k: int) -> Optional[str]:
            if k >= len(body):
                return None
            t = body[k]
            if not isinstance(t, WordCall):
                return None
            entry = self.dictionary.lookup(t.name)
            if isinstance(entry, UserVariable):
                return entry.name
            return None

        # SENSOR: 3-term `<link-uservar> LitStr SENSOR`.
        if (
            i + 2 < len(body)
            and (uvname := is_uservar(i)) is not None
            and isinstance(body[i + 1], LitStr)
            and is_primitive(i + 2, "SENSOR")
        ):
            prop_value = body[i + 1].value
            sensor = body[i + 2]
            rw = self._rw(sensor, slot_rewrite)
            self._emit(out, "sensor", (rw.writes[0], uvname, prop_value))
            return 3

        # PRINTFLUSH: 2-term `<link-uservar> PRINTFLUSH`.
        if (
            i + 1 < len(body)
            and (uvname := is_uservar(i)) is not None
            and is_primitive(i + 1, "PRINTFLUSH")
        ):
            self._emit(out, "printflush", (uvname,))
            return 2

        # PRINT: 2-term `<link-uservar> PRINT` — emits the bare name,
        # which mlog `print` accepts (it prints the variable's value).
        if (
            i + 1 < len(body)
            and (uvname := is_uservar(i)) is not None
            and is_primitive(i + 1, "PRINT")
        ):
            self._emit(out, "print", (uvname,))
            return 2

        # Bead mforth-eaz: Mindustry @-identifier lifting — parallel to
        # the LinkRef-uservar lifts above. Lets `<block> @copper SENSOR`,
        # `@message1 PRINTFLUSH`, `@time PRINT` all fold to single
        # instructions with bare `@name` operands and no preceding `set`.
        def is_at_identifier(k: int) -> Optional[str]:
            """Return the @-identifier's name if body[k] is one, else None."""
            if k >= len(body):
                return None
            t = body[k]
            if not isinstance(t, WordCall):
                return None
            entry = self.dictionary.lookup(t.name)
            if (
                isinstance(entry, BuiltinWord)
                and entry.tag in _MINDUSTRY_AT_TAGS
                and entry.name.startswith("@")
            ):
                return entry.name
            return None

        # SENSOR: 3-term `<link-uservar> <@-prop> SENSOR`.
        if (
            i + 2 < len(body)
            and (uvname := is_uservar(i)) is not None
            and (atprop := is_at_identifier(i + 1)) is not None
            and is_primitive(i + 2, "SENSOR")
        ):
            sensor = body[i + 2]
            rw = self._rw(sensor, slot_rewrite)
            self._emit(out, "sensor", (rw.writes[0], uvname, atprop))
            return 3

        # SENSOR: 3-term `<@-block> <@-prop> SENSOR` (both lift). Two
        # @-identifiers folded into one instruction.
        if (
            i + 2 < len(body)
            and (atblock := is_at_identifier(i)) is not None
            and (atprop := is_at_identifier(i + 1)) is not None
            and is_primitive(i + 2, "SENSOR")
        ):
            sensor = body[i + 2]
            rw = self._rw(sensor, slot_rewrite)
            self._emit(out, "sensor", (rw.writes[0], atblock, atprop))
            return 3

        # SENSOR: 3-term `LitStr <@-prop> SENSOR` — block is a quoted
        # string literal, prop is an @-identifier.
        if (
            i + 2 < len(body)
            and isinstance(body[i], LitStr)
            and (atprop := is_at_identifier(i + 1)) is not None
            and is_primitive(i + 2, "SENSOR")
        ):
            block_value = body[i].value
            sensor = body[i + 2]
            rw = self._rw(sensor, slot_rewrite)
            self._emit(out, "sensor", (rw.writes[0], block_value, atprop))
            return 3

        # SENSOR: 2-term tail `<@-prop> SENSOR` — block came from an
        # earlier-emitted term (computed handle); only prop lifts.
        if (
            i + 1 < len(body)
            and (atprop := is_at_identifier(i)) is not None
            and is_primitive(i + 1, "SENSOR")
        ):
            sensor = body[i + 1]
            rw = self._rw(sensor, slot_rewrite)
            # reads = (block_slot, prop_slot); the prop_slot is the one
            # the @-identifier would have written into — but the lift
            # elides that write, so we use the bare @name directly.
            self._emit(out, "sensor", (rw.writes[0], rw.reads[0], atprop))
            return 2

        # PRINTFLUSH: 2-term `<@-name> PRINTFLUSH`.
        if (
            i + 1 < len(body)
            and (atname := is_at_identifier(i)) is not None
            and is_primitive(i + 1, "PRINTFLUSH")
        ):
            self._emit(out, "printflush", (atname,))
            return 2

        # PRINT: 2-term `<@-name> PRINT` — mlog `print` accepts a bare
        # identifier and prints the value of the variable.
        if (
            i + 1 < len(body)
            and (atname := is_at_identifier(i)) is not None
            and is_primitive(i + 1, "PRINT")
        ):
            self._emit(out, "print", (atname,))
            return 2

        # CONTROL-ENABLED / CONTROL-CONFIG lifting (bead mforth-cto).
        # The two most common USR-script patterns:
        #   cv1 1 CONTROL-ENABLED       → control enabled cv1 1 0 0 0
        #   sorter1 @copper CONTROL-CONFIG → control config sorter1 @copper 0 0 0
        #   sorter1 S" @lead" CONTROL-CONFIG → control config sorter1 @lead 0 0 0
        # Block source: link-uservar (most common), LitStr (occasional), or
        # @-identifier (rare). Value source: LitInt (enabled flag), LitStr
        # or @-identifier (config target).
        def lift_control_block_value(sub_name: str, target: str) -> int:
            """Try to lift `<block> <value> <CONTROL-target>` patterns.

            Returns 3 if a lift fired, 0 otherwise. `sub_name` is the
            mlog sub-command ("enabled", "config"); `target` is the
            Forth word name we expect at body[i+2].
            """
            if i + 2 >= len(body):
                return 0
            if not is_primitive(i + 2, target):
                return 0
            # Block operand resolution.
            block_op: Optional[str] = None
            if (uvname := is_uservar(i)) is not None:
                block_op = uvname
            elif (atblock := is_at_identifier(i)) is not None:
                block_op = atblock
            elif isinstance(body[i], LitStr):
                block_op = body[i].value
            if block_op is None:
                return 0
            # Value operand resolution.
            value_op: Optional[str] = None
            if isinstance(body[i + 1], LitInt):
                value_op = str(body[i + 1].value)
            elif isinstance(body[i + 1], LitStr):
                value_op = body[i + 1].value
            elif (atval := is_at_identifier(i + 1)) is not None:
                value_op = atval
            if value_op is None:
                return 0
            self._emit(
                out,
                "control",
                (sub_name, block_op, value_op, "0", "0", "0"),
            )
            return 3

        consumed = lift_control_block_value("enabled", "CONTROL-ENABLED")
        if consumed:
            return consumed
        consumed = lift_control_block_value("config", "CONTROL-CONFIG")
        if consumed:
            return consumed

        # CONTROL-ENABLED / CONTROL-CONFIG lifting with a stack-computed
        # VALUE operand (bead mforth-vdt). The USR "All In" /
        # "ConveyorBlock" / "Just Charge" pattern: a hysteresis or
        # threshold comparison drives the flag, the block name stays a
        # sidecar link. The natural mforth port (Forth stack order is
        # `( block flag -- )`):
        #
        #   graphC base @itemCapacity SENSOR base @graphite SENSOR >
        #          CONTROL-ENABLED
        #
        # ...where the bare-uservar push of `graphC` would normally
        # trip v1's cell-free guard. This lift recognises the variable-
        # length shape `<block-literal-source> <value-computation>
        # <CONTROL-target>` and emits the value-computation normally
        # (it writes to the slot the CONTROL primitive expects to read
        # for the value) followed by a single
        # `control <sub> <block> s<value-slot> 0 0 0` instruction with
        # the block kept literal.
        #
        # The forward scan tracks simulated stack depth (block pushed at
        # +1; each intervening term applies its (in_arity, out_arity))
        # and stops at the first CONTROL-ENABLED / CONTROL-CONFIG
        # encountered with depth == 2 immediately before that primitive
        # consumes its operands. By stackcheck having already accepted
        # the program, the intervening segment is guaranteed net (0, 1).
        #
        # Priority: this lifter runs AFTER the 3-term all-literal
        # lifters above so the fully-literal patterns (e.g.
        # `cv1 1 CONTROL-ENABLED`) still fire and emit a literal value
        # rather than collapsing into a slot-reference.
        def lift_control_block_slot_value() -> int:
            """Try to lift `<block-literal> <value-comp> CONTROL-target`.

            Returns `k+1` (consumed term count) if a lift fired, 0
            otherwise.
            """
            # body[i] must be a block-literal-source.
            block_op: Optional[str] = None
            if (uvname := is_uservar(i)) is not None:
                block_op = uvname
            elif (atblock := is_at_identifier(i)) is not None:
                block_op = atblock
            elif isinstance(body[i], LitStr):
                block_op = body[i].value
            if block_op is None:
                return 0
            # Forward-scan, tracking simulated stack depth (block push
            # = +1 at body[i+1] entry). Stop at the first CONTROL-
            # ENABLED / CONTROL-CONFIG with depth == 2 immediately
            # before its consume.
            depth = 1
            j = i + 1
            while j < len(body):
                term = body[j]
                # Detect a matching CONTROL-target.
                if is_primitive(j, "CONTROL-ENABLED") and depth == 2:
                    sub_name = "enabled"
                    matched = True
                elif is_primitive(j, "CONTROL-CONFIG") and depth == 2:
                    sub_name = "config"
                    matched = True
                else:
                    matched = False
                if matched:
                    # Emit the intervening (value-computation) segment
                    # normally, then the lifted control instruction.
                    self._emit_body(out, body[i + 1 : j], slot_rewrite)
                    control_term = body[j]
                    rw = self._rw(control_term, slot_rewrite)
                    if len(rw.reads) < 2:
                        # Shouldn't happen for a well-stackchecked
                        # CONTROL term; bail and let the slot-form
                        # fallback handle it.
                        return 0
                    # reads[0] = block-slot (the slot the bare-uservar
                    # push WOULD have written to — elided here).
                    # reads[1] = value-slot (where the intervening
                    # computation actually wrote the value).
                    value_slot = rw.reads[1]
                    self._emit(
                        out,
                        "control",
                        (sub_name, block_op, value_slot, "0", "0", "0"),
                    )
                    return j - i + 1
                # Update simulated depth using the term's stack effect.
                # Bail out (return 0, let fallback handle) on any term
                # whose effect we can't statically read — control flow,
                # nested user-defs with recursion, etc. Those shapes
                # don't appear in the USR target scripts.
                eff = _term_static_effect(term, self.dictionary, self.result)
                if eff is None:
                    return 0
                new_depth = depth - eff[0] + eff[1]
                if new_depth < 0:
                    # Underflow — would have failed stackcheck; bail.
                    return 0
                depth = new_depth
                j += 1
            return 0

        consumed = lift_control_block_slot_value()
        if consumed:
            return consumed

        return 0

    def _emit_mindustry_slot_form(self, out: list, name: str, rw) -> None:
        """Emit the slot-reference form for a Mindustry primitive whose
        operands were not lifted at the body-walker layer.

        Per the bead .18 spec mappings:

        * ``PRINT``     → ``print s<i-1>``
        * ``PRINTFLUSH``→ ``printflush s<i-1>``
        * ``WAIT``      → ``wait s<i-1>``
        * ``SENSOR``    → ``sensor s<i-2> s<i-2> s<i-1>`` (write into the
          block-slot per the (2, 1) effect; bead .19 substitutes a bare
          name for the second occurrence if a sidecar reference was the
          source)
        * ``GETLINK``   → ``getlink s<i-1> s<i-1>`` (link-index slot is
          reused as the output handle slot, matching the slot
          allocator's (1, 1) same-slot pattern used by NOT)

        Reaching this method means the operands were computed (came
        from arithmetic, a fetched VARIABLE, an earlier primitive's
        output, etc.) — the .19 sidecar-substitution pass owns any
        further rewriting that final-output bare names require.
        """
        reads = rw.reads
        writes = rw.writes

        if name == "PRINT":
            # ( str -- ): one read, zero writes.
            self._emit(out, "print", (reads[0],))
            return

        if name == "PRINTFLUSH":
            # ( block -- ): one read, zero writes.
            self._emit(out, "printflush", (reads[0],))
            return

        if name == "WAIT":
            # ( seconds -- ): one read, zero writes.
            self._emit(out, "wait", (reads[0],))
            return

        if name == "SENSOR":
            # ( block prop -- value ): reads = (block, prop), writes =
            # (value,).  mlog `sensor <result> <block> <property>` —
            # result is allowed to alias the block slot because mlog
            # reads operands before writing the result within an
            # instruction.
            self._emit(out, "sensor", (writes[0], reads[0], reads[1]))
            return

        if name == "GETLINK":
            # ( i -- block ): reads = (i,), writes = (block,) — slot
            # allocator gives same slot for both (the (1, 1) same-slot
            # pattern).  mlog `getlink <result> <i>`.
            self._emit(out, "getlink", (writes[0], reads[0]))
            return

        # CONTROL-* (bead mforth-cto). Slot-form fallback for the five
        # block-control sub-commands. The first read is the block; the
        # remaining reads are the sub-command's extra operands in stack
        # order. mlog's `control` always takes 5 operands after the
        # sub-command, so we pad trailing positions with "0".
        if name in _CONTROL_SHAPES:
            sub, _total = _CONTROL_SHAPES[name]
            ops = [sub, *reads]
            while len(ops) < 6:  # sub + 5 operands total
                ops.append("0")
            self._emit(out, "control", tuple(ops))
            return

        raise NotImplementedError(
            f"Mindustry primitive '{name}' has no slot-form emit handler"
        )

    # ---- user-def inlining ---------------------------------------------

    def _emit_user_def_call(
        self,
        out: list,
        defn: Definition,
        call: WordCall,
        outer_rewrite,
    ) -> None:
        """Inline `defn`'s body at the call site, rewriting slot names."""
        eff = self.result.effects[defn.name]
        # The body was emitted (well, slot-allocated) with frame offset
        # `eff.in_arity`.  The caller's depth at the call site is
        # `read_start = absolute_depth - eff.in_arity` (see slot
        # allocator).  So we need the body's `s<k>` (which is already at
        # caller-absolute index `eff.in_arity + (k - eff.in_arity) = k`
        # IF the call is at absolute depth eff.in_arity) — but in
        # general the caller's depth differs.  We can read it off the
        # call's own read_slots: the slot allocator gave the call site
        # `reads = (s<read_start>, ..., s<read_start + in_arity - 1>)`,
        # so `read_start` is `int(reads[0][1:])` (assuming in_arity > 0)
        # or the call's write slots[0] base (for in_arity == 0 defs).
        call_reads = self.slots.reads(call)
        call_writes = self.slots.writes(call)
        if eff.in_arity > 0:
            caller_base = int(call_reads[0][1:])
        elif eff.out_arity > 0:
            caller_base = int(call_writes[0][1:])
        else:
            # () → () definition.  Body emission doesn't reference any
            # stack slot the caller cares about; use 0 as a no-op base.
            caller_base = 0
        # Body was allocated with frame_offset = entry_depth = in_arity,
        # so its first input lives at body-slot s0.  The caller's first
        # input lives at caller-slot s<caller_base>.  Map body s<k> to
        # caller s<k + caller_base>.
        offset = caller_base

        def rewrite(slot_name: str) -> str:
            """Rewrite an inner s<k> to outer s<k + offset>.  Pass
            through named variables and the swap scratch unchanged.
            Compose with any outer rewrite (handles nested inlines)."""
            if slot_name.startswith("s") and slot_name[1:].isdigit():
                inner_idx = int(slot_name[1:])
                rewritten = f"s{inner_idx + offset}"
            else:
                rewritten = slot_name
            if outer_rewrite is not None:
                return outer_rewrite(rewritten)
            return rewritten

        # Walk the FUSED body, not `defn.body` — the dictionary holds
        # the original Definition with the pre-fusion body, but the slot
        # map was built from the fused program (via
        # ``_fuse_variable_patterns`` in ``emit()``).  When the body
        # contains a control-flow term (IfThen/Begin/DoLoop) the fusion
        # pass `dc_replace`s it, producing a new object the slot map
        # keys by identity — so the original term would KeyError on
        # ``self.slots.reads(term)``.
        fused_defn = next(
            (
                d for d in self.result.program.definitions
                if d.name == defn.name
            ),
            defn,
        )
        self._emit_body(out, fused_defn.body, slot_rewrite=rewrite)

    # ---- control-flow emission (bead .17) ------------------------------

    def _emit_if_then(self, out: list, term: IfThen, slot_rewrite) -> None:
        """IF / [ELSE] / THEN.

        The flag is in `rw.reads[0]` (slot allocator recorded it on the
        IfThen at depth `D-1`).  Two skeletons:

        * No else: jump to end-label if flag==0; emit then-body; place
          end-label.
        * With else: jump to else-label if flag==0; emit then-body;
          unconditional jump to end-label; place else-label; emit
          else-body; place end-label.

        Empty bodies are legal — the labels still get placed (as
        sentinels if no instruction follows them).
        """
        n = self._if_counter
        self._if_counter += 1
        rw = self._rw(term, slot_rewrite)
        flag = rw.reads[0]
        end_label = f"L_if_{n}_end"

        if term.else_body:
            else_label = f"L_if_{n}_else"
            self._emit(out, "jump", (else_label, "equal", flag, "0"))
            self._emit_body(out, term.then_body, slot_rewrite)
            self._emit(out, "jump", (end_label, "always", "0", "0"))
            self._queue_label(else_label)
            self._emit_body(out, term.else_body, slot_rewrite)
            self._queue_label(end_label)
        else:
            self._emit(out, "jump", (end_label, "equal", flag, "0"))
            self._emit_body(out, term.then_body, slot_rewrite)
            self._queue_label(end_label)

    def _emit_begin(self, out: list, term: Begin, slot_rewrite) -> None:
        """BEGIN / UNTIL or BEGIN / WHILE / REPEAT.

        The flag slot for both kinds is `s<depth_in(term) + frame_offset>`.
        We can read the absolute depth directly from the slot allocator's
        record on the Begin term itself: the term's reads/writes are both
        empty, but the slot allocator did record it.  We need the depth
        a different way — read it off the FIRST term in the body (its
        ``depth_in`` is the entry depth).  Or — simpler — the flag in
        an UNTIL lives at the same slot as the body's *final* push,
        which is the slot just above the entry depth.

        We compute the entry-depth slot index via the slot-rewrite walk:
        we know any literal in the body would write to s<entry_depth +
        local_depth>.  The cleanest path: the slot allocator's
        max-slot-index data is too coarse; instead, peek at the
        sub-AST.  We do the bookkeeping ourselves by reading
        `result.depth_in(term)` and applying `slot_rewrite` to a synthetic
        ``s<D>`` token.
        """
        n = self._begin_counter
        self._begin_counter += 1
        top_label = f"L_begin_{n}_top"

        # The flag lives at slot s<entry_depth + 0>, where entry_depth is
        # what stackcheck recorded for the Begin term (per the .15 slot
        # allocator's `walk` — the body walks with the same frame_offset,
        # so the entry depth in the absolute frame is just
        # `result.depth_in(begin)` + frame_offset_of_caller).  We don't
        # carry the frame_offset explicitly here, but the slot_rewrite
        # callback does — it knows the mapping.  So compute the slot
        # name as we would for a literal at the body's entry depth, then
        # rewrite.
        entry_depth = self.result.depth_in(term)
        raw_flag_slot = f"s{entry_depth}"
        flag = slot_rewrite(raw_flag_slot) if slot_rewrite is not None else raw_flag_slot

        if term.kind == "until":
            self._queue_label(top_label)
            self._emit_body(out, term.body, slot_rewrite)
            self._emit(out, "jump", (top_label, "equal", flag, "0"))
        elif term.kind == "while-repeat":
            after_label = f"L_begin_{n}_after"
            self._queue_label(top_label)
            self._emit_body(out, term.body, slot_rewrite)  # the test
            self._emit(out, "jump", (after_label, "equal", flag, "0"))
            self._emit_body(out, term.cond_body, slot_rewrite)
            self._emit(out, "jump", (top_label, "always", "0", "0"))
            self._queue_label(after_label)
        else:
            raise ValueError(f"unknown Begin kind {term.kind!r}")

    def _emit_do_loop(self, out: list, term: DoLoop, slot_rewrite) -> None:
        """DO / LOOP.

        Slot allocator gave us `reads = (limit_slot, index_slot)` per
        the ANS Forth `( limit index -- )` convention (index on top).

        Lowering uses two named mlog variables — `__do_idx_<N>` and
        `__do_limit_<N>` — so nested DO/LOOPs cannot collide.  `I`/`J`
        in the body resolve via the emitter's `_loop_stack` (innermost
        N on top).

        Prologue:
            set __do_idx_N s<index_slot>
            set __do_limit_N s<limit_slot>
        Top label, body, then increment + back-jump:
            L_do_N_top:
              <body>
              op add __do_idx_N __do_idx_N 1
              jump L_do_N_top lessThan __do_idx_N __do_limit_N
        """
        n = self._do_counter
        self._do_counter += 1
        rw = self._rw(term, slot_rewrite)
        limit_slot, index_slot = rw.reads
        idx_var = f"__do_idx_{n}"
        limit_var = f"__do_limit_{n}"
        top_label = f"L_do_{n}_top"

        self._emit(out, "set", (idx_var, index_slot))
        self._emit(out, "set", (limit_var, limit_slot))
        self._queue_label(top_label)
        self._loop_stack.append(n)
        try:
            self._emit_body(out, term.body, slot_rewrite)
        finally:
            self._loop_stack.pop()
        self._emit(out, "op", ("add", idx_var, idx_var, "1"))
        self._emit(out, "jump", (top_label, "lessThan", idx_var, limit_var))

    # ---- helpers --------------------------------------------------------

    def _rw(self, term, slot_rewrite):
        """Pull the (reads, writes) tuples for `term` from the slot map
        and apply `slot_rewrite` to every name.

        Returns a small namespace-like object with `.reads` and
        `.writes`.  Using a tiny class keeps call sites readable
        (`rw.reads[0]`) and lets us avoid a third allocation tier.
        """
        reads = self.slots.reads(term)
        writes = self.slots.writes(term)
        if slot_rewrite is not None:
            reads = tuple(slot_rewrite(s) for s in reads)
            writes = tuple(slot_rewrite(s) for s in writes)
        return _RW(reads, writes)

    def _guard_no_pending(self, pending_var: Optional[str], term) -> None:
        if pending_var is not None:
            raise NotImplementedError(
                f"VARIABLE address '{pending_var}' is being consumed by "
                f"'{getattr(term, 'name', type(term).__name__)}' rather than "
                f"by @ or ! — v1 mforth is cell-free and does not support "
                f"manipulating variable addresses as stack values"
            )


class _RW:
    """Tiny holder for rewritten (reads, writes) slot tuples."""

    __slots__ = ("reads", "writes")

    def __init__(self, reads: tuple, writes: tuple) -> None:
        self.reads = reads
        self.writes = writes


def _term_static_effect(
    term, dictionary, result: StackcheckResult
) -> Optional[tuple[int, int]]:
    """Return a term's static `(in_arity, out_arity)` if statically known.

    Used by the CONTROL slot-value lifter (bead mforth-vdt) to forward-
    scan over the value-computation segment between the block-literal
    source and the CONTROL primitive without re-running stackcheck.
    Returns ``None`` for terms whose effect can't be cheaply read out
    here (control flow, definitions whose effect isn't in `result`),
    signalling the caller to bail out and let the slot-form fallback
    handle the case.
    """
    if isinstance(term, (LitInt, LitStr)):
        return (0, 1)
    if isinstance(term, VarRef):
        return (0, 1) if term.mode == "fetch" else (1, 0)
    if isinstance(term, WordCall):
        entry = dictionary.lookup(term.name)
        if isinstance(entry, BuiltinWord):
            eff = entry.stack_effect
            return (eff.in_arity, eff.out_arity)
        if isinstance(entry, UserVariable):
            return (0, 1)
        if isinstance(entry, Definition):
            eff = result.effects.get(entry.name)
            if eff is None:
                return None
            return (eff.in_arity, eff.out_arity)
        return None
    # IfThen / Begin / DoLoop — control flow inside a CONTROL value
    # computation is unusual; if it appears, bail out of the lift.
    return None


__all__ = ["MlogInstr", "emit"]
