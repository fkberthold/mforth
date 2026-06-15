"""Phase-0 expand-then-check pass for the mforth compiler (bead mforth-7h1.1).

``expand(program, dictionary)`` is the single deterministic function both
front-ends (mlog backend and host/REPL runner) invoke BETWEEN ``resolve``
and ``stackcheck``. It replaces every ``WordCall`` whose dictionary entry is
a ``Macro`` with the macro's body terms, recursively to a fixpoint, so that
stackcheck and both backends only ever see a fully-expanded AST containing
ZERO meta-words (Invariant 1).

Two invariants enforced here:

INVARIANT 1 — Zero meta-words survive expansion. After ``expand`` returns,
no ``WordCall`` anywhere in the program (main, definition bodies, or
control-flow bodies) resolves to a ``Macro``.

INVARIANT 2 — Purity. A macro body that calls a world-sink primitive (tag
``"mindustry"`` or ``"mindustry-control"``) or reads runtime state (a
``UserVariable`` fetch via ``@``) raises ``PurityError`` before the
program is accepted. The check is tag-driven, not name-driven, so a novel
world-sink registered under any name is caught automatically.

Cycle detection: a direct self-cycle, a mutual cycle (A→B→A), or a cycle
reachable through a control-flow body all raise ``ExpandError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    Program,
    WordCall,
)

if TYPE_CHECKING:
    from mforth.dictionary import Dictionary


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExpandError(Exception):
    """Raised when macro expansion does not terminate (cycle detected)."""


class PurityError(Exception):
    """Raised when a macro body (transitively) calls a world-sink or reads
    runtime state. The message names the offending primitive."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expand_terms(
    terms: list,
    dictionary: "Dictionary",
    expanding: frozenset[str],
) -> list:
    """Expand ``terms`` recursively, returning a new flat list.

    ``expanding`` is the set of macro names currently on the expansion
    stack (for cycle detection). It is passed by value (frozenset) so
    each branch of a control-flow node gets its own scope.
    """
    from mforth.dictionary import Macro

    result: list = []
    for term in terms:
        if isinstance(term, WordCall):
            entry = dictionary.lookup(term.name)
            if isinstance(entry, Macro):
                # Cycle check
                macro_key = entry.name.lower()
                if macro_key in expanding:
                    raise ExpandError(
                        f"cyclic macro expansion: {entry.name!r} refers to itself"
                    )
                # Purity check on the body before expanding into it
                _check_purity(entry.body, dictionary, entry.name)
                # Recurse into the macro body with this macro added to
                # the expanding set.
                expanded_body = _expand_terms(
                    entry.body,
                    dictionary,
                    expanding | {macro_key},
                )
                result.extend(expanded_body)
            else:
                result.append(term)
        elif isinstance(term, IfThen):
            new_then = _expand_terms(term.then_body, dictionary, expanding)
            new_else = _expand_terms(term.else_body, dictionary, expanding)
            # IfThen is a mutable dataclass — create a new one
            new_node = IfThen(
                then_body=new_then,
                else_body=new_else,
                src_loc=term.src_loc,
            )
            result.append(new_node)
        elif isinstance(term, Begin):
            new_body = _expand_terms(term.body, dictionary, expanding)
            new_cond = _expand_terms(term.cond_body, dictionary, expanding)
            new_node = Begin(
                body=new_body,
                kind=term.kind,
                cond_body=new_cond,
                src_loc=term.src_loc,
            )
            result.append(new_node)
        elif isinstance(term, DoLoop):
            new_body = _expand_terms(term.body, dictionary, expanding)
            new_node = DoLoop(body=new_body, src_loc=term.src_loc)
            result.append(new_node)
        else:
            result.append(term)
    return result


def _check_purity(
    body: list,
    dictionary: "Dictionary",
    macro_name: str,
) -> None:
    """Recursively check that ``body`` contains no world-sink calls or
    runtime-state reads. Raises ``PurityError`` if a violation is found.

    A world-sink call is a ``WordCall`` resolving to a ``BuiltinWord``
    with tag ``"mindustry"`` or ``"mindustry-control"``.

    A runtime-state read is a ``@`` (fetch) applied to a ``UserVariable``
    — the ``x @`` pattern, where the variable name precedes the fetch.
    We flag the ``@`` when it immediately follows a ``UserVariable``
    reference. The walk descends into control-flow bodies and recurses
    into nested macros (transitive purity).
    """
    _check_purity_terms(body, dictionary, macro_name, set())


