"""Common subexpression elimination (CSE) — bead mforth-10t.37.

A v2 Tier-B optimizer pass (priority **fast > small**). Within a single
``Definition`` body — treated as one basic block: the linear top-level
term sequence, NOT descending into ``IfThen`` / ``Begin`` / ``DoLoop``
bodies (those are separate blocks) — value numbering recognizes when the
same *value* is produced twice and reuses the first result instead of
recomputing it.

Why fast > small both win
=========================

Each elided recomputation is real runtime cycles saved every tick. The
stack juggle that replaces it (a ``SWAP``, plus one stash ``DUP`` at the
first occurrence — or nothing at all when the value is already live on
top) costs 0–1 ops; the elided expression typically costs 2–5. Both axes
win.

Eliminable (CSE-able) class
===========================

Only **event-free pure** value-producing terms may be reused:

* literals (``LitInt`` / ``LitFloat`` / ``LitStr``);
* arithmetic / comparison / logical builtins
  (``+ - * / MOD = <> < > <= >= AND OR NOT``);
* a variable *fetch* ``<var> @`` keyed on the variable name + the current
  fetch *generation* — the canonical Forth idiom the bead targets
  (``n @ 1 + n @ 2 +`` reuses ``n @``).

Invalidators
============

A *store* (``<var> !``) bumps the fetch generation, so a later
``<var> @`` keys to a fresh value number and is NOT reused — the negative
case the bead mandates (``n @ ... n ! ... n @``). The other side-effecting
/ event-emitting words — ``.`` / ``PRINT`` / ``PRINTFLUSH`` / ``WAIT`` /
``SENSOR`` / ``GETLINK`` / ``CONTROL-*`` — are never CSE targets and
consume their stack inputs, but they do not mutate a *variable*, so a
pure constant or an unstored fetch stays reusable across them (a ``PRINT``
between two ``3 4 +`` cannot change the constant).

Equivalence preservation (non-negotiable)
=========================================

Every rewrite shape is a stack-effect-neutral substitution that leaves
the final data stack identical. Pure-arithmetic elimination is byte-for-
byte identical on the EventStream (arithmetic + literals + ``DUP``/
``SWAP`` emit nothing). A repeated ``<var> @`` reuse intentionally elides
the provably-redundant second ``VariableReadEvent`` — a simulator
instrumentation artifact mirroring a no-op variable read in real mlog;
the observable *sink* events (PRINT output) stay identical. The tests
execute both forms through the host backend and diff the stream.

Soundness gate
==============

A recurrence is rewritten only when the saved value can be delivered to
the reuse site by a *provably-equivalent* manipulation, and the
intervening segment provably never reaches below the stashed copy:

* prior value still live on the abstract-stack top  → delete the
  recomputation (the live copy already serves);
* prior value consumed, but a ``DUP`` stashed right after its producer
  leaves a copy one slot below the reuse-site top  → replace the
  recomputation with ``SWAP``.

Anything else is left untouched. Conservative-but-correct: a missed
optimization is fine; a wrong one violates the headline equivalence
property. Whole-block bail (return the input verbatim) happens the moment
a term can't be modelled precisely (control flow, user-def call,
@-identifier, bare address, unknown word).

Standalone / do-not-wire
========================

This module is intentionally NOT wired into the default pipeline
(``finalize`` / ``emit`` / ``cli`` / ``cli_compile``). Bead mforth-10t.40
wires the ``-O`` levels. Until then it is dead code exercised only by
``tests/unit/test_cse.py``.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Optional

from mforth.dictionary import (
    BuiltinWord,
    Definition,
    Dictionary,
    UserVariable,
    standard_dictionary,
)
from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    LitStr,
    Program,
    WordCall,
)


# ---------------------------------------------------------------------------
# Word classification
# ---------------------------------------------------------------------------

# Pure, event-free value-producing builtins (name UPPER → arity-in;
# arity-out is always 1).
_PURE_OPS: dict[str, int] = {
    "+": 2, "-": 2, "*": 2, "/": 2, "MOD": 2,
    "=": 2, "<>": 2, "<": 2, ">": 2, "<=": 2, ">=": 2,
    "AND": 2, "OR": 2, "NOT": 1,
}

# Pure stack-shuffling builtins (no computation, no events).
_STACK_OPS = frozenset({"DUP", "DROP", "SWAP", "OVER", "ROT", "NIP", "TUCK"})

_FETCH = "@"
_STORE = "!"

# Side-effecting / event-emitting consumers. Never CSE targets; consume
# their inputs and (for the few that push) produce fresh, unreusable
# values. Only a *store* bumps the fetch generation.
_SIDE_EFFECTING = frozenset({
    ".", "PRINT", "PRINTFLUSH", "WAIT", "SENSOR", "GETLINK",
    "CONTROL-ENABLED", "CONTROL-CONFIG", "CONTROL-SHOOT",
    "CONTROL-SHOOTP", "CONTROL-COLOR",
})


class _Bail(Exception):
    """Raised internally when the block can't be modelled precisely; the
    caller then returns the block unchanged."""


# ---------------------------------------------------------------------------
# Step model — one entry per modelled span of the block
# ---------------------------------------------------------------------------


class _Step:
    """A modelled span of the source block.

    ``terms`` — the source terms this step covers (verbatim).
    ``key``   — canonical value key if this step produces ONE reusable
                value, else ``None`` (opaque / side-effecting / store).
    ``vn``    — the value number produced (only when ``key`` is not None).
    ``depth_after`` — abstract stack depth immediately after the step.
    ``kind``  — ``"lit"`` / ``"op"`` / ``"fetch"`` for value producers,
                else ``None``. Only ``"op"`` / ``"fetch"`` steps (real
                work) are CSE reuse *targets*; bare literals never are.
    ``expr``  — the set of step indices comprising this value's full
                sub-expression (itself + the producers of its operands).
                Used to elide the whole recomputation, not just the
                final op.
    """

    __slots__ = ("terms", "key", "vn", "depth_after", "kind", "expr")

    def __init__(self, terms, key, vn, depth_after, kind=None, expr=None):
        self.terms = terms
        self.key = key
        self.vn = vn
        self.depth_after = depth_after
        self.kind = kind
        self.expr = expr if expr is not None else set()


# ---------------------------------------------------------------------------
# Pattern recognizers
# ---------------------------------------------------------------------------


def _entry(dictionary: Dictionary, term):
    if isinstance(term, WordCall):
        return dictionary.lookup(term.name)
    return None


def _is_var_fetch(terms: list, i: int, dictionary: Dictionary) -> Optional[str]:
    if i + 1 >= len(terms):
        return None
    a, b = terms[i], terms[i + 1]
    if not (isinstance(a, WordCall) and isinstance(b, WordCall)):
        return None
    if b.name != _FETCH:
        return None
    entry = dictionary.lookup(a.name)
    return entry.name if isinstance(entry, UserVariable) else None


def _is_var_store(terms: list, i: int, dictionary: Dictionary) -> Optional[str]:
    if i + 1 >= len(terms):
        return None
    a, b = terms[i], terms[i + 1]
    if not (isinstance(a, WordCall) and isinstance(b, WordCall)):
        return None
    if b.name != _STORE:
        return None
    entry = dictionary.lookup(a.name)
    return entry.name if isinstance(entry, UserVariable) else None


def _swap_word(src_loc):
    return WordCall(name="SWAP", src_loc=src_loc)


def _dup_word(src_loc):
    return WordCall(name="DUP", src_loc=src_loc)


def _span_loc(span):
    for t in span:
        loc = getattr(t, "src_loc", None)
        if loc is not None:
            return loc
    return None


# ---------------------------------------------------------------------------
# Pass 1 — model the block into steps + an abstract VN stack
# ---------------------------------------------------------------------------


def _model_block(terms: list, dictionary: Dictionary) -> list:
    """Return a list of :class:`_Step` modelling ``terms`` precisely, or
    raise :class:`_Bail` if any term can't be modelled."""
    steps: list[_Step] = []
    stack: list[int] = []  # abstract VN stack, bottom→top
    # Parallel stack: the step index that produced each live VN (or None
    # for opaque-origin slots like side-effect outputs / sentinel pushes).
    origin: list = []
    next_vn = [0]
    key_to_vn: dict[tuple, int] = {}
    fetch_gen = [0]

    def fresh() -> int:
        vn = next_vn[0]
        next_vn[0] += 1
        return vn

    def produce(span, key, kind, operand_origins=()) -> None:
        vn = key_to_vn.get(key)
        if vn is None:
            vn = fresh()
            key_to_vn[key] = vn
        my_idx = len(steps)
        # Full expression = self + the expressions of each operand step.
        expr = {my_idx}
        for oidx in operand_origins:
            if oidx is None:
                # operand came from an opaque source — this expression is
                # not self-contained; mark it non-reusable by clearing kind.
                expr = None
                break
            expr |= steps[oidx].expr
        stack.append(vn)
        origin.append(my_idx)
        steps.append(
            _Step(
                span, key, vn, len(stack),
                kind=(kind if expr is not None else None),
                expr=(expr if expr is not None else {my_idx}),
            )
        )

    n = len(terms)
    i = 0
    while i < n:
        term = terms[i]

        if isinstance(term, (IfThen, DoLoop, Begin)):
            raise _Bail  # control flow → separate block; do not optimize

        entry = _entry(dictionary, term)

        # VARIABLE <name> — declaration, pushes nothing.
        if isinstance(entry, BuiltinWord) and entry.name == "VARIABLE":
            steps.append(_Step([term], None, None, len(stack)))
            i += 1
            continue

        # store `<var> !`
        if _is_var_store(terms, i, dictionary) is not None:
            if not stack:
                raise _Bail
            stack.pop()  # `!` consumes the value
            origin.pop()
            fetch_gen[0] += 1
            # stale fetch-keyed canonical entries can no longer be reused
            for k in [k for k in key_to_vn if k[0] == "fetch"]:
                del key_to_vn[k]
            steps.append(_Step(terms[i : i + 2], None, None, len(stack)))
            i += 2
            continue

        # fetch `<var> @`
        fv = _is_var_fetch(terms, i, dictionary)
        if fv is not None:
            produce(terms[i : i + 2], ("fetch", fv, fetch_gen[0]), "fetch")
            i += 2
            continue

        # literal
        if isinstance(term, (LitInt, LitFloat, LitStr)):
            produce([term], ("lit", type(term).__name__, term.value), "lit")
            i += 1
            continue

        # pure op
        if isinstance(entry, BuiltinWord) and entry.name in _PURE_OPS:
            arity = _PURE_OPS[entry.name]
            if len(stack) < arity:
                raise _Bail
            operand_vns = tuple(stack[-arity:])
            operand_origins = tuple(origin[-arity:])
            del stack[-arity:]
            del origin[-arity:]
            produce([term], (entry.name, operand_vns), "op", operand_origins)
            i += 1
            continue

        # pure stack shuffle
        if isinstance(entry, BuiltinWord) and entry.name in _STACK_OPS:
            _apply_stack_op(entry.name, stack, origin)
            steps.append(_Step([term], None, None, len(stack)))
            i += 1
            continue

        # side-effecting consumer
        if isinstance(entry, BuiltinWord) and entry.name in _SIDE_EFFECTING:
            se = entry.stack_effect
            if len(stack) < se.in_arity:
                raise _Bail
            if se.in_arity:
                del stack[-se.in_arity:]
                del origin[-se.in_arity:]
            for _ in range(se.out_arity):
                stack.append(fresh())
                origin.append(None)  # opaque-origin value
            steps.append(_Step([term], None, None, len(stack)))
            i += 1
            continue

        # anything else (user-def call, @-identifier, NULL, bare address,
        # unknown) — cannot model; bail.
        raise _Bail

    return steps


