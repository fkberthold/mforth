"""mforth v2 dead-code-elimination pass (bead mforth-10t.36).

A **standalone** v2 optimizer (priority *fast > small*). It is deliberately
NOT wired into the default compile pipeline — bead mforth-10t.40 owns the
``-O`` level wiring that composes this with the other v2 passes. Until then
this module is dead code by design; its only consumers are
``tests/unit/test_dce.py`` and (eventually) the ``-O`` driver.

Why DCE wins on BOTH axes
=========================

Per the bead: unreachable mlog instructions cost runtime even when the
program counter never enters them — they consume the per-processor
instruction budget, which lowers the achievable loop frequency (``fast``),
and they bloat the emitted listing (``small``). Eliminating them is a pure
win.

Two transforms (this pass applies both, in order)
=================================================

(a) **Literal-flag IF/ELSE pruning.** When the term immediately preceding
    an :class:`~mforth.parse.IfThen` is an *already-present* integer or
    float literal (a :class:`~mforth.parse.LitInt` / :class:`LitFloat` the
    parser produced — this pass does NOT fold expressions; composition with
    the constant-fold pass mforth-10t.35 is mforth-10t.40's job), the branch
    is statically decided:

    * non-zero flag  → keep ``then_body``  (``1 IF a ELSE b THEN`` → ``a``)
    * zero flag      → keep ``else_body``  (``0 IF a THEN``        → nothing)

    The literal AND the ``IfThen`` are removed; the surviving branch is
    spliced in and itself recursively pruned.

    *Stack-effect preservation.* ``<flag> IF ... THEN`` consumes exactly the
    flag and then runs the selected arm. After the rewrite the flag literal
    is gone (so it never gets pushed) and the selected arm runs directly.
    Net effect on the data stack is identical: the literal would have pushed
    one value that ``IF`` immediately popped, so removing both is depth-
    neutral, and the surviving arm has the same stack effect it had inside
    the branch. The transformed program re-passes :func:`stackcheck`.

(b) **Unreachable-definition elimination.** Build a call graph rooted at the
    (already-pruned) ``main``; any :class:`~mforth.parse.Definition` not
    transitively reachable from a :class:`~mforth.parse.WordCall` in ``main``
    is removed from ``program.definitions``. When a :class:`Dictionary` is
    supplied it is also dropped from there, so codegen never resolves it.

    Running (a) before (b) is what produces the *combined* win the bead
    calls out: a definition referenced ONLY inside a statically-pruned
    branch becomes unreachable after the prune and is then collected.

Purity
======

:func:`dead_code_eliminate` returns a NEW :class:`Program`; the input is
left unmutated so the caller still has the un-optimized program available
for the equivalence comparison (the headline regression guard). Term
objects that survive unchanged are shared by reference (they are treated as
immutable AST), but every body list that the pass touches is rebuilt fresh.
"""

from __future__ import annotations

from typing import Optional

from mforth.dictionary import Dictionary
from mforth.parse import (
    Begin,
    Definition,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    Program,
    WordCall,
)


# ---------------------------------------------------------------------------
# (a) Literal-flag IF/ELSE pruning
# ---------------------------------------------------------------------------


def _literal_flag(term) -> Optional[bool]:
    """Return the truth value of ``term`` if it is a compile-time numeric
    literal flag, else ``None`` (meaning: not statically known).

    Forth truthiness: any non-zero value is true, zero is false. Both
    ``LitInt`` and ``LitFloat`` qualify; ``0`` and ``0.0`` are false.
    """
    if isinstance(term, (LitInt, LitFloat)):
        return term.value != 0
    return None


def _prune_branches(terms: list) -> list:
    """Return a new term list with literal-flag IFs pruned, recursing into
    every nested body.

    The scan is left-to-right. When a ``LitInt``/``LitFloat`` is immediately
    followed by an ``IfThen``, the pair is replaced by the statically-
    selected branch (itself recursively pruned). The replacement is spliced
    in-line; the scan does NOT advance past it, so a literal that now leads
    the spliced branch can feed a further prune in the next iteration's
    look-ahead — but because we rebuild into ``out`` and re-scan the spliced
    terms by extending the *input* view, we instead recurse on the branch
    eagerly (simpler + equally complete for the nesting the dialect allows).
    """
    out: list = []
    i = 0
    n = len(terms)
    while i < n:
        term = terms[i]
        nxt = terms[i + 1] if i + 1 < n else None
        if isinstance(nxt, IfThen):
            flag = _literal_flag(term)
            if flag is not None:
                branch = nxt.then_body if flag else nxt.else_body
                # Splice the selected branch (recursively pruned). Skip BOTH
                # the literal and the IfThen.
                out.extend(_prune_branches(branch))
                i += 2
                continue
        out.append(_prune_term(term))
        i += 1
    return out


