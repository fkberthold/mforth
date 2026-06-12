"""Loop-invariant code motion (LICM) — v2 ``-Ofast`` Tier-B pass.

Bead **mforth-10t.38**. Hoists loop-invariant, side-effect-free Term
runs out of ``DO/LOOP`` and ``BEGIN`` bodies so they execute once
instead of every iteration. This is the canonical case where the
project's *fast > small* priority actually pays off: a 5-instruction
invariant expression lifted out of a 100-iteration loop saves ~500
executed instructions per program tick.

Standalone by design
====================

This module is **dead code until bead mforth-10t.40 wires the ``-O``
levels**. It is intentionally NOT imported by ``finalize.py`` /
``cli.py`` / ``cli_compile.py`` / ``emit.py``. It takes a parsed +
(implicitly) stack-valid :class:`~mforth.parse.Program` and returns a
**new** ``Program`` with the same observable behaviour. Callers re-run
:func:`mforth.stackcheck.stackcheck` on the result; the transform keeps
the AST stack-valid by construction (every lifted run is replaced by an
equal-net-effect sequence of literal pushes).

What counts as hoistable (the conservative whitelist)
====================================================

The headline equivalence property (CLAUDE.md hard rule) forbids any
change to the observable EventStream. To guarantee that, LICM only
touches runs that are **provably pure, deterministic, and
self-contained**:

* Every term is a literal (:class:`LitInt`, :class:`LitFloat`,
  :class:`LitStr`) OR a :class:`WordCall` to a builtin whose family is
  a pure value transform — ``arith`` (``+ - * / MOD = < > AND OR NOT``
  …) or ``stack`` (``DUP DROP SWAP OVER ROT NIP TUCK``). These emit no
  events and depend only on their stack inputs.
* The run is **self-contained**: walked from an empty local stack it
  never underflows (consumes nothing from before it) and ends with a
  net surplus of K >= 1 values.
* Consequently the run is a pure function of constants → it is
  *constant-foldable*. LICM evaluates it once at compile time and
  replaces the in-loop occurrence with the K resulting literal pushes.

Because such a run produces **no events**, running it once (folded) or
N times (original) yields a byte-for-byte identical EventStream — the
hoist is safe. Everything else is left untouched:

* ``I`` / ``J`` (loop-counter reads, family ``control``) — *variant*.
* ``@`` / ``!`` (variable fetch/store, family ``var``) — read/write
  mutable state.
* ``PRINT`` / ``PRINTFLUSH`` / ``WAIT`` / ``SENSOR`` / ``GETLINK`` and
  every ``mindustry-*`` family — side effects or time-varying reads
  (``@time``, ``@tick`` …). Even the constant magic vars (``@pi``) are
  excluded wholesale to keep the rule simple and conservative.
* User :class:`Definition` calls — not analysed for purity in v1
  (could transitively hit any of the above); left in place.
* Any control-flow node (``IfThen``, nested ``DoLoop``, ``Begin``)
  inside a run breaks the run; LICM recurses into those bodies
  separately.

This is deliberately the smallest safe rule. A future bead can widen
the whitelist (e.g. hoisting provably-pure user words, or
non-foldable-but-invariant runs into a true preheader with slot
reads) — but only behind its own equivalence fixtures.
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from typing import Optional

from mforth.dictionary import (
    BuiltinWord,
    Dictionary,
    StackEffect,
    standard_dictionary,
)
from mforth.parse import (
    Begin,
    Definition,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    LitStr,
    Program,
    WordCall,
)


# Builtin families whose words are pure, deterministic value transforms
# (no events, no mutable-state reads, no loop-counter dependence).
_PURE_FAMILIES = frozenset({"arith", "stack"})


# ---------------------------------------------------------------------------
# Purity / invariance predicates
# ---------------------------------------------------------------------------


def _pure_builtin(entry) -> bool:
    return isinstance(entry, BuiltinWord) and entry.tag in _PURE_FAMILIES


def _is_invariant_atom(term, dictionary: Dictionary) -> bool:
    """True if ``term`` is a single literal or a call to a pure builtin.

    Conservatively False for everything else — ``I``/``J`` (control),
    ``@``/``!`` (var), every Mindustry primitive, user-definition calls,
    and any control-flow node.
    """
    if isinstance(term, (LitInt, LitFloat, LitStr)):
        return True
    if isinstance(term, WordCall):
        entry = dictionary.lookup(term.name)
        return _pure_builtin(entry)
    # VarRef, IfThen, DoLoop, Begin, Definition-call → not an atom.
    return False


def _effect_of(term, dictionary: Dictionary) -> StackEffect:
    if isinstance(term, (LitInt, LitFloat, LitStr)):
        return StackEffect(0, 1)
    entry = dictionary.lookup(term.name)  # WordCall, known pure builtin
    return entry.stack_effect


# ---------------------------------------------------------------------------
# Pure constant evaluator (for folding a self-contained invariant run)
# ---------------------------------------------------------------------------


def _apply_pure_word(name: str, stack: list) -> None:
    # Stack ops (family "stack").
    if name == "DUP":
        stack.append(stack[-1])
        return
    if name == "DROP":
        stack.pop()
        return
    if name == "SWAP":
        stack[-1], stack[-2] = stack[-2], stack[-1]
        return
    if name == "OVER":
        stack.append(stack[-2])
        return
    if name == "ROT":  # a b c -> b c a
        c = stack.pop()
        b = stack.pop()
        a = stack.pop()
        stack.extend((b, c, a))
        return
    if name == "NIP":  # a b -> b
        b = stack.pop()
        stack.pop()
        stack.append(b)
        return
    if name == "TUCK":  # a b -> b a b
        b = stack.pop()
        a = stack.pop()
        stack.extend((b, a, b))
        return

    # Arithmetic / comparison / logical (family "arith").
    b = stack.pop()
    a = stack.pop()
    if name == "+":
        stack.append(a + b)
    elif name == "-":
        stack.append(a - b)
    elif name == "*":
        stack.append(a * b)
    elif name == "/":
        stack.append(a / b)  # float division — mforth-dlr
    elif name == "MOD":
        stack.append(a % b)
    elif name == "=":
        stack.append(1 if a == b else 0)
    elif name == "<>":
        stack.append(1 if a != b else 0)
    elif name == "<":
        stack.append(1 if a < b else 0)
    elif name == ">":
        stack.append(1 if a > b else 0)
    elif name == "<=":
        stack.append(1 if a <= b else 0)
    elif name == ">=":
        stack.append(1 if a >= b else 0)
    elif name == "AND":
        stack.append(1 if (a != 0 and b != 0) else 0)
    elif name == "OR":
        stack.append(1 if (a != 0 or b != 0) else 0)
    else:  # pragma: no cover — _PURE_FAMILIES is exhaustive above
        raise AssertionError(f"unexpected pure word {name!r}")


def _const_to_term(value, src_loc) -> object:
    if isinstance(value, str):
        return LitStr(value=value, src_loc=src_loc)
    if isinstance(value, float):
        return LitFloat(value=value, src_loc=src_loc)
    return LitInt(value=int(value), src_loc=src_loc)


# NOT family — pure but unary; handle separately so the binary _apply
# path stays clean.
def _apply_not(stack: list) -> None:
    a = stack.pop()
    stack.append(1 if a == 0 else 0)


# ---------------------------------------------------------------------------
# Core: rewrite one term-list (a loop body), folding invariant runs
# ---------------------------------------------------------------------------


def _maximal_invariant_runs(terms: list, dictionary: Dictionary) -> list:
    """Return a new term list where every maximal self-contained run of
    invariant pure atoms has been collapsed to its folded constants.

    Nested control-flow nodes are recursed into first (so an invariant
    run inside a nested loop is also lifted), then the *current* level is
    scanned for liftable runs.
    """
    # First, recurse into nested structures so inner loops are optimized.
    recursed = [_rewrite_term(t, dictionary) for t in terms]

    out: list = []
    i = 0
    n = len(recursed)
    while i < n:
        term = recursed[i]
        if not _is_invariant_atom(term, dictionary):
            out.append(term)
            i += 1
            continue

        # Greedily extend a self-contained run: track local stack depth,
        # never let it go negative (would consume from before the run).
        run: list = []
        depth = 0
        j = i
        while j < n and _is_invariant_atom(recursed[j], dictionary):
            eff = _effect_of(recursed[j], dictionary)
            if depth - eff.in_arity < 0:
                # This atom would consume a value from before the run —
                # the run is not self-contained past this point.
                break
            depth = depth - eff.in_arity + eff.out_arity
            run.append(recursed[j])
            j += 1

        # A liftable run needs >= 2 atoms (so folding can actually shrink
        # it) and must end with a net surplus depth >= 1. Single literals
        # are already minimal; leave them.
        if len(run) >= 2 and depth >= 1:
            folded = _fold_run(run)
            if folded is not None and len(folded) < len(run):
                out.extend(folded)
                i = j
                continue

        # Not liftable: emit the first atom unchanged and advance by one
        # (the rest of the run is re-examined from i+1).
        out.append(term)
        i += 1

    return out


def _fold_run(run: list) -> Optional[list]:
    """Evaluate a self-contained pure run and return the literal pushes
    for the resulting K values, or None if it cannot be folded (e.g.
    contains a string in an arithmetic position — defensive)."""
    try:
        result_stack = _eval_run(run)
    except (IndexError, TypeError, ZeroDivisionError):
        return None
    src_loc = run[0].src_loc
    return [_const_to_term(v, src_loc) for v in result_stack]


def _eval_run(run: list) -> list:
    """Evaluate a self-contained pure run on an empty local stack and
    return the resulting stack (Python numbers / strings).

    Mirrors the host backend's primitive semantics exactly so the folded
    constants match what the loop would have computed each iteration. The
    unary ``NOT`` builtin is routed separately (the binary ``_apply_pure_word``
    path expects two operands)."""
    stack: list = []
    for t in run:
        if isinstance(t, (LitInt, LitFloat, LitStr)):
            stack.append(t.value)
        elif isinstance(t, WordCall) and t.name.upper() == "NOT":
            _apply_not(stack)
        else:
            _apply_pure_word(t.name.upper(), stack)
    return stack


# ---------------------------------------------------------------------------
# AST walk
# ---------------------------------------------------------------------------


def _rewrite_term(term, dictionary: Dictionary):
    """Recurse into control-flow nodes, applying LICM to their bodies.

    Loop bodies (DoLoop.body, Begin.body, Begin.cond_body) are the
    targets of the hoist. IfThen branches are recursed for nested loops
    but their own top level is not a loop, so a run there isn't hoisted
    by *this* node (it would be hoisted by an enclosing loop's body
    rewrite, which sees the IfThen as an opaque non-atom and recurses).
    """
    if isinstance(term, DoLoop):
        return _dc_replace(
            term, body=_maximal_invariant_runs(term.body, dictionary)
        )
    if isinstance(term, Begin):
        return _dc_replace(
            term,
            body=_maximal_invariant_runs(term.body, dictionary),
            cond_body=_maximal_invariant_runs(term.cond_body, dictionary),
        )
    if isinstance(term, IfThen):
        return _dc_replace(
            term,
            then_body=[_rewrite_term(t, dictionary) for t in term.then_body],
            else_body=[_rewrite_term(t, dictionary) for t in term.else_body],
        )
    return term


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def licm(program: Program, dictionary: Optional[Dictionary] = None) -> Program:
    """Apply loop-invariant code motion to ``program``.

    Returns a **new** :class:`Program`; the input is not mutated. The
    result is stack-valid by construction — callers should re-run
    :func:`mforth.stackcheck.stackcheck` before codegen.

    ``dictionary`` is used to classify ``WordCall`` families; if omitted
    a fresh standard dictionary is used (sufficient because only builtin
    families matter for the purity test, and user definitions are never
    hoisted).
    """
    dictionary = dictionary or standard_dictionary()

    new_defs = [
        _dc_replace(
            d, body=[_rewrite_term(t, dictionary) for t in d.body]
        )
        for d in program.definitions
    ]
    new_main = [_rewrite_term(t, dictionary) for t in program.main]
    return Program(definitions=new_defs, main=new_main)


__all__ = ["licm"]