def _check_purity_terms(
    terms: list,
    dictionary: "Dictionary",
    macro_name: str,
    seen_macros: set[str],
) -> None:
    """Walk ``terms`` checking purity, descending into control-flow and
    recursing into nested macros.

    ``seen_macros`` guards against infinite recursion in cycles (which
    will be caught by ExpandError anyway, but we want purity check to
    short-circuit cleanly too).
    """
    from mforth.dictionary import Macro, UserVariable, BuiltinWord

    prev_is_user_var = False
    for term in terms:
        if isinstance(term, WordCall):
            entry = dictionary.lookup(term.name)
            if isinstance(entry, BuiltinWord):
                if entry.tag in {"mindustry", "mindustry-control"}:
                    raise PurityError(
                        f"macro {macro_name!r} calls world-sink primitive "
                        f"{entry.name!r} — macros must be pure (compile-time only)"
                    )
                if entry.tag == "var" and entry.name == "@" and prev_is_user_var:
                    # @ fetch of a user variable — runtime state read
                    raise PurityError(
                        f"macro {macro_name!r} reads runtime state via '@' fetch "
                        f"on a user variable — macros must be pure (compile-time only)"
                    )
            elif isinstance(entry, UserVariable):
                # A UserVariable in the body means it's going to be fetched
                # (the pattern is `x @`). Flag it: if the next word is @
                # it's a runtime read; even standing alone a UserVariable
                # reference makes no sense at compile-time without @.
                # We track this with prev_is_user_var flag and raise on @.
                prev_is_user_var = True
                continue
            elif isinstance(entry, Macro):
                # Recurse into the nested macro body (only if not already visited)
                key = entry.name.lower()
                if key not in seen_macros:
                    _check_purity_terms(
                        entry.body,
                        dictionary,
                        macro_name,
                        seen_macros | {key},
                    )
            prev_is_user_var = False
        elif isinstance(term, IfThen):
            _check_purity_terms(term.then_body, dictionary, macro_name, seen_macros)
            _check_purity_terms(term.else_body, dictionary, macro_name, seen_macros)
            prev_is_user_var = False
        elif isinstance(term, Begin):
            _check_purity_terms(term.body, dictionary, macro_name, seen_macros)
            _check_purity_terms(term.cond_body, dictionary, macro_name, seen_macros)
            prev_is_user_var = False
        elif isinstance(term, DoLoop):
            _check_purity_terms(term.body, dictionary, macro_name, seen_macros)
            prev_is_user_var = False
        else:
            prev_is_user_var = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def expand(program: Program, dictionary: "Dictionary") -> Program:
    """Expand all macro calls in ``program`` to a fixpoint.

    Walks ``program.main`` and each ``Definition.body``, replacing every
    ``WordCall`` that resolves to a ``Macro`` with the macro's body terms,
    recursively until no macro calls remain.

    Parameters
    ----------
    program
        The parsed program (post-parse, post-resolve).
    dictionary
        The populated dictionary (must already contain any seeded Macros).

    Returns
    -------
    Program
        A new ``Program`` with the same definitions (bodies expanded) and
        an expanded ``main``, containing ZERO macro-resolving ``WordCall``s.

    Raises
    ------
    ExpandError
        If a cycle is detected during expansion.
    PurityError
        If a macro body (directly or transitively) calls a world-sink or
        reads runtime state.
    """
    from mforth.parse import Definition

    # Expand main
    expanded_main = _expand_terms(program.main, dictionary, frozenset())

    # Expand each definition body
    expanded_defs = []
    for defn in program.definitions:
        expanded_body = _expand_terms(defn.body, dictionary, frozenset())
        new_defn = Definition(
            name=defn.name,
            body=expanded_body,
            src_loc=defn.src_loc,
            declared_effect=defn.declared_effect,
        )
        expanded_defs.append(new_defn)

    return Program(definitions=expanded_defs, main=expanded_main)


__all__ = [
    "ExpandError",
    "PurityError",
    "expand",
]
