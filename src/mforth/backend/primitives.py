"""mforth host REPL built-in primitives (bead mforth-10t.11).

Implements every built-in registered in `mforth.dictionary` (the A3
registry from bead mforth-10t.6) as a Python callable taking the
`Executor` and mutating its data stack / variables / world. The
`register_all(executor)` helper wires the entire table onto a single
executor instance.

This bead covers the arith / stack / logical / var / io families.
Mindustry-tagged primitives (PRINT, PRINTFLUSH, WAIT, SENSOR,
GETLINK) extend this same module via bead mforth-10t.12. I and J
(control tag) ship in the executor's default primitive set
(`backend.host._DEFAULT_PRIMITIVES`) because every DO/LOOP test in
.10 already depends on them; we do not override them here.

## Comparison-encoding contract (LOAD-BEARING)

Comparison primitives push **mlog's 0/1 encoding** (NOT Forth's
-1/0). This matches what bead mforth-10t.16 emits on the mlog side:
mlog's `op equal`, `op lessThan`, etc. all return 0.0 or 1.0. The
REPL ↔ mlog equivalence property (CLAUDE.md headline test class)
requires identical observable values across both backends — a
divergence here is the highest-severity regression class.

The bead's original text recommended Forth's -1/0; the dispatch
context for this bead explicitly overrode that recommendation to
keep equivalence with .16. Documented here so the override survives
future re-reads of the bead description.

Logical AND/OR/NOT operate on the 0/1 boolean encoding:

* `AND` and `OR` short-circuit nothing — they perform `int(a) & int(b)`
  and `int(a) | int(b)` on integer-coerced inputs. With 0/1 inputs
  this gives the standard truth table; matches mlog's `op and` /
  `op or` on the same encoding.
* `NOT` returns 0 for any non-zero input and 1 for zero. Matches
  mlog's `op not` when the input is constrained to 0/1.

## Division semantics

`/` is float division (mlog `op div`). `MOD` is `math.fmod`-like
modulo (mlog `op mod`). Division/modulo by zero must NOT raise — mlog
returns inf/nan and the REPL must mirror that to preserve
equivalence. We use `math.inf` and `math.nan` directly so the
behavior is deterministic across CPython versions.

## VARIABLE handling

`VARIABLE` is NOT in the primitive table — the executor (see
`backend.host.Executor._run_terms`) special-cases the literal
`WordCall("VARIABLE")` to consume the next term as the declared
name. Trying to register a `VARIABLE` callable would be unreachable.

`@` (fetch) and `!` (store) are in the table; they mirror the
`backend.host` defaults (the .10 ship registered minimal stubs to
get the executor's acceptance test green). `register_all` overrides
those defaults with the canonical implementations here so that
primitives.py is the single source of truth.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from mforth.backend.host import Executor


PrimitiveFn = Callable[["Executor"], None]


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def _plus(ex: "Executor") -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(a + b)


def _minus(ex: "Executor") -> None:
    # Forth convention: ( a b -- a-b )
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(a - b)


def _times(ex: "Executor") -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(a * b)


def _divide(ex: "Executor") -> None:
    """( a b -- a/b ) float division. b == 0 yields inf or nan — matches
    mlog's `op div` (no exception); preserves REPL ↔ mlog equivalence.
    """
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    if b == 0:
        if a == 0:
            ex.data_stack.append(math.nan)
        elif a > 0:
            ex.data_stack.append(math.inf)
        else:
            ex.data_stack.append(-math.inf)
        return
    ex.data_stack.append(a / b)


def _mod(ex: "Executor") -> None:
    """( a b -- a%b ) modulo. b == 0 yields nan — matches mlog's
    `op mod`; no exception.
    """
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    if b == 0:
        ex.data_stack.append(math.nan)
        return
    ex.data_stack.append(a % b)


# ---------------------------------------------------------------------------
# Comparison — mlog 0/1 encoding (NOT Forth -1/0)
# ---------------------------------------------------------------------------


def _eq(ex: "Executor") -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(1 if a == b else 0)


def _ne(ex: "Executor") -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(1 if a != b else 0)


def _lt(ex: "Executor") -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(1 if a < b else 0)


def _gt(ex: "Executor") -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(1 if a > b else 0)


def _le(ex: "Executor") -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(1 if a <= b else 0)


def _ge(ex: "Executor") -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(1 if a >= b else 0)


# ---------------------------------------------------------------------------
# Logical — bitwise on 0/1 encoding
# ---------------------------------------------------------------------------


def _and(ex: "Executor") -> None:
    """( a b -- (a & b) ). With 0/1 inputs reduces to logical AND;
    matches mlog `op and` on the 0/1 encoding."""
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(int(a) & int(b))


def _or(ex: "Executor") -> None:
    """( a b -- (a | b) ). With 0/1 inputs reduces to logical OR;
    matches mlog `op or`."""
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(int(a) | int(b))


def _not(ex: "Executor") -> None:
    """( a -- !a ). 0 → 1, anything truthy → 0. Matches mlog `op not`
    when the input is constrained to the 0/1 boolean encoding."""
    a = ex.data_stack.pop()
    ex.data_stack.append(0 if a else 1)


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------


def _dup(ex: "Executor") -> None:
    ex.data_stack.append(ex.data_stack[-1])


def _drop(ex: "Executor") -> None:
    ex.data_stack.pop()


def _swap(ex: "Executor") -> None:
    ex.data_stack[-1], ex.data_stack[-2] = ex.data_stack[-2], ex.data_stack[-1]


def _over(ex: "Executor") -> None:
    # ( a b -- a b a )
    ex.data_stack.append(ex.data_stack[-2])


def _rot(ex: "Executor") -> None:
    # ( a b c -- b c a )
    c = ex.data_stack.pop()
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(b)
    ex.data_stack.append(c)
    ex.data_stack.append(a)


def _nip(ex: "Executor") -> None:
    # ( a b -- b )
    b = ex.data_stack.pop()
    ex.data_stack.pop()  # discard a
    ex.data_stack.append(b)


def _tuck(ex: "Executor") -> None:
    # ( a b -- b a b )
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(b)
    ex.data_stack.append(a)
    ex.data_stack.append(b)


# ---------------------------------------------------------------------------
# Variables — @ / !
# ---------------------------------------------------------------------------


def _store(ex: "Executor") -> None:
    """`!` — ( value addr -- ) store value into the named variable.
    `addr` is the variable name (string handle pushed by the executor
    when a `UserVariable` WordCall is dispatched)."""
    addr = ex.data_stack.pop()
    value = ex.data_stack.pop()
    ex._write_variable(str(addr), value)


def _fetch(ex: "Executor") -> None:
    """`@` — ( addr -- value ) fetch the named variable's current
    value. `addr` is the variable name (string handle)."""
    addr = ex.data_stack.pop()
    ex.data_stack.append(ex._read_variable(str(addr)))


# ---------------------------------------------------------------------------
# IO — .
# ---------------------------------------------------------------------------


def _dot(ex: "Executor") -> None:
    """`.` — pop top of stack and print via `world.print`. Integer-shaped
    floats render without a trailing '.0' so equivalence with mlog
    `print` (which has no implicit decimal) is preserved.

    All output goes through `MockWorld.print` → `MessagePrintEvent` so
    test fixtures and the web viz can observe; mlog has no stdout, so
    this is the only equivalence-safe sink.
    """
    val = ex.data_stack.pop()
    if isinstance(val, bool):
        # Python bool is a subclass of int; check before the int branch
        # so True doesn't render as "True".
        text = "1" if val else "0"
    elif isinstance(val, float) and val.is_integer():
        text = str(int(val))
    else:
        text = str(val)
    ex.world.print(text)


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------


_PRIMITIVES: dict[str, PrimitiveFn] = {
    # Arithmetic
    "+": _plus,
    "-": _minus,
    "*": _times,
    "/": _divide,
    "MOD": _mod,
    # Comparison (mlog 0/1)
    "=": _eq,
    "<>": _ne,
    "<": _lt,
    ">": _gt,
    "<=": _le,
    ">=": _ge,
    # Logical
    "AND": _and,
    "OR": _or,
    "NOT": _not,
    # Stack
    "DUP": _dup,
    "DROP": _drop,
    "SWAP": _swap,
    "OVER": _over,
    "ROT": _rot,
    "NIP": _nip,
    "TUCK": _tuck,
    # Variables
    "@": _fetch,
    "!": _store,
    # IO
    ".": _dot,
}


def register_all(executor: "Executor") -> None:
    """Register every built-in implemented in this module onto `executor`.

    Overrides any prior implementation registered for the same name
    (later wins; matches `Executor.register_primitive` semantics). The
    `.10` executor pre-registers minimal stubs for `+`, `.`, `@`, `!`,
    `I`, `J` — this call replaces the first four with the canonical
    primitives.py versions; I and J are left untouched (they belong to
    the executor's loop-control machinery, not the word table).

    Mindustry primitives (PRINT, PRINTFLUSH, WAIT, SENSOR, GETLINK)
    are NOT registered here — bead mforth-10t.12 owns those and
    extends this module.

    VARIABLE is NOT in the table — the executor handles the literal
    `WordCall("VARIABLE")` directly to consume the next term as the
    declared variable name.
    """
    for name, fn in _PRIMITIVES.items():
        executor.register_primitive(name, fn)


__all__ = [
    "PrimitiveFn",
    "register_all",
]