def _prune_term(term):
    """Recursively prune nested bodies of a single control-flow term. Leaf
    terms (literals, word calls, var refs) are returned unchanged."""
    if isinstance(term, IfThen):
        return IfThen(
            then_body=_prune_branches(term.then_body),
            else_body=_prune_branches(term.else_body),
            src_loc=term.src_loc,
        )
    if isinstance(term, DoLoop):
        return DoLoop(body=_prune_branches(term.body), src_loc=term.src_loc)
    if isinstance(term, Begin):
        return Begin(
            body=_prune_branches(term.body),
            kind=term.kind,
            cond_body=_prune_branches(term.cond_body),
            src_loc=term.src_loc,
        )
    return term


# ---------------------------------------------------------------------------
# (b) Unreachable-definition elimination
# ---------------------------------------------------------------------------


def _collect_called_names(terms: list, out: set) -> None:
    """Add the (lower-cased) name of every ``WordCall`` reachable in
    ``terms`` — including those nested inside control-flow bodies — to
    ``out``."""
    for t in terms:
        if isinstance(t, WordCall):
            out.add(t.name.lower())
        elif isinstance(t, IfThen):
            _collect_called_names(t.then_body, out)
            _collect_called_names(t.else_body, out)
        elif isinstance(t, Begin):
            _collect_called_names(t.body, out)
            _collect_called_names(t.cond_body, out)
        elif isinstance(t, DoLoop):
            _collect_called_names(t.body, out)


def _reachable_definitions(program: Program) -> set:
    """Return the set of (lower-cased) definition names transitively
    reachable from ``program.main`` via WordCalls.

    A standard work-list graph traversal: seed with the names called in
    ``main``; for each name that resolves to a user ``Definition``, enqueue
    the names IT calls. Names that resolve to builtins / variables are
    irrelevant to definition reachability and simply have no outgoing edges
    in the definition graph.
    """
    by_name = {d.name.lower(): d for d in program.definitions}

    seed: set = set()
    _collect_called_names(program.main, seed)

    reachable: set = set()
    worklist = [name for name in seed if name in by_name]
    while worklist:
        name = worklist.pop()
        if name in reachable:
            continue
        reachable.add(name)
        defn = by_name[name]
        callees: set = set()
        _collect_called_names(defn.body, callees)
        for callee in callees:
            if callee in by_name and callee not in reachable:
                worklist.append(callee)
    return reachable


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def dead_code_eliminate(
    program: Program, dictionary: Optional[Dictionary] = None
) -> Program:
    """Apply dead-code elimination to ``program`` and return a NEW program.

    Order of operations:

    1. Prune literal-flag IF/ELSE branches in ``main`` and in every
       definition body (transform (a)). This can make some definitions
       unreachable.
    2. Compute the set of definitions transitively reachable from the
       (pruned) ``main`` and drop the rest (transform (b)).

    If ``dictionary`` is provided, the dropped definitions are also removed
    from it (case-insensitively) so a later codegen pass that shares the
    dictionary never resolves a name whose definition no longer exists. The
    input ``program`` is left unmutated.
    """
    # (a) Prune literal-flag branches everywhere.
    pruned_main = _prune_branches(program.main)
    pruned_defs = [
        Definition(
            name=d.name,
            body=_prune_branches(d.body),
            src_loc=d.src_loc,
            declared_effect=d.declared_effect,
        )
        for d in program.definitions
    ]

    pruned = Program(definitions=pruned_defs, main=pruned_main)

    # (b) Drop unreachable definitions.
    reachable = _reachable_definitions(pruned)
    kept_defs = [d for d in pruned_defs if d.name.lower() in reachable]
    removed_names = {
        d.name.lower() for d in pruned_defs if d.name.lower() not in reachable
    }

    if dictionary is not None:
        for name in removed_names:
            # Only evict if the dictionary entry is THIS dead definition,
            # not a builtin/variable that happens to share the name.
            entry = dictionary.lookup(name)
            if isinstance(entry, Definition):
                dictionary._entries.pop(name, None)  # noqa: SLF001

    return Program(definitions=kept_defs, main=pruned_main)


__all__ = ["dead_code_eliminate"]
