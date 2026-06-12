"""Constant folding — a v2 pre-codegen AST optimization pass (bead mforth-10t.35).

Runs post-stackcheck, pre-slot-alloc on a :class:`~mforth.parse.Program`
(``definitions`` + ``main``). The pass walks each term sequence maintaining a
*symbolic constant stack*: a run of literal pushes (``LitInt`` / ``LitFloat`` /
``LitStr``) that have not yet been consumed by a side-effect. When the next
``Term`` is a **pure** word (arithmetic, comparison, bitwise/logical, or a
stack op) AND the symbolic stack holds enough constants of the right kind, the
operation is evaluated at compile time and the consumed literals + word are
replaced by a single literal node. The result is fewer instructions emitted
AND fewer executed (fast > small — net win on both axes).

Anything that is NOT a pure word over constants *breaks the chain*: the
pending symbolic constants are flushed back out as literal terms (in stack
order) and the breaking term is emitted unchanged. Chain breakers are:

* side-effectful / IO words (``. PRINT PRINTFLUSH WAIT SENSOR GETLINK``,
  the ``CONTROL-*`` family, ``@`` ``!`` ``VARIABLE``);
* the Mindustry ``@``-identifier surface (magic vars / content tags) and
  ``NULL`` — these push runtime/opaque handles, not foldable literals;
* user-``Definition`` calls (opaque effect — we never inline-then-fold in v1);
* control-flow nodes (``IfThen`` / ``Begin`` / ``DoLoop``) and ``VarRef`` —
  the pass recurses INTO their bodies but never folds a constant across the
  boundary;
* a pure word whose operands are not all currently constant (it consumes a
  runtime value), or whose constant operands are the wrong type (e.g. a
  string into ``+``).

Faithfulness contract (CLAUDE.md headline rule — REPL ↔ mlog equivalence):
the folded value's *Python type* must match what the host primitive would have
pushed, because the runtime type is observable (``/`` yields a float; ``PRINT``
strips a trailing ``.0`` but ``=`` does not). So ``int``-valued results become
``LitInt`` and ``float``-valued results become ``LitFloat``, exactly mirroring
:mod:`mforth.backend.primitives`. The folding evaluators below are literally
the same arithmetic those primitives run.

DEAD CODE until bead mforth-10t.40 wires ``-O`` levels — this module is not
imported by the default pipeline.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Optional

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
    Term,
    VarRef,
    WordCall,
)


# ---------------------------------------------------------------------------
# Symbolic constant entry
# ---------------------------------------------------------------------------


class _Const:
    """A pending compile-time constant on the symbolic stack.

    ``value`` is the Python runtime value (int / float / str) the literal
    pushes. ``src_loc`` is preserved from the originating literal so a
    flushed-back or folded literal keeps a sensible source location.

    ``numeric`` distinguishes foldable-into-arithmetic values (int/float)
    from string handles (only valid for stack ops).
    """

    __slots__ = ("value", "src_loc", "numeric")

    def __init__(self, value, src_loc: SrcLoc, numeric: bool) -> None:
        self.value = value
        self.src_loc = src_loc
        self.numeric = numeric

    def to_term(self) -> Term:
        """Reconstruct the AST literal node for this constant.

        The node TYPE is chosen by the value's Python type so the pushed
        runtime type matches the un-folded program (load-bearing for
        equivalence — see module docstring).
        """
        v = self.value
        if isinstance(v, str):
            return LitStr(value=v, src_loc=self.src_loc)
        if isinstance(v, bool):
            # Python bool subclasses int; mforth has no bool literals, and
            # no primitive produces one (comparisons yield int 0/1). Coerce
            # to int defensively so we never emit a bool.
            return LitInt(value=int(v), src_loc=self.src_loc)
        if isinstance(v, int):
            return LitInt(value=v, src_loc=self.src_loc)
        if isinstance(v, float):
            return LitFloat(value=v, src_loc=self.src_loc)
        raise TypeError(f"non-foldable constant value {v!r} ({type(v).__name__})")


def _const_for_literal(term: Term) -> Optional[_Const]:
    """If ``term`` is a literal push, return its :class:`_Const`; else None."""
    if isinstance(term, LitInt):
        return _Const(term.value, term.src_loc, numeric=True)
    if isinstance(term, LitFloat):
        return _Const(term.value, term.src_loc, numeric=True)
    if isinstance(term, LitStr):
        return _Const(term.value, term.src_loc, numeric=False)
    return None


# ---------------------------------------------------------------------------
# Pure-word evaluators
#
# These mirror mforth.backend.primitives EXACTLY (mlog 0/1 comparison
# encoding, int()&int() for bitwise, float `/`, inf/nan on divide-by-zero).
# A divergence here would falsify the headline REPL ↔ mlog equivalence
# property the moment a folded program is run.
# ---------------------------------------------------------------------------


def _binary(fn):
    """Wrap a 2-arg evaluator; arity = (2 consumed, 1 produced)."""
    return (2, 1, fn)


def _unary(fn):
    return (1, 1, fn)


def _add(a, b):
    return a + b


def _sub(a, b):
    return a - b


def _mul(a, b):
    return a * b


def _div(a, b):
    if b == 0:
        if a == 0:
            return math.nan
        return math.inf if a > 0 else -math.inf
    return a / b


def _mod(a, b):
    if b == 0:
        return math.nan
    return a % b


def _eq(a, b):
    return 1 if a == b else 0


def _ne(a, b):
    return 1 if a != b else 0


def _lt(a, b):
    return 1 if a < b else 0


def _gt(a, b):
    return 1 if a > b else 0


def _le(a, b):
    return 1 if a <= b else 0


def _ge(a, b):
    return 1 if a >= b else 0


def _and(a, b):
    return int(a) & int(b)


def _or(a, b):
    return int(a) | int(b)


def _not(a):
    return 0 if a else 1


# name (upper) -> (in_arity, out_arity, evaluator). All require NUMERIC
# operands. Evaluators take popped operands in Forth order (a then b for
# binary, where b was on top of the stack).
_ARITH_WORDS: dict[str, tuple] = {
    "+": _binary(_add),
    "-": _binary(_sub),
    "*": _binary(_mul),
    "/": _binary(_div),
    "MOD": _binary(_mod),
    "=": _binary(_eq),
    "<>": _binary(_ne),
    "<": _binary(_lt),
    ">": _binary(_gt),
    "<=": _binary(_le),
    ">=": _binary(_ge),
    "AND": _binary(_and),
    "OR": _binary(_or),
    "NOT": _unary(_not),
}


# ---------------------------------------------------------------------------
# Stack ops — operate on the symbolic constant stack directly. Each takes the
# current list of _Const (top = last) and returns a NEW list, or None if the
# op cannot be performed purely (not enough constants).
# ---------------------------------------------------------------------------


def _stack_dup(stack: list) -> Optional[list]:
    if len(stack) < 1:
        return None
    top = stack[-1]
    return stack + [_Const(top.value, top.src_loc, top.numeric)]


def _stack_drop(stack: list) -> Optional[list]:
    if len(stack) < 1:
        return None
    return stack[:-1]


def _stack_swap(stack: list) -> Optional[list]:
    if len(stack) < 2:
        return None
    new = list(stack)
    new[-1], new[-2] = new[-2], new[-1]
    return new


def _stack_over(stack: list) -> Optional[list]:
    if len(stack) < 2:
        return None
    second = stack[-2]
    return stack + [_Const(second.value, second.src_loc, second.numeric)]


def _stack_rot(stack: list) -> Optional[list]:
    # ( a b c -- b c a )
    if len(stack) < 3:
        return None
    new = list(stack)
    c = new.pop()
    b = new.pop()
    a = new.pop()
    new.extend([b, c, a])
    return new


def _stack_nip(stack: list) -> Optional[list]:
    # ( a b -- b )
    if len(stack) < 2:
        return None
    new = list(stack)
    top = new.pop()
    new.pop()  # discard second
    new.append(top)
    return new


def _stack_tuck(stack: list) -> Optional[list]:
    # ( a b -- b a b )
    if len(stack) < 2:
        return None
    new = list(stack)
    b = new.pop()
    a = new.pop()
    new.extend([
        _Const(b.value, b.src_loc, b.numeric),
        a,
        b,
    ])
    return new


_STACK_WORDS = {
    "DUP": _stack_dup,
    "DROP": _stack_drop,
    "SWAP": _stack_swap,
    "OVER": _stack_over,
    "ROT": _stack_rot,
    "NIP": _stack_nip,
    "TUCK": _stack_tuck,
}


# ---------------------------------------------------------------------------
# Core fold over a single term list
# ---------------------------------------------------------------------------


def _fold_terms(terms: list) -> list:
    """Constant-fold a single term sequence.

    Returns a new list of terms; nested control-flow bodies are folded
    recursively but the constant stack never crosses a control-flow,
    side-effect, or opaque-call boundary.
    """
    out: list = []
    sym: list = []  # symbolic constant stack of _Const

    def flush() -> None:
        # Emit pending constants in stack order (bottom → top) and clear.
        for c in sym:
            out.append(c.to_term())
        sym.clear()

    for term in terms:
        const = _const_for_literal(term)
        if const is not None:
            sym.append(const)
            continue

        if isinstance(term, WordCall):
            name = term.name.upper()

            # Stack op over the symbolic constant stack?
            stack_fn = _STACK_WORDS.get(name)
            if stack_fn is not None:
                new_sym = stack_fn(sym)
                if new_sym is not None:
                    sym[:] = new_sym
                    continue
                # Not enough constants — the op consumes a runtime value.
                flush()
                out.append(term)
                continue

            # Arithmetic / comparison / logical over numeric constants?
            arith = _ARITH_WORDS.get(name)
            if arith is not None:
                in_arity, _out_arity, fn = arith
                if len(sym) >= in_arity and all(
                    c.numeric for c in sym[-in_arity:]
                ):
                    operands = [c.value for c in sym[-in_arity:]]
                    result = fn(*operands)
                    # Re-coerce a bool result to int (defensive; evaluators
                    # already return ints for comparisons).
                    if isinstance(result, bool):
                        result = int(result)
                    loc = sym[-in_arity].src_loc
                    del sym[-in_arity:]
                    sym.append(_Const(result, loc, numeric=True))
                    continue
                # Operands not all constant — break the chain.
                flush()
                out.append(term)
                continue

            # Any other word (side-effect, IO, @-identifier, NULL, user def,
            # VARIABLE, @, !) breaks the chain.
            flush()
            out.append(term)
            continue

        # Non-WordCall, non-literal term: control flow / VarRef. Recurse into
        # control-flow bodies; never fold across the boundary.
        flush()
        if isinstance(term, IfThen):
            out.append(
                replace(
                    term,
                    then_body=_fold_terms(term.then_body),
                    else_body=_fold_terms(term.else_body),
                )
            )
        elif isinstance(term, Begin):
            out.append(
                replace(
                    term,
                    body=_fold_terms(term.body),
                    cond_body=_fold_terms(term.cond_body),
                )
            )
        elif isinstance(term, DoLoop):
            out.append(replace(term, body=_fold_terms(term.body)))
        else:
            # VarRef or any future Term type — emit unchanged.
            out.append(term)

    flush()
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fold_constants(program: Program) -> Program:
    """Return a NEW :class:`Program` with constant-foldable sequences folded.

    The input program is not mutated (a fresh ``Program`` with folded term
    lists is returned). ``Definition`` metadata (name, src_loc,
    declared_effect) is preserved; only the body is folded.

    Folding is purely local to each term sequence — it does not inline user
    definitions, so a call to a user word always breaks the constant chain.
    The resulting AST is stack-valid (folding is stack-effect-preserving by
    construction) and ready for the slot allocator.
    """
    new_defs = [
        replace(defn, body=_fold_terms(defn.body))
        for defn in program.definitions
    ]
    new_main = _fold_terms(program.main)
    return Program(definitions=new_defs, main=new_main)


__all__ = ["fold_constants"]