def _apply_stack_op(name: str, stack: list, origin: list) -> None:
    if name == "DUP":
        if not stack:
            raise _Bail
        stack.append(stack[-1])
        origin.append(origin[-1])
    elif name == "DROP":
        if not stack:
            raise _Bail
        stack.pop()
        origin.pop()
    elif name == "SWAP":
        if len(stack) < 2:
            raise _Bail
        stack[-1], stack[-2] = stack[-2], stack[-1]
        origin[-1], origin[-2] = origin[-2], origin[-1]
    elif name == "OVER":
        if len(stack) < 2:
            raise _Bail
        stack.append(stack[-2])
        origin.append(origin[-2])
    elif name == "ROT":
        if len(stack) < 3:
            raise _Bail
        a, b, c = stack[-3], stack[-2], stack[-1]
        stack[-3], stack[-2], stack[-1] = b, c, a
        oa, ob, oc = origin[-3], origin[-2], origin[-1]
        origin[-3], origin[-2], origin[-1] = ob, oc, oa
    elif name == "NIP":
        if len(stack) < 2:
            raise _Bail
        top = stack.pop()
        stack[-1] = top
        otop = origin.pop()
        origin[-1] = otop
    elif name == "TUCK":
        if len(stack) < 2:
            raise _Bail
        a, b = stack[-2], stack[-1]
        stack[-2], stack[-1] = b, a
        stack.append(b)
        oa, ob = origin[-2], origin[-1]
        origin[-2], origin[-1] = ob, oa
        origin.append(ob)
    else:  # pragma: no cover - defensive
        raise _Bail


