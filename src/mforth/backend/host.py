"""mforth host REPL executor (bead mforth-10t.10).

The executor walks an annotated AST term-by-term, maintaining a
persistent quadruple `(data_stack, return_stack, variables, world)` so
the interactive REPL can keep results around between user input lines.

Stack-checking has already run by the time we get here (the
`StackcheckResult` carries the dictionary and per-term depth
annotations). The executor does **not** re-check; it executes,
trusting the gate.

## Term dispatch

* `LitInt` / `LitStr` push their value onto the data stack.
* `WordCall(name)` looks the name up in the dictionary, then:
    - `BuiltinWord` → invoke the registered host implementation. If no
      implementation has been registered, raise `NotImplementedError`
      with a pointer to follow-on beads .11/.12.
    - `Definition`  → recursively execute the body (inline call;
      Forth semantics, matches our v1 no-return-stack codegen).
    - `UserVariable` → push the variable's name as its address-handle.
* `IfThen` pops the flag; runs `then_body` if non-zero, else `else_body`.
* `Begin(kind="until")` → execute body, pop flag, repeat while flag==0.
* `Begin(kind="while-repeat")` → execute body (the test), pop flag, exit
  if zero; otherwise execute `cond_body` (the loop body) and repeat.
* `DoLoop(body)` → pop limit & index from data stack onto return stack;
  iterate from index < limit; pop both at end.
* `VarRef(name, mode)` → if mode=='fetch', read; if mode=='store', pop
  value and write.

## Primitive registry

Real built-in implementations (the 30 dictionary words) land in beads
mforth-10t.11 (stack/arith/comparison/logical) and mforth-10t.12
(Mindustry primitives). This bead ships a registry + just enough stubs
(`+`, `.`, `@`, `!`) for the acceptance test and VARIABLE round-trip.

Future beads register more primitives via `Executor.register_primitive`.

## State persistence

A single `Executor` instance is meant to live for the entire REPL
session. Each `.execute(result)` call:
* extends `self.dictionary` with the new program's user definitions
  + VARIABLE declarations (already done by the stackchecker);
* runs the program's `main` against the persistent state quadruple.

Definitions accumulate across calls because the dictionary is the same
object the stackchecker populated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from mforth.dictionary import (
    BuiltinWord,
    Dictionary,
    UserVariable,
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
    SrcLoc,
    VarRef,
    WordCall,
)
from mforth.backend.world import MockWorld
from mforth.stackcheck import StackcheckResult


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExecutionError(Exception):
    """Raised on runtime errors the static stack-checker couldn't catch
    (e.g. a custom primitive that misbehaves, or an unexpected exception
    raised by a registered primitive). Carries the source location of
    the offending term.
    """

    def __init__(self, message: str, src_loc: SrcLoc) -> None:
        super().__init__(
            f"{src_loc.file}:{src_loc.line}:{src_loc.col}: {message}"
        )
        self.message = message
        self.src_loc = src_loc


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


PrimitiveFn = Callable[["Executor"], None]


@dataclass
class Executor:
    world: MockWorld = field(default_factory=MockWorld)
    data_stack: list = field(default_factory=list)
    return_stack: list = field(default_factory=list)
    variables: dict = field(default_factory=dict)
    dictionary: Dictionary = field(default_factory=standard_dictionary)
    _primitives: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Pre-register the minimal primitives needed by the acceptance
        # test + VARIABLE round-trip. Full coverage lands in .11/.12.
        for name, fn in _DEFAULT_PRIMITIVES.items():
            self._primitives.setdefault(name, fn)

    # -- public API ----------------------------------------------------

    def register_primitive(self, name: str, fn: PrimitiveFn) -> None:
        """Plug an implementation in for the named built-in. Case-insensitive
        (stored upper). Later registrations replace earlier ones."""
        self._primitives[name.upper()] = fn

    def execute(self, result: StackcheckResult) -> None:
        """Run `result.program.main` against this executor's persistent
        state. Definitions in `result.program.definitions` are already
        in `result.dictionary` from the stackcheck pass — we adopt that
        dictionary so subsequent `.execute()` calls share it.
        """
        if result.dictionary is not None:
            self.dictionary = result.dictionary
        self._run_terms(result.program.main)

    # -- internal: term dispatch --------------------------------------

    def _run_terms(self, terms: list) -> None:
        i = 0
        while i < len(terms):
            term = terms[i]

            if isinstance(term, LitInt):
                self.data_stack.append(term.value)
                i += 1
                continue

            if isinstance(term, LitFloat):
                # Push a Python float — already the runtime type used by
                # SENSOR returns and `/` division (mforth-dlr).
                self.data_stack.append(term.value)
                i += 1
                continue

            if isinstance(term, LitStr):
                # String-handle is just the Python str — host-stored, no
                # interning beyond what CPython does for free.
                self.data_stack.append(term.value)
                i += 1
                continue

            if isinstance(term, WordCall):
                # Special case the `VARIABLE <name>` pair: the parser
                # leaves these as plain WordCalls (VarRef synthesis is
                # deferred per the overnight decision drawer). At runtime
                # `VARIABLE` is a no-op consuming the *next* token-as-
                # WordCall (the declared name).
                if term.name.upper() == "VARIABLE":
                    if i + 1 >= len(terms) or not isinstance(terms[i + 1], WordCall):
                        raise ExecutionError(
                            "VARIABLE not followed by a name",
                            term.src_loc,
                        )
                    name_term = terms[i + 1]
                    # Ensure the variable exists (the resolver should
                    # have registered it, but be defensive — REPL might
                    # have created the AST by hand).
                    self.variables.setdefault(name_term.name, 0.0)
                    i += 2
                    continue
                self._run_word_call(term)
                i += 1
                continue

            if isinstance(term, IfThen):
                flag = self.data_stack.pop()
                branch = term.then_body if flag != 0 else term.else_body
                self._run_terms(branch)
                i += 1
                continue

            if isinstance(term, Begin):
                if term.kind == "until":
                    while True:
                        self._run_terms(term.body)
                        flag = self.data_stack.pop()
                        if flag != 0:
                            break
                elif term.kind == "while-repeat":
                    while True:
                        self._run_terms(term.body)  # the test
                        flag = self.data_stack.pop()
                        if flag == 0:
                            break
                        self._run_terms(term.cond_body)  # the loop body
                else:
                    raise ExecutionError(
                        f"unknown Begin kind {term.kind!r}", term.src_loc
                    )
                i += 1
                continue

            if isinstance(term, DoLoop):
                # Forth: limit on top? Convention varies; we follow the
                # standard ANS Forth `( limit index -- )` where index is
                # on top. After DO they live on the return stack with
                # index on top.
                try:
                    index = self.data_stack.pop()
                    limit = self.data_stack.pop()
                except IndexError as e:
                    raise ExecutionError(
                        f"DO underflow ({e})", term.src_loc
                    ) from e
                # Push limit then index so index is on top (and `I` reads top).
                self.return_stack.append(limit)
                self.return_stack.append(index)
                while True:
                    cur = self.return_stack[-1]
                    cur_limit = self.return_stack[-2]
                    if cur >= cur_limit:
                        break
                    self._run_terms(term.body)
                    # Body must be stack-neutral on return_stack too; advance.
                    self.return_stack[-1] = self.return_stack[-1] + 1
                # Pop index and limit.
                self.return_stack.pop()
                self.return_stack.pop()
                i += 1
                continue

            if isinstance(term, VarRef):
                if term.mode == "fetch":
                    self.data_stack.append(self._read_variable(term.name))
                elif term.mode == "store":
                    try:
                        value = self.data_stack.pop()
                    except IndexError as e:
                        raise ExecutionError(
                            f"store underflow for '{term.name}'", term.src_loc
                        ) from e
                    self._write_variable(term.name, value)
                else:
                    raise ExecutionError(
                        f"unknown VarRef mode {term.mode!r}", term.src_loc
                    )
                i += 1
                continue

            raise ExecutionError(
                f"unknown Term type {type(term).__name__}",
                getattr(term, "src_loc", SrcLoc("<unknown>", 1, 1)),
            )

    # -- internal: WordCall dispatch ----------------------------------

    def _run_word_call(self, term: WordCall) -> None:
        entry = self.dictionary.lookup(term.name)
        if entry is None:
            raise ExecutionError(
                f"unresolved word '{term.name}' (resolver should have caught this)",
                term.src_loc,
            )

        if isinstance(entry, BuiltinWord):
            fn = self._primitives.get(entry.name.upper())
            if fn is None:
                raise NotImplementedError(
                    f"primitive '{entry.name}' not implemented yet — "
                    f"see mforth-10t.11/.12"
                )
            try:
                fn(self)
            except (NotImplementedError, ExecutionError):
                raise
            except Exception as e:  # noqa: BLE001 — wrap with src loc
                raise ExecutionError(
                    f"primitive '{entry.name}' raised {type(e).__name__}: {e}",
                    term.src_loc,
                ) from e
            return

        if isinstance(entry, Definition):
            # Inline call: walk the body in the caller's stacks. This
            # matches the v1 codegen (no return stack for user calls;
            # everything inlines). The Python recursion limit is the
            # practical bound, which is fine for the demos.
            self._run_terms(entry.body)
            return

        if isinstance(entry, UserVariable):
            # Pushing a "variable address" — we use the name itself as
            # the handle. `@` / `!` pop the handle and act on
            # `self.variables[name]`.
            self.data_stack.append(entry.name)
            return

        raise ExecutionError(
            f"unknown dictionary entry type {type(entry).__name__} for '{term.name}'",
            term.src_loc,
        )

    # -- internal: variable I/O ---------------------------------------

    def _read_variable(self, name: str) -> float:
        value = float(self.variables.get(name, 0.0))
        # Mirror to the world's EventStream so subscribers see it.
        self.world.read_variable(name)
        return value

    def _write_variable(self, name: str, value) -> None:
        v = float(value)
        self.variables[name] = v
        self.world.write_variable(name, v)


# ---------------------------------------------------------------------------
# Default primitives (skeleton — full implementations land in .11/.12)
# ---------------------------------------------------------------------------


def _prim_plus(ex: Executor) -> None:
    b = ex.data_stack.pop()
    a = ex.data_stack.pop()
    ex.data_stack.append(a + b)


def _prim_print(ex: Executor) -> None:
    """`.` — pop top of stack and print via the world. The host-side
    convention: integers render without a decimal point so the
    acceptance test's `1 2 + .` produces "3" rather than "3.0".
    """
    val = ex.data_stack.pop()
    if isinstance(val, float) and val.is_integer():
        text = str(int(val))
    elif isinstance(val, bool):
        text = "1" if val else "0"
    else:
        text = str(val)
    ex.world.print(text)


def _prim_store(ex: Executor) -> None:
    """`!` — ( value addr -- ) store value into the named variable."""
    addr = ex.data_stack.pop()
    value = ex.data_stack.pop()
    ex._write_variable(str(addr), value)


def _prim_fetch(ex: Executor) -> None:
    """`@` — ( addr -- value ) fetch from the named variable."""
    addr = ex.data_stack.pop()
    ex.data_stack.append(ex._read_variable(str(addr)))


def _prim_i(ex: Executor) -> None:
    """`I` — push current DO/LOOP index (top of return stack)."""
    ex.data_stack.append(ex.return_stack[-1])


def _prim_j(ex: Executor) -> None:
    """`J` — push outer DO/LOOP index. The return stack layout per loop
    is `[..., outer_limit, outer_index, inner_limit, inner_index]`, so
    the outer index is at offset -3 from the top.
    """
    ex.data_stack.append(ex.return_stack[-3])


_DEFAULT_PRIMITIVES: dict = {
    "+": _prim_plus,
    ".": _prim_print,
    "!": _prim_store,
    "@": _prim_fetch,
    "I": _prim_i,
    "J": _prim_j,
}


__all__ = [
    "ExecutionError",
    "Executor",
    "PrimitiveFn",
]
