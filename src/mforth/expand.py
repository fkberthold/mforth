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

from typing import TYPE_CHECKING, Optional

from mforth.parse import (
    Begin,
    Definition,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    LitStr,
    Program,
    SrcLoc,
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


class CellBoundaryError(Exception):
    """The D5 cell-free-boundary compile error (bead mforth-7h1.2 / INVARIANT C).

    Raised when a ``CREATE … DOES>`` defining word's ``DOES>`` body cannot be
    reduced to a *cell-free* form — i.e. it needs a mutable / addressable /
    runtime-indexed memory cell, which mforth v1's cell-free strategy forbids.
    The message NAMES the offending defining word (and the child being
    stamped) and cites the cell-free boundary, modelled on
    :class:`PurityError`. This is a clean compile error, NEVER a miscompile
    and NEVER a silent pass.
    """


# ===========================================================================
# CREATE / , / DOES> defining-word STAMPER (bead mforth-7h1.2)
#
# A ``:`` definition whose body has the shape ``CREATE <create-phase> DOES>
# <does-body>`` is a *defining word*. Calling it (``76 CONSTANT TROMBONES``)
# runs the CREATE phase at compile time to build an immutable
# compile-time-constant *field* (the ``,`` ops consume preceding constants),
# then partial-evaluates + const-folds the DOES> body against that field. If
# the residual is cell-free (pure literals — no leftover field-address, no
# store, no runtime-indexed fetch) the child is registered as a stamped
# ``Macro`` (so B1's inliner lowers it to a literal push on both backends).
# Otherwise a :class:`CellBoundaryError` is raised naming the offending word.
#
# Two cooperating entry points:
#   * ``register_defining_words`` — called from ``resolve`` BEFORE its
#     existence check. Detects defining words, stamps each child into a
#     ``Macro`` in the dictionary (raising CellBoundaryError as needed) and
#     returns the set of names ``resolve`` must tolerate (the defining-word
#     names + the meta-words CREATE/,/DOES>) so the check passes.
#   * the new clause inside ``expand`` — drops the (now compile-time-consumed)
#     defining-word definitions and child-invocation term sequences from the
#     program, leaving only the child Macro *references*, which the B1 inliner
#     turns into literal pushes.
# ===========================================================================


# The reserved meta-words of the CREATE/,/DOES> surface. They only ever appear
# inside a defining word's body; they never reach stackcheck or a backend.
_CREATE = "create"
_COMMA = ","
_DOES = "does>"


class _DefiningWord:
    """A detected ``CREATE … DOES>`` defining word.

    ``create_phase`` is the term list between ``CREATE`` and ``DOES>`` (for the
    textbook ``CONSTANT`` it is a single ``,``); ``does_body`` is the term list
    after ``DOES>`` (the child-behaviour template).
    """

    __slots__ = ("name", "create_phase", "does_body", "src_loc")

    def __init__(self, name, create_phase, does_body, src_loc) -> None:
        self.name = name
        self.create_phase = create_phase
        self.does_body = does_body
        self.src_loc = src_loc


def _is_word(term, name: str) -> bool:
    return isinstance(term, WordCall) and term.name.lower() == name


def _split_defining_word(defn: "Definition") -> Optional[_DefiningWord]:
    """If ``defn``'s body is a ``CREATE … DOES> …`` defining word, return the
    split :class:`_DefiningWord`; otherwise return ``None``.

    The shape recognised is: a ``CREATE`` word somewhere in the body, followed
    (later) by exactly one ``DOES>`` word. The create-phase is everything
    strictly between them; the does-body is everything after ``DOES>``.
    """
    body = defn.body
    create_idx = next(
        (i for i, t in enumerate(body) if _is_word(t, _CREATE)), None
    )
    does_idx = next(
        (i for i, t in enumerate(body) if _is_word(t, _DOES)), None
    )
    if create_idx is None or does_idx is None or does_idx <= create_idx:
        return None
    create_phase = body[create_idx + 1 : does_idx]
    does_body = body[does_idx + 1 :]
    return _DefiningWord(
        name=defn.name,
        create_phase=create_phase,
        does_body=does_body,
        src_loc=defn.src_loc,
    )


def find_defining_words(program: Program) -> "dict[str, _DefiningWord]":
    """Return ``{lowercased-name: _DefiningWord}`` for every ``CREATE … DOES>``
    defining word declared in ``program``."""
    found: dict[str, _DefiningWord] = {}
    for defn in program.definitions:
        dw = _split_defining_word(defn)
        if dw is not None:
            found[defn.name.lower()] = dw
    return found


# --- compile-time const values + the immutable field --------------------------


def _literal_value(term):
    """If ``term`` is a literal push, return its Python value; else ``None``.

    A sentinel is needed to distinguish "not a literal" from a literal whose
    value is falsy (``0``), so callers test identity against
    :data:`_NOT_LITERAL`.
    """
    if isinstance(term, (LitInt, LitFloat, LitStr)):
        return term.value
    return _NOT_LITERAL


_NOT_LITERAL = object()


def _value_to_term(value, src_loc: SrcLoc):
    """Reconstruct a literal AST node for ``value`` (type chosen by Python
    type, mirroring :mod:`mforth.fold` so the pushed runtime type matches)."""
    if isinstance(value, bool):
        return LitInt(value=int(value), src_loc=src_loc)
    if isinstance(value, int):
        return LitInt(value=value, src_loc=src_loc)
    if isinstance(value, float):
        return LitFloat(value=value, src_loc=src_loc)
    if isinstance(value, str):
        return LitStr(value=value, src_loc=src_loc)
    raise TypeError(f"non-literal stamped value {value!r}")


def _count_create_field_slots(dw: _DefiningWord, child_name: str) -> int:
    """How many compile-time-constant values the CREATE phase consumes — one
    per ``,`` op. Any non-``,`` word in the create-phase is unsupported in v1.
    """
    slots = 0
    for term in dw.create_phase:
        if _is_word(term, _COMMA):
            slots += 1
        else:
            raise CellBoundaryError(
                f"defining word '{dw.name}' (stamping child '{child_name}') "
                f"has an unsupported CREATE phase: only ',' is supported in v1 "
                f"(crossed the cell-free boundary)"
            )
    return slots


# --- symbolic partial-evaluation of the DOES> body ----------------------------
#
# The DOES> body is analysed as a child-behaviour template. The field base
# address is auto-pushed on top of the child's (symbolic) runtime inputs. We
# model three symbolic value kinds:
#
#   _Const(value)   — a compile-time constant.
#   _FieldAddr(off) — the immutable field base + a *constant* offset.
#   _RUNTIME        — an unknown runtime value (a child input or a value
#                     derived from one); requesting more operands than the
#                     symbolic stack holds yields _RUNTIME ("from below").
#
# Reductions:
#   @  : fetch — _FieldAddr(k) with k a valid index → _Const(field[k]);
#        _RUNTIME or an out-of-range / non-constant address → CellBoundaryError
#        (runtime or runtime-indexed fetch).
#   !  : store — always CellBoundaryError (mutable cell).
#   +/-/…: pure arithmetic — _Const⊕_Const → fold; _FieldAddr(k)+_Const(c) →
#        _FieldAddr(k+c) (static address arithmetic); anything touching
#        _RUNTIME → _RUNTIME.
#   stack ops: shuffle the symbolic stack.
#
# Cell-free SUCCESS ⇔ no store, no runtime fetch, and the residual stack is
# all _Const (a child that leaves a bare _FieldAddr, or a _RUNTIME it cannot
# reduce to a constant, has crossed the boundary).


class _Const:
    __slots__ = ("value",)

    def __init__(self, value) -> None:
        self.value = value


class _FieldAddr:
    __slots__ = ("offset",)

    def __init__(self, offset: int) -> None:
        self.offset = offset


_RUNTIME = object()


# Pure numeric evaluators — mirror mforth.fold / mforth.backend.primitives
# EXACTLY (float `/`, 0/1 comparison encoding, int&int bitwise).
import math as _math


def _eval_pure(name: str, args: list):
    a = args[0]
    if len(args) == 2:
        b = args[1]
        if name == "+":
            return a + b
        if name == "-":
            return a - b
        if name == "*":
            return a * b
        if name == "/":
            if b == 0:
                return _math.nan if a == 0 else (_math.inf if a > 0 else -_math.inf)
            return a / b
        if name == "MOD":
            return _math.nan if b == 0 else a % b
        if name == "=":
            return 1 if a == b else 0
        if name == "<>":
            return 1 if a != b else 0
        if name == "<":
            return 1 if a < b else 0
        if name == ">":
            return 1 if a > b else 0
        if name == "<=":
            return 1 if a <= b else 0
        if name == ">=":
            return 1 if a >= b else 0
        if name == "AND":
            return int(a) & int(b)
        if name == "OR":
            return int(a) | int(b)
    elif name == "NOT":
        return 0 if a else 1
    raise KeyError(name)


# (upper-name → consumed arity) for the pure arithmetic/logical/comparison set.
_PURE_ARITH_ARITY = {
    "+": 2, "-": 2, "*": 2, "/": 2, "MOD": 2,
    "=": 2, "<>": 2, "<": 2, ">": 2, "<=": 2, ">=": 2,
    "AND": 2, "OR": 2, "NOT": 1,
}


def _pop(stack: list):
    """Pop one symbolic value; underflow yields a fresh _RUNTIME ("from below"
    = an unknown child input)."""
    if stack:
        return stack.pop()
    return _RUNTIME


def _stamp_child(
    dw: _DefiningWord,
    child_name: str,
    field: "list",
    field_src: SrcLoc,
) -> "list":
    """Partial-evaluate ``dw.does_body`` against the immutable ``field`` and
    return the reduced cell-free body (a list of literal Terms). Raises
    :class:`CellBoundaryError` if the body crosses the cell-free boundary.
    """

    def boundary(reason: str) -> "CellBoundaryError":
        return CellBoundaryError(
            f"defining word '{dw.name}' (child '{child_name}') DOES> body "
            f"{reason} — crosses the cell-free boundary (v1 is cell-free; "
            f"design D5)"
        )

    sym: list = [_FieldAddr(0)]  # the auto-pushed field base address
    for term in dw.does_body:
        val = _literal_value(term)
        if val is not _NOT_LITERAL:
            sym.append(_Const(val))
            continue

        if isinstance(term, WordCall):
            upper = term.name.upper()
            lower = term.name.lower()

            if lower == "@":  # fetch
                top = _pop(sym)
                if isinstance(top, _FieldAddr) and 0 <= top.offset < len(field):
                    sym.append(_Const(field[top.offset]))
                    continue
                raise boundary("performs a runtime / runtime-indexed '@' fetch")

            if lower == "!":  # store
                raise boundary("performs a '!' store (needs a mutable cell)")

            arity = _PURE_ARITH_ARITY.get(upper)
            if arity is not None:
                operands = [_pop(sym) for _ in range(arity)][::-1]
                if all(isinstance(o, _Const) for o in operands):
                    result = _eval_pure(upper, [o.value for o in operands])
                    if isinstance(result, bool):
                        result = int(result)
                    sym.append(_Const(result))
                    continue
                # FieldAddr + constant offset stays a static address.
                if (
                    upper in ("+", "-")
                    and arity == 2
                    and isinstance(operands[0], _FieldAddr)
                    and isinstance(operands[1], _Const)
                    and isinstance(operands[1].value, int)
                ):
                    delta = operands[1].value
                    off = operands[0].offset + (delta if upper == "+" else -delta)
                    sym.append(_FieldAddr(off))
                    continue
                # Any operand touches a _RUNTIME or a non-static address →
                # the result is runtime.
                sym.append(_RUNTIME)
                continue

            # Stack-shuffle words operate on the symbolic stack directly.
            if upper in _STACK_SHUFFLE:
                _STACK_SHUFFLE[upper](sym)
                continue

            # Any other word inside a DOES> body is not cell-free in v1
            # (world sinks / @-identifiers / user calls / VARIABLE etc.).
            raise boundary(f"calls non-cell-free word '{term.name}'")

        # Control-flow / VarRef inside a DOES> body — unsupported in v1.
        raise boundary(
            f"contains an unsupported construct ({type(term).__name__})"
        )

    # Cell-free SUCCESS requires an all-_Const residual.
    if any(isinstance(v, _FieldAddr) for v in sym):
        raise boundary("leaves a bare field address on the stack")
    if not all(isinstance(v, _Const) for v in sym):
        raise boundary("does not reduce to a compile-time constant")

    return [_value_to_term(v.value, field_src) for v in sym]


def _shuffle_dup(s: list) -> None:
    s.append(s[-1] if s else _RUNTIME)


def _shuffle_drop(s: list) -> None:
    _pop(s)


def _shuffle_swap(s: list) -> None:
    b = _pop(s)
    a = _pop(s)
    s.extend([b, a])


def _shuffle_over(s: list) -> None:
    b = _pop(s)
    a = _pop(s)
    s.extend([a, b, a])


def _shuffle_rot(s: list) -> None:
    c = _pop(s)
    b = _pop(s)
    a = _pop(s)
    s.extend([b, c, a])


def _shuffle_nip(s: list) -> None:
    b = _pop(s)
    _pop(s)
    s.append(b)


def _shuffle_tuck(s: list) -> None:
    b = _pop(s)
    a = _pop(s)
    s.extend([b, a, b])


_STACK_SHUFFLE = {
    "DUP": _shuffle_dup,
    "DROP": _shuffle_drop,
    "SWAP": _shuffle_swap,
    "OVER": _shuffle_over,
    "ROT": _shuffle_rot,
    "NIP": _shuffle_nip,
    "TUCK": _shuffle_tuck,
}


# --- walking term lists for invocations ---------------------------------------


def _stamp_invocations_in_terms(
    terms: list,
    dictionary: "Dictionary",
    defining_words: "dict[str, _DefiningWord]",
    stamped_children: "set[str]",
) -> None:
    """Scan ``terms`` for ``<const-args> DEFWORD CHILD`` invocation sequences;
    for each, run the CREATE phase + stamp the child into a ``Macro`` in
    ``dictionary`` and record the child name in ``stamped_children``. Recurses
    into control-flow bodies. Raises :class:`CellBoundaryError` on a
    boundary-crossing child.
    """
    from mforth.dictionary import Macro

    i = 0
    n = len(terms)
    while i < n:
        term = terms[i]
        if isinstance(term, WordCall) and term.name.lower() in defining_words:
            dw = defining_words[term.name.lower()]
            # The child name is the WordCall immediately after the defword.
            if i + 1 >= n or not isinstance(terms[i + 1], WordCall):
                raise CellBoundaryError(
                    f"defining word '{dw.name}' is not followed by a child "
                    f"name to define"
                )
            child = terms[i + 1]
            child_name = child.name
            slots = _count_create_field_slots(dw, child_name)
            # Gather the `slots` preceding literal args (in stack order).
            field: list = []
            arg_src = term.src_loc
            for k in range(slots):
                idx = i - slots + k
                if idx < 0:
                    raise CellBoundaryError(
                        f"defining word '{dw.name}' (child '{child_name}') "
                        f"needs {slots} compile-time-constant argument(s) but "
                        f"too few precede it"
                    )
                arg = terms[idx]
                val = _literal_value(arg)
                if val is _NOT_LITERAL:
                    raise CellBoundaryError(
                        f"defining word '{dw.name}' (child '{child_name}') "
                        f"argument is not a compile-time constant — crosses "
                        f"the cell-free boundary"
                    )
                field.append(val)
                arg_src = arg.src_loc
            body = _stamp_child(dw, child_name, field, arg_src)
            # Register the stamped child as a Macro (its body is the cell-free
            # literal-push terms). A later child of the same name redefines it
            # (Forth semantics).
            dictionary._entries[child_name.lower()] = Macro(  # noqa: SLF001
                name=child_name, body=body
            )
            stamped_children.add(child_name.lower())
            i += 1
            continue

        if isinstance(term, IfThen):
            _stamp_invocations_in_terms(
                term.then_body, dictionary, defining_words, stamped_children
            )
            _stamp_invocations_in_terms(
                term.else_body, dictionary, defining_words, stamped_children
            )
        elif isinstance(term, Begin):
            _stamp_invocations_in_terms(
                term.body, dictionary, defining_words, stamped_children
            )
            _stamp_invocations_in_terms(
                term.cond_body, dictionary, defining_words, stamped_children
            )
        elif isinstance(term, DoLoop):
            _stamp_invocations_in_terms(
                term.body, dictionary, defining_words, stamped_children
            )
        i += 1


def register_defining_words(
    program: Program, dictionary: "Dictionary"
) -> "set[str]":
    """Detect defining words, stamp each child invocation into a ``Macro`` in
    ``dictionary``, and return the set of names ``resolve``'s existence check
    must tolerate.

    Called from :func:`mforth.dictionary.resolve` BEFORE its WordCall
    existence check, so the stamped child references resolve to ``Macro``
    entries and the meta-words ``CREATE`` / ``,`` / ``DOES>`` (which only ever
    live inside a defining word's body) are skipped. Raises
    :class:`CellBoundaryError` on a boundary-crossing child (D5).

    The set of stamped child names is recorded on the dictionary
    (``_stamped_children``) so :func:`strip_defining_words_in_place` can inline
    those child *references* to their literal bodies — needed by any consumer
    that runs ``stackcheck``/``emit`` directly off ``resolve`` without
    re-threading through ``expand`` (e.g. the equivalence harness).
    """
    defining_words = find_defining_words(program)
    if not defining_words:
        return set()

    stamped_children: set[str] = set()
    # Stamp every invocation reachable from main and from non-defining-word
    # definition bodies (a defining word may be used inside another `:` body).
    _stamp_invocations_in_terms(
        program.main, dictionary, defining_words, stamped_children
    )
    for defn in program.definitions:
        if defn.name.lower() in defining_words:
            continue  # the defining word's own body is meta — skip it
        _stamp_invocations_in_terms(
            defn.body, dictionary, defining_words, stamped_children
        )

    # Stash the stamped-child names for the in-place strip+inline.
    dictionary._stamped_children = stamped_children  # noqa: SLF001

    tolerated: set[str] = {_CREATE, _COMMA, _DOES}
    tolerated.update(defining_words.keys())
    return tolerated


# --- the expand-time program transform ----------------------------------------


def _strip_invocations_in_terms(
    terms: list,
    defining_words: "dict[str, _DefiningWord]",
    dictionary: "Dictionary",
    stamped_children: "set[str]",
) -> list:
    """Return a copy of ``terms`` with (a) each ``<const-args> DEFWORD CHILD``
    invocation sequence removed (it was fully consumed at compile time) and
    (b) every stamped-child *reference* replaced by its cell-free literal body.

    Inlining the child references here (rather than relying solely on the
    general macro inliner in :func:`expand`) means a consumer that runs
    ``stackcheck``/``emit`` straight off ``resolve`` — without re-threading the
    program through ``expand`` (the equivalence harness) — also sees only
    literals, so the REPL ↔ mlog property holds on both paths."""
    out: list = []
    i = 0
    n = len(terms)
    while i < n:
        term = terms[i]
        if isinstance(term, WordCall) and term.name.lower() in defining_words:
            dw = defining_words[term.name.lower()]
            slots = sum(1 for t in dw.create_phase if _is_word(t, _COMMA))
            # Drop the `slots` preceding literal args we already appended.
            for _ in range(slots):
                if out:
                    out.pop()
            # Skip the defword call and the following child-name term.
            i += 2
            continue

        if (
            isinstance(term, WordCall)
            and term.name.lower() in stamped_children
        ):
            # A *use* of a stamped child → inline its cell-free literal body.
            from mforth.dictionary import Macro

            entry = dictionary.lookup(term.name)
            if isinstance(entry, Macro):
                out.extend(entry.body)
                i += 1
                continue

        if isinstance(term, IfThen):
            out.append(
                IfThen(
                    then_body=_strip_invocations_in_terms(
                        term.then_body, defining_words, dictionary, stamped_children
                    ),
                    else_body=_strip_invocations_in_terms(
                        term.else_body, defining_words, dictionary, stamped_children
                    ),
                    src_loc=term.src_loc,
                )
            )
        elif isinstance(term, Begin):
            out.append(
                Begin(
                    body=_strip_invocations_in_terms(
                        term.body, defining_words, dictionary, stamped_children
                    ),
                    kind=term.kind,
                    cond_body=_strip_invocations_in_terms(
                        term.cond_body, defining_words, dictionary, stamped_children
                    ),
                    src_loc=term.src_loc,
                )
            )
        elif isinstance(term, DoLoop):
            out.append(
                DoLoop(
                    body=_strip_invocations_in_terms(
                        term.body, defining_words, dictionary, stamped_children
                    ),
                    src_loc=term.src_loc,
                )
            )
        else:
            out.append(term)
        i += 1
    return out


def strip_defining_words_in_place(
    program: Program, dictionary: "Dictionary"
) -> None:
    """Mutate ``program`` IN PLACE: drop every defining-word definition, strip
    every child-invocation sequence, and inline every stamped-child reference
    to its cell-free literal body. The stamped child ``Macro``s + the set of
    stamped child names were recorded on ``dictionary`` by
    :func:`register_defining_words`.

    Done in place (rather than returning a new ``Program``) so that callers who
    hold the program by reference and run their OWN pipeline after ``resolve``
    (e.g. the equivalence harness ``parse → resolve → stackcheck → emit``, which
    does not re-thread the program through ``expand``) see the transformed
    program at ``stackcheck`` time. ``CREATE`` / ``,`` / ``DOES>`` and the
    stamped child Macros therefore never reach stackcheck or a backend.
    Idempotent: a second call is a no-op (the defining words are already
    gone)."""
    defining_words = find_defining_words(program)
    if not defining_words:
        return

    stamped_children: set[str] = getattr(dictionary, "_stamped_children", set())

    new_defs = [
        Definition(
            name=d.name,
            body=_strip_invocations_in_terms(
                d.body, defining_words, dictionary, stamped_children
            ),
            src_loc=d.src_loc,
            declared_effect=d.declared_effect,
        )
        for d in program.definitions
        if d.name.lower() not in defining_words
    ]
    program.definitions[:] = new_defs
    program.main[:] = _strip_invocations_in_terms(
        program.main, defining_words, dictionary, stamped_children
    )


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

    # Phase 0a — CREATE/,/DOES> defining words (bead mforth-7h1.2). Drop the
    # defining-word definitions and the (compile-time-consumed) child
    # invocation sequences; the stamped child Macros were already registered
    # into the dictionary by ``register_defining_words`` (called from resolve),
    # so the surviving child references are lowered by the macro inliner below.
    # ``resolve`` already performed this strip in place — this call is the
    # idempotent safety net for callers that hand ``expand`` an un-stripped
    # program directly.
    strip_defining_words_in_place(program, dictionary)

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
    "CellBoundaryError",
    "ExpandError",
    "PurityError",
    "expand",
    "find_defining_words",
    "register_defining_words",
    "strip_defining_words_in_place",
]