# ---------------------------------------------------------------------------
# Pass 2 — pick the first sound CSE and rewrite
# ---------------------------------------------------------------------------


def _find_cse(steps: list) -> Optional[tuple]:
    """Find the first sound CSE. Returns a plan tuple
    ``(reuse_expr_set, reuse_start, reuse_loc, shape, stash_after)`` or
    ``None``:

    * ``reuse_expr_set`` — step indices of the recomputation to elide.
    * ``reuse_start``    — first (lowest) step index of that recomputation.
    * ``reuse_loc``      — a SrcLoc for the synthesized reuse op.
    * ``shape``          — ``"dup"`` (prior value live on top → ``DUP``) or
                           ``"stash_swap"`` (stash ``DUP`` after the prior
                           producer, surface with ``SWAP``).
    * ``stash_after``    — step index after which to splice the stash
                           ``DUP`` (``"stash_swap"`` only; else ``None``).

    Only ``op`` / ``fetch`` steps (real work) are reuse targets; bare
    literals never are. Soundness for ``"stash_swap"`` is verified by
    simulating the saved copy's depth across the intervening steps.
    """
    first_seen: dict[tuple, int] = {}
    # Top VN of the abstract stack just before each step (for "dup" shape).
    top_before = _top_vn_before_each(steps)

    for j, step in enumerate(steps):
        if step.key is None or step.kind not in ("op", "fetch"):
            # not a reusable producer; still record literal/op keys so a
            # later identical *expression* keys consistently. We only seed
            # first_seen for reuse-eligible producers.
            if step.key is not None and step.kind in ("op", "fetch"):
                first_seen.setdefault(step.key, j)
            continue
        prior = first_seen.get(step.key)
        if prior is None:
            first_seen[step.key] = j
            continue
        # Recurrence of an op/fetch value. The recomputation to elide is
        # this step's full sub-expression.
        reuse_set = steps[j].expr
        reuse_start = min(reuse_set)
        reuse_loc = _span_loc(steps[j].terms)
        prior_vn = steps[prior].vn

        # Shape "dup": prior value already on top right before the
        # recomputation begins.
        if top_before[reuse_start] == prior_vn:
            return (reuse_set, reuse_start, reuse_loc, "dup", None)

        # Shape "stash_*": stash a DUP right after the prior producer's
        # full expression. The saved copy ends at depth == working-region
        # size at the reuse point: 0 → surface with DUP, 1 → with SWAP.
        prod_end = max(steps[prior].expr)
        surfacer = _stash_surfacer(steps, prod_end, reuse_start)
        if surfacer is not None:
            return (
                reuse_set, reuse_start, reuse_loc, surfacer, prod_end
            )

        # Not soundly reusable at this site; allow a later occurrence to
        # pair against this one instead.
        first_seen[step.key] = j
    return None


