"""mforth host REPL built-in primitives (bead mforth-10t.11).

Implements every built-in registered in `mforth.dictionary` (the A3
registry from bead mforth-10t.6) as a Python callable taking the
`Executor` and mutating its data stack / variables / world. The
`register_all(executor)` helper wires the entire table onto a single
executor instance.

This bead covers the arith / stack / logical / var / io families.
Mindustry-tagged primitives (PRINT, PRINTFLUSH, WAIT, SENSOR,
GETLINK) are wired in this same module by bead mforth-10t.12 — see
the "Mindustry primitives" section below. I and J (control tag)
ship in the executor's default primitive set
(`backend.host._DEFAULT_PRIMITIVES`) because every DO/LOOP test in
.10 already depends on them; we do not override them here.

## Block-handle representation (LOAD-BEARING — bead .12)

PRINTFLUSH, SENSOR, and GETLINK all traffic in "block handles" — the
data-stack form of a reference to a linked Mindustry block. The host
REPL represents a block handle as the **bare mforth-name string**
(e.g. `"message1"`), NOT a prefixed form like `"block:message1"`:

* `MockWorld.lookup_block(name)` and `MockWorld.getlink(i)` both
  speak the bare-name form, so primitive bodies pass values through
  unchanged with no prefix-strip step.
* mlog itself uses bare identifiers for block names in emitted source,
  so the REPL ↔ mlog equivalence story stays one-to-one — bead .16's
  emitter pushes the same bare name into the slot variable.
* The data stack is a heterogeneous Python list; bare strings are
  already structurally distinct from numerics, so the "block:" prefix
  would only add overhead without resolving any ambiguity.

The bead text recommended the `"block:<name>"` form; this override
was made at design time and is mirrored in
`tests/unit/test_mindustry_primitives.py`'s module docstring.

## GETLINK out-of-range returns None (mlog null)

`world.getlink(i)` returns `None` for out-of-range `i` (mlog returns
`null`). The host primitive pushes that `None` onto the data stack to
satisfy the static stack effect `(1, 1)` — pushing nothing would
underflow a downstream consumer and falsify the stackchecker. The
mlog interpreter (bead .31) produces the equivalent observable (the
result variable is left as `null`).

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
# Mindustry primitives (bead mforth-10t.12)
#
# Each is a thin pass-through to a `MockWorld` method that emits the
# corresponding Event on the world's EventStream. The host REPL and the
# mlog backend (bead .16) must produce identical event sequences for the
# same source program — that is the REPL ↔ mlog equivalence contract.
# ---------------------------------------------------------------------------


def _print(ex: "Executor") -> None:
    """`PRINT` — ( str -- ) queue a value to the world's print buffer.

    `world.print(str)` calls `str()` on its input and emits
    `MessagePrintEvent(text=...)`. Numeric arguments are stringified
    via Python's `str()` (matches mlog `print` which accepts any value).
    """
    value = ex.data_stack.pop()
    ex.world.print(value)


def _printflush(ex: "Executor") -> None:
    """`PRINTFLUSH` — ( block -- ) flush the print buffer to a message
    block. Block handle is the bare mforth-name string. Emits
    `MessagePrintflushEvent(block_name, buffer)` regardless of whether
    the block exists (mlog: silent on bad block; the event is still
    observable so subscribers can count attempts).
    """
    block_name = ex.data_stack.pop()
    ex.world.printflush(str(block_name))


def _wait(ex: "Executor") -> None:
    """`WAIT` — ( seconds -- ) advance the simulation clock and emit
    `WaitEvent(seconds)`. `world.wait` coerces via `float(seconds)`.
    """
    seconds = ex.data_stack.pop()
    ex.world.wait(seconds)


def _sensor(ex: "Executor") -> None:
    """`SENSOR` — ( block prop -- value ) read property `prop` from the
    named block and push the value. Missing block or missing property
    yields 0.0 (community-lore mlog behavior; pinned in world.py).
    Emits `SensorReadEvent(block_name, prop, value)`.
    """
    prop = ex.data_stack.pop()
    block_name = ex.data_stack.pop()
    value = ex.world.sensor(str(block_name), str(prop))
    ex.data_stack.append(value)


def _getlink(ex: "Executor") -> None:
    """`GETLINK` — ( i -- block ) push the bare mforth-name of the i-th
    linked block, or `None` if i is out of range (mlog: null). Static
    stack effect (1, 1) requires that *something* be pushed even on the
    out-of-range path; pushing `None` is the closest analog to mlog's
    `null` and matches `world.getlink`'s return contract.

    `world.getlink` only emits `LinkResolvedEvent` for in-range lookups;
    out-of-range is silent on the event stream, by design.
    """
    i = ex.data_stack.pop()
    result = ex.world.getlink(int(i))
    ex.data_stack.append(result)


# ---------------------------------------------------------------------------
# Mindustry CONTROL block-instructions (bead mforth-cto)
#
# Per-sub-command words. Each pops its operands in Forth order (top of
# stack last), forwards to `world.control(op, block, *args)`. The world
# records a `ControlEvent` and (for `enabled` / `config`) mutates the
# matching block's state. Missing-block invocations still emit the event
# — same convention as PRINTFLUSH (.12).
# ---------------------------------------------------------------------------


def _control_enabled(ex: "Executor") -> None:
    """`CONTROL-ENABLED` — ( block flag -- )."""
    flag = ex.data_stack.pop()
    block = ex.data_stack.pop()
    ex.world.control("enabled", str(block), flag)


def _control_config(ex: "Executor") -> None:
    """`CONTROL-CONFIG` — ( block value -- )."""
    value = ex.data_stack.pop()
    block = ex.data_stack.pop()
    ex.world.control("config", str(block), value)


def _control_shoot(ex: "Executor") -> None:
    """`CONTROL-SHOOT` — ( block x y shoot -- )."""
    shoot = ex.data_stack.pop()
    y = ex.data_stack.pop()
    x = ex.data_stack.pop()
    block = ex.data_stack.pop()
    ex.world.control("shoot", str(block), x, y, shoot)


def _control_shootp(ex: "Executor") -> None:
    """`CONTROL-SHOOTP` — ( block unit shoot -- )."""
    shoot = ex.data_stack.pop()
    unit = ex.data_stack.pop()
    block = ex.data_stack.pop()
    ex.world.control("shootp", str(block), unit, shoot)


def _control_color(ex: "Executor") -> None:
    """`CONTROL-COLOR` — ( block r g b -- )."""
    b = ex.data_stack.pop()
    g = ex.data_stack.pop()
    r = ex.data_stack.pop()
    block = ex.data_stack.pop()
    ex.world.control("color", str(block), r, g, b)


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
    # Mindustry (bead .12)
    "PRINT": _print,
    "PRINTFLUSH": _printflush,
    "WAIT": _wait,
    "SENSOR": _sensor,
    "GETLINK": _getlink,
    # Mindustry CONTROL (bead mforth-cto)
    "CONTROL-ENABLED": _control_enabled,
    "CONTROL-CONFIG": _control_config,
    "CONTROL-SHOOT": _control_shoot,
    "CONTROL-SHOOTP": _control_shootp,
    "CONTROL-COLOR": _control_color,
}


# ---------------------------------------------------------------------------
# Mindustry @-identifier primitives (bead mforth-eaz)
#
# Each is a `(-- value)` push. Two flavors:
#
#   * Magic vars push a deterministic stub from
#     `dictionary.MINDUSTRY_MAGIC_STUBS` (0/0.0/known constants). The mlog
#     interpreter pre-seeds the SAME stubs so REPL ↔ mlog equivalence
#     holds even though Mindustry's runtime values are non-deterministic.
#
#   * Content names + sensor properties + tile sentinels push their bare
#     `@name` string as an opaque tag — matches the .12 block-handle
#     "bare string" convention. SENSOR pops the tag and forwards it to
#     `world.sensor(block, prop)`.
#
# Aliases (e.g. `@ticks` → `@tick`) resolve at the dictionary level —
# both look up the same BuiltinWord object, so `Executor._run_word_call`
# uses the entry's canonical `.name` to look up the primitive and both
# alias routes hit the same callable.
# ---------------------------------------------------------------------------


def _make_magic_push(name: str, value: object) -> PrimitiveFn:
    """Build a `(-- value)` primitive that pushes the deterministic stub
    for magic var `name`."""

    def push(ex: "Executor") -> None:
        ex.data_stack.append(value)

    push.__name__ = f"_push_magic_{name.lstrip('@')}"
    push.__doc__ = f"`{name}` — push deterministic stub value ({value!r})."
    return push


def _make_tag_push(name: str) -> PrimitiveFn:
    """Build a `(-- tag)` primitive that pushes the bare `@name` string
    as an opaque tag (matches the .12 block-handle convention)."""

    def push(ex: "Executor") -> None:
        ex.data_stack.append(name)

    push.__name__ = f"_push_tag_{name.lstrip('@').replace('-', '_')}"
    push.__doc__ = f"`{name}` — push bare tag string."
    return push


def _build_mindustry_primitives() -> dict[str, PrimitiveFn]:
    """Build the full Mindustry @-identifier primitive table.

    Walks the dictionary's `_MINDUSTRY_IDENTIFIERS` registry so the
    host side cannot drift from the dictionary side — every entry the
    dictionary knows about gets a host primitive here.
    """
    from mforth.dictionary import (  # local to avoid an import cycle
        MINDUSTRY_MAGIC_STUBS,
        _MINDUSTRY_IDENTIFIERS,
    )

    table: dict[str, PrimitiveFn] = {}
    for entry in _MINDUSTRY_IDENTIFIERS:
        name = entry.name
        if entry.tag == "mindustry-magic":
            stub = MINDUSTRY_MAGIC_STUBS.get(name)
            table[name] = _make_magic_push(name, stub)
        else:
            table[name] = _make_tag_push(name)
    return table


_MINDUSTRY_IDENT_PRIMITIVES: dict[str, PrimitiveFn] = _build_mindustry_primitives()


def register_all(executor: "Executor") -> None:
    """Register every built-in implemented in this module onto `executor`.

    Overrides any prior implementation registered for the same name
    (later wins; matches `Executor.register_primitive` semantics). The
    `.10` executor pre-registers minimal stubs for `+`, `.`, `@`, `!`,
    `I`, `J` — this call replaces the first four with the canonical
    primitives.py versions; I and J are left untouched (they belong to
    the executor's loop-control machinery, not the word table).

    Mindustry primitives (PRINT, PRINTFLUSH, WAIT, SENSOR, GETLINK)
    are also registered here — bead mforth-10t.12 wired them in. Each
    is a thin pass-through to a `MockWorld` method that emits the
    corresponding Event on `world.events`. Block handles on the data
    stack are bare mforth-name strings (see the module docstring's
    "Block-handle representation" section).

    Bead mforth-eaz adds the 154 Mindustry @-identifier push primitives
    (magic vars + content names + sensor props + tile sentinels).

    VARIABLE is NOT in the table — the executor handles the literal
    `WordCall("VARIABLE")` directly to consume the next term as the
    declared variable name.
    """
    for name, fn in _PRIMITIVES.items():
        executor.register_primitive(name, fn)
    for name, fn in _MINDUSTRY_IDENT_PRIMITIVES.items():
        executor.register_primitive(name, fn)


__all__ = [
    "PrimitiveFn",
    "register_all",
]
