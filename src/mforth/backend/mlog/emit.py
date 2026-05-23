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

Out (raise `NotImplementedError`):

* Control flow (`IfThen`, `Begin`, `DoLoop`) — bead .17.
* Mindustry primitives (`PRINT`, `PRINTFLUSH`, `WAIT`, `SENSOR`,
  `GETLINK`) and the IO word `.` — a later bead.
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
    "/": "idiv",  # Forth tradition: integer divide
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


# Mindustry primitives — recognised but deferred to a later bead.  We
# enumerate them here so the error message names the user's word
# accurately, and so the catch-all NotImplementedError doesn't swallow
# typos that should have been resolver errors.
_DEFERRED_MINDUSTRY: frozenset[str] = frozenset({
    "PRINT", "PRINTFLUSH", "WAIT", "SENSOR", "GETLINK",
    # IO printing of a stack value — also deferred (host REPL has it,
    # mlog needs a target buffer + printflush sequence).
    ".",
})


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

    # ---- top-level ------------------------------------------------------

    def emit_program(self) -> list:
        out: list = []
        # Definitions are not emitted in line — they expand at call sites.
        # Only `main` produces instructions at the top level.
        self._emit_body(out, self.result.program.main, slot_rewrite=None)
        return out

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
        """
        pending_var: Optional[str] = None
        for term in body:
            pending_var = self._emit_term(out, term, slot_rewrite, pending_var)
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
            out.append((None, "set", (writes[0], str(term.value))))
            return None

        if isinstance(term, LitStr):
            self._guard_no_pending(pending_var, term)
            writes = self._rw(term, slot_rewrite).writes
            # mlog string literals are inline-quoted; preserve the quotes.
            out.append((None, "set", (writes[0], f'"{term.value}"')))
            return None

        if isinstance(term, VarRef):
            # Reserved for future resolver output; behave like a fused
            # @/! the resolver already worked out.
            self._guard_no_pending(pending_var, term)
            rw = self._rw(term, slot_rewrite)
            if term.mode == "fetch":
                out.append((None, "set", (rw.writes[0], term.name)))
            elif term.mode == "store":
                out.append((None, "set", (term.name, rw.reads[0])))
            else:
                raise ValueError(f"unknown VarRef mode {term.mode!r}")
            return None

        if isinstance(term, WordCall):
            return self._emit_wordcall(out, term, slot_rewrite, pending_var)

        if isinstance(term, (IfThen, Begin, DoLoop)):
            raise NotImplementedError(
                f"control flow ({type(term).__name__}) is not yet supported by "
                f"the mlog emitter — see bead mforth-10t.17"
            )

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
                out.append((None, "set", (rw.writes[0], pending_var)))
            else:  # "!"
                # ( value addr -- ): emit `set <name> s<value-slot>`.
                # The slot allocator gave ! reads = (value_slot, addr_slot)
                # but we fused the addr-push so the addr_slot was never
                # actually written; the value is in reads[0].
                out.append((None, "set", (pending_var, rw.reads[0])))
            return None

        # VARIABLE declaration site — emits nothing.
        if isinstance(entry, BuiltinWord) and entry.name == "VARIABLE":
            self._guard_no_pending(pending_var, term)
            return None

        # Mindustry primitives — deferred.
        if isinstance(entry, BuiltinWord) and entry.name in _DEFERRED_MINDUSTRY:
            self._guard_no_pending(pending_var, term)
            raise NotImplementedError(
                f"Mindustry primitive '{entry.name}' is not yet supported by "
                f"the mlog emitter — see bead mforth-10t.16 OUT OF SCOPE"
            )

        # Builtin binary op.
        if isinstance(entry, BuiltinWord) and entry.name in _BINARY_OP_MAP:
            self._guard_no_pending(pending_var, term)
            mlog_op = _BINARY_OP_MAP[entry.name]
            rw = self._rw(term, slot_rewrite)
            out.append((None, "op", (mlog_op, rw.writes[0], rw.reads[0], rw.reads[1])))
            return None

        # NOT — unary; mlog `op not` takes (result, a, 0).
        if isinstance(entry, BuiltinWord) and entry.name == "NOT":
            self._guard_no_pending(pending_var, term)
            rw = self._rw(term, slot_rewrite)
            out.append((None, "op", ("not", rw.writes[0], rw.reads[0], "0")))
            return None

        # Stack ops.
        if isinstance(entry, BuiltinWord) and entry.tag == "stack":
            self._guard_no_pending(pending_var, term)
            self._emit_stack_op(out, entry.name, self._rw(term, slot_rewrite))
            return None

        # I / J — defer until DO/LOOP (.17) gives them meaning.  They
        # appear in (0, 1) form in the dictionary so the slot allocator
        # treats them as a plain push, but the underlying counter
        # register lives in DO/LOOP codegen.  In a body that contains
        # an I or J without an enclosing DO/LOOP, stackcheck won't have
        # erred (it can't know), but the emitter can't produce sensible
        # mlog without the DO/LOOP machinery.  Raise.
        if isinstance(entry, BuiltinWord) and entry.name in ("I", "J"):
            self._guard_no_pending(pending_var, term)
            raise NotImplementedError(
                f"DO/LOOP counter '{entry.name}' requires control-flow emission "
                f"(bead mforth-10t.17)"
            )

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
            out.append((None, "set", (writes[1], reads[0])))
            return

        if name == "DROP":
            # (1, 0): nothing to emit; the slot becomes dead.
            return

        if name == "OVER":
            # (2, 3): reads (s_b, s_top), writes (s_b, s_top, s_new).
            # Emit one set: writes[2] = reads[0].
            out.append((None, "set", (writes[2], reads[0])))
            return

        if name == "NIP":
            # (2, 1): reads (s_b, s_top), writes (s_b,).
            # Emit one set: writes[0] = reads[1].
            out.append((None, "set", (writes[0], reads[1])))
            return

        if name == "SWAP":
            # (2, 2): reads (s_b, s_top), writes (s_b, s_top).
            # Three sets via __swap_tmp.
            out.append((None, "set", (_SWAP_SCRATCH, reads[0])))
            out.append((None, "set", (writes[0], reads[1])))
            out.append((None, "set", (writes[1], _SWAP_SCRATCH)))
            return

        if name == "ROT":
            # (3, 3): reads (a, b, c), writes (a', b', c') = (b, c, a).
            # Four sets via __swap_tmp.
            out.append((None, "set", (_SWAP_SCRATCH, reads[0])))
            out.append((None, "set", (writes[0], reads[1])))
            out.append((None, "set", (writes[1], reads[2])))
            out.append((None, "set", (writes[2], _SWAP_SCRATCH)))
            return

        if name == "TUCK":
            # (2, 3): a b → b a b.  reads (a, b), writes (b, a, b).
            # tmp = b; b' = a; a' = tmp; new_top = tmp.
            out.append((None, "set", (_SWAP_SCRATCH, reads[1])))
            out.append((None, "set", (writes[1], reads[0])))
            out.append((None, "set", (writes[0], _SWAP_SCRATCH)))
            out.append((None, "set", (writes[2], _SWAP_SCRATCH)))
            return

        raise NotImplementedError(f"stack op '{name}' has no emit handler")

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

        self._emit_body(out, defn.body, slot_rewrite=rewrite)

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


__all__ = ["MlogInstr", "emit"]