def _top_vn_before_each(steps: list) -> list:
    """Return a list ``top[j]`` = the abstract-stack top VN just before
    ``steps[j]`` executes (or ``None`` if empty / ambiguous)."""
    stack: list = []
    top_before: list = []
    for j, step in enumerate(steps):
        top_before.append(stack[-1] if stack else None)
        # Apply this step's net effect to keep the running stack. We only
        # need VNs precisely for value producers; shuffles/opaque steps
        # use depth deltas with sentinels (never equal a real vn).
        prev_depth = steps[j - 1].depth_after if j > 0 else 0
        delta = step.depth_after - prev_depth
        if step.key is not None:
            # value producer nets +1, pushes its vn.
            stack.append(step.vn)
        else:
            if delta >= 0:
                for _ in range(delta):
                    stack.append(None)
            else:
                for _ in range(-delta):
                    if stack:
                        stack.pop()
    return top_before


def _stash_surfacer(steps, prod_end, reuse_start) -> Optional[str]:
    """Decide how to surface a stashed copy of the prior producer's value
    at the reuse site, or ``None`` if unsound.

    A ``DUP`` is spliced right after step ``prod_end``; the saved copy
    then rides at the bottom of the post-stash region while the working
    region (initially size 1 — the producer's value) is operated on by
    the intervening steps. Tracking the working-region size:

    * if it ever goes *negative*, a consumer would reach below the saved
      copy → unsound (return ``None``);
    * at the reuse site the saved copy sits at depth == working size:
      size 0 → it is the top → surface with ``DUP``;
      size 1 → one slot below top → surface with ``SWAP``;
      anything deeper → not handled in v1 (return ``None``).
    """
    work = 1
    for j in range(prod_end + 1, reuse_start):
        prev_depth = steps[j - 1].depth_after
        delta = steps[j].depth_after - prev_depth
        work += delta
        if work < 0:
            return None
    if work == 0:
        # Saved copy IS the top and is the only thing the recomputation
        # would have left — delete the recomputation entirely.
        return "delete"
    if work == 1:
        # Saved copy one slot below the single working item — SWAP it up.
        return "swap"
    return None


def _rewrite(terms, steps, plan) -> list:
    """Apply the chosen CSE plan, returning a new term list.

    ``shape`` is the surfacing op (``"dup"`` or ``"swap"``); ``stash_after``
    is the step index after which to splice a stash ``DUP`` (or ``None``
    for the live-on-top case, where the prior value is still on the stack
    and no stash is needed)."""
    reuse_set, reuse_start, reuse_loc, shape, stash_after = plan
    out: list = []
    for j, step in enumerate(steps):
        if j in reuse_set:
            if j == reuse_start:
                # Replace the whole recomputation with the reuse op.
                # "delete" → nothing; "dup" → DUP; "swap" → SWAP.
                if shape == "swap":
                    out.append(_swap_word(reuse_loc))
                elif shape == "dup":
                    out.append(_dup_word(reuse_loc))
                # "delete" emits no surfacing op.
            # All other steps of the recomputation are dropped.
            continue
        out.extend(step.terms)
        if stash_after is not None and j == stash_after:
            out.append(_dup_word(_span_loc(step.terms)))
    return out


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def cse_terms(terms: list, dictionary: Dictionary) -> list:
    """Common-subexpression-eliminate a single linear basic block.

    Returns a NEW term list (the input is never mutated). Applies CSEs
    repeatedly until a fixed point. If the block can't be modelled
    precisely (control flow, user-def call, etc.) the original list is
    returned unchanged.
    """
    if len(terms) < 2:
        return list(terms)
    current = list(terms)
    # Iterate to a fixed point so multiple independent recurrences all
    # get eliminated. Bounded by term count (each pass removes ≥1 term).
    for _ in range(len(terms) + 1):
        try:
            steps = _model_block(current, dictionary)
        except _Bail:
            return current
        plan = _find_cse(steps)
        if plan is None:
            return current
        current = _rewrite(current, steps, plan)
    return current


def cse_definition(
    defn: Definition, dictionary: Optional[Dictionary] = None
) -> Definition:
    """Return a copy of ``defn`` with CSE applied to its (single-block)
    body. Control-flow and call structure are preserved; only the linear
    top-level term sequence is optimized."""
    d = dictionary if dictionary is not None else standard_dictionary()
    return dc_replace(defn, body=cse_terms(defn.body, d))


def cse_program(
    program: Program, dictionary: Optional[Dictionary] = None
) -> Program:
    """Return a copy of ``program`` with CSE applied to every definition
    body and to ``main``. Standalone — not wired into the default pipeline
    (bead mforth-10t.40 owns the ``-O`` wiring)."""
    d = dictionary if dictionary is not None else standard_dictionary()
    new_defs = [cse_definition(defn, d) for defn in program.definitions]
    return Program(definitions=new_defs, main=cse_terms(program.main, d))


__all__ = [
    "cse_definition",
    "cse_program",
    "cse_terms",
]
