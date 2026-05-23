"""In-repo mlog interpreter — bead mforth-10t.31.

A tiny Python interpreter for the mlog text emitted by
:mod:`mforth.backend.mlog.finalize`. It executes against the SAME
:class:`MockWorld` surface the host REPL uses, so the equivalence
test harness (``tests/integration/test_equivalence.py``) can prove:
the host REPL and the compile-then-interpret path produce the SAME
observable event sequence for the same ``.fs`` source.

That equivalence property is the HEADLINE TEST CLASS for mforth
(CLAUDE.md hard rule): divergence is the highest-severity regression
class because the REPL is the teaching surface — if it diverges from
compiled output, mforth has failed as a teaching tool.

What it executes
================

The v1 instruction subset matching what bead .16/.17/.18/.19 emit:

* ``set <result> <value>`` — copy a literal or variable into a slot.
* ``op <operation> <result> <a> <b>`` — arithmetic, comparison,
  logical, bitwise. The full operation set from the mlog reference
  drawer (``drawer_mforth_references_96affa82d1ff0917a17bdbaf``)
  except a small set of float-tolerance edge cases that v1 fixtures
  don't exercise.
* ``jump <line> <cond> <a> <b>`` — absolute, 0-indexed line number
  (post-resolve_labels output). Conditions: ``equal``, ``notEqual``,
  ``strictEqual``, ``lessThan``, ``lessThanEq``, ``greaterThan``,
  ``greaterThanEq``, ``always``.
* ``print <value>`` — calls :meth:`MockWorld.print`. Value is
  formatted to match host PRINT (int-shaped floats render without
  ``.0`` so REPL ↔ mlog equivalence holds for stack-computed numerics).
* ``printflush <block>`` — calls :meth:`MockWorld.printflush`.
* ``wait <seconds>`` — calls :meth:`MockWorld.wait`.
* ``sensor <result> <block> <prop>`` — calls :meth:`MockWorld.sensor`,
  stores the result.
* ``getlink <result> <i>`` — calls :meth:`MockWorld.getlink`, stores
  the resolved block name (or ``None``). Out-of-range emits no event
  (matching the host primitive — bead .12 contract).
* ``end`` — sugar for ``jump 0 always`` (auto-loop restart).

Comment lines (first non-whitespace ``#``) are skipped at lex time and
do NOT consume executable line-number space. The header comment that
:func:`finalize.write_mlog` emits is line 0 in the text but line -1 in
the executable space; ``jump 0`` always targets the first executable
instruction.

Number representation
=====================

mlog uses IEEE 754 doubles for all numbers. The interpreter stores
operands as Python ``int`` when they are whole-number literals (so
``set s0 2`` followed by ``print s0`` renders as ``"2"``, matching the
host's ``print 2`` which str()s the int directly), and as ``float``
otherwise. Op results are kept as ``int`` when both inputs were
``int`` AND the result is exact (mirroring Python's int arithmetic),
otherwise ``float``. This preserves REPL ↔ mlog string equivalence for
the common case of integer programs.

Auto-loop
=========

When ``@counter`` advances past the last executable instruction the
interpreter wraps back to ``@counter = 0``. The ``iterations`` argument
to :meth:`run` counts complete passes — a pass ends when the loop
wraps (or when an ``end`` is executed). Mode B prologue ``getlink``
lines are part of the program body and re-execute every iteration,
matching real-mlog behaviour.

Why this lives at the package top-level
=======================================

The host backend (``mforth.backend``) is the *host* simulation; mlog
interpretation is a sibling surface, not a sub-component of the host.
Placing it at ``mforth.mlog_interp`` keeps the dependency arrow
straight: ``mlog_interp`` imports ``backend.world`` but not the
``backend.host`` executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from mforth.backend.world import MockWorld


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MlogInterpError(Exception):
    """Raised on malformed mlog text or unimplemented instructions.

    Distinct from :class:`mforth.backend.host.ExecutionError` so a
    failing equivalence test can pinpoint which side of the parity
    crashed.
    """


# ---------------------------------------------------------------------------
# Operand parsing
# ---------------------------------------------------------------------------


def _parse_literal(token: str) -> Any:
    """Return ``token`` coerced to int / float / string-as-bare-name.

    Operand classification:

    * A token wrapped in double quotes is a string literal — strip the
      quotes (PRINT may receive a string).
    * A token that parses as an integer (no decimal point, no
      exponent) is an ``int`` — preserves the host's str-of-int format
      for whole-number values.
    * A token that parses as a float is a ``float``.
    * Otherwise it is a variable name (bare identifier, possibly with
      ``@`` prefix for built-ins) — return as a string so the caller
      can look it up.
    """
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    # int first so "5" doesn't become 5.0
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token  # variable name (or bare identifier)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_for_print(value: Any) -> str:
    """Format a value for ``print`` to match the host PRINT primitive's
    observable string. Host PRINT calls ``world.print(value)`` which
    ``str()`` 's the value; int → "5"; float that is whole → would be
    "5.0" in Python but the host never gets a float for whole numbers
    because ``2 + 3`` stays an int. The interpreter preserves the same
    by keeping integer literals + integer arithmetic as Python int.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        # Defensive: an int-shaped float should still render integer-ly
        # so a future codegen path that introduces a float doesn't
        # silently diverge from host PRINT on integer operands.
        return str(int(value))
    return str(value)


# ---------------------------------------------------------------------------
# Op table
# ---------------------------------------------------------------------------


def _op_add(a, b): return a + b
def _op_sub(a, b): return a - b
def _op_mul(a, b): return a * b


def _op_div(a, b):
    """Float division. Division by zero yields inf / -inf / nan (matches
    mlog `op div` — no exception)."""
    if b == 0:
        import math
        if a == 0:
            return math.nan
        return math.inf if a > 0 else -math.inf
    return a / b


def _op_idiv(a, b):
    """Integer division — mlog `op idiv`. Returns float in mlog; we
    return int when both operands are int so PRINT output matches
    host arithmetic (``20 4 /`` host: int; mlog idiv: int after
    coercion)."""
    if b == 0:
        import math
        return math.nan
    if isinstance(a, int) and isinstance(b, int):
        return a // b
    return float(a) // float(b)


def _op_mod(a, b):
    if b == 0:
        import math
        return math.nan
    return a % b


def _op_pow(a, b): return a ** b
def _op_equal(a, b): return 1 if a == b else 0
def _op_not_equal(a, b): return 1 if a != b else 0
def _op_strict_equal(a, b):
    """Binary equality — for booleans/identity comparisons."""
    return 1 if (type(a) == type(b) and a == b) else 0


def _op_less_than(a, b): return 1 if a < b else 0
def _op_less_than_eq(a, b): return 1 if a <= b else 0
def _op_greater_than(a, b): return 1 if a > b else 0
def _op_greater_than_eq(a, b): return 1 if a >= b else 0


def _op_land(a, b):
    """Logical and — mlog: nonzero is truthy."""
    return 1 if (a and b) else 0


def _op_or(a, b):
    return 1 if (a or b) else 0


def _op_not(a, _b):
    """Unary not — mlog's `op not` is bitwise; the codegen emits
    `op not <r> <a> 0` for the Forth `NOT` (logical). We treat it
    as logical-not: 1 if zero, 0 otherwise."""
    return 1 if not a else 0


def _op_and(a, b):
    """Bitwise and — for integer operands. Promotes via int()."""
    return int(a) & int(b)


def _op_xor(a, b):
    return int(a) ^ int(b)


def _op_shl(a, b):
    return int(a) << int(b)


def _op_shr(a, b):
    return int(a) >> int(b)


def _op_max(a, b): return max(a, b)
def _op_min(a, b): return min(a, b)
def _op_abs(a, _b): return abs(a)


def _op_floor(a, _b):
    import math
    return math.floor(a)


def _op_ceil(a, _b):
    import math
    return math.ceil(a)


def _op_sqrt(a, _b):
    import math
    return math.sqrt(a)


_OP_TABLE = {
    "add": _op_add,
    "sub": _op_sub,
    "mul": _op_mul,
    "div": _op_div,
    "idiv": _op_idiv,
    "mod": _op_mod,
    "pow": _op_pow,
    "equal": _op_equal,
    "notEqual": _op_not_equal,
    "strictEqual": _op_strict_equal,
    "lessThan": _op_less_than,
    "lessThanEq": _op_less_than_eq,
    "greaterThan": _op_greater_than,
    "greaterThanEq": _op_greater_than_eq,
    "land": _op_land,
    "or": _op_or,
    "and": _op_and,
    "not": _op_not,
    "xor": _op_xor,
    "shl": _op_shl,
    "shr": _op_shr,
    "max": _op_max,
    "min": _op_min,
    "abs": _op_abs,
    "floor": _op_floor,
    "ceil": _op_ceil,
    "sqrt": _op_sqrt,
}


# ---------------------------------------------------------------------------
# Jump conditions
# ---------------------------------------------------------------------------


_COND_TABLE = {
    "equal": lambda a, b: a == b,
    "notEqual": lambda a, b: a != b,
    "strictEqual": lambda a, b: type(a) == type(b) and a == b,
    "lessThan": lambda a, b: a < b,
    "lessThanEq": lambda a, b: a <= b,
    "greaterThan": lambda a, b: a > b,
    "greaterThanEq": lambda a, b: a >= b,
    "always": lambda a, b: True,
}


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


@dataclass
class MlogInterpreter:
    """Execute mlog text against a :class:`MockWorld`.

    Parameters
    ----------
    world
        The MockWorld the interpreter drives. Events emitted by the
        instruction handlers flow through ``world.events`` — the same
        seam the host REPL uses.
    text
        The mlog source text (typically the output of
        :func:`mforth.backend.mlog.finalize.finalize`).
    """

    world: MockWorld
    text: str = ""
    # mforth-0qi (2026-05-23): names of mlog variables that came from
    # Forth `VARIABLE` declarations. Reads/writes of these names are
    # routed through `world.read_variable` / `world.write_variable` so
    # the in-repo mlog interpreter emits the same `VariableReadEvent`
    # / `VariableWriteEvent` stream the host REPL emits — restoring
    # the REPL ↔ mlog equivalence property on every program touching a
    # VARIABLE. Default empty set keeps pre-existing callers (golden
    # tests, ad-hoc interpreter use) backward-compatible.
    user_variables: set = field(default_factory=set)
    instructions: list = field(default_factory=list, init=False)
    variables: dict = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.instructions = self._lex(self.text)
        self.variables.setdefault("@counter", 0)
        # Pre-seed Mindustry magic-var stubs (bead mforth-eaz). Same
        # deterministic values the host REPL's primitives push, so the
        # REPL ↔ mlog equivalence property holds: `@time` read on the
        # interpreter side yields the same 0.0 the host pushes from
        # `MINDUSTRY_MAGIC_STUBS`. Bare-tag identifiers (content names,
        # sensor props) are NOT pre-seeded — they only appear as bare
        # operands in lifted instructions (e.g. `sensor s0 reactor
        # @copper`), so the read path naturally falls through to the
        # unbound default (the @-name string for arithmetic purposes is
        # 0, but in sensor/print contexts the bare token is used as a
        # tag, not read as a value).
        from mforth.dictionary import MINDUSTRY_MAGIC_STUBS
        for name, value in MINDUSTRY_MAGIC_STUBS.items():
            self.variables.setdefault(name, value)
        # Also pre-seed the bare-tag identifiers to themselves so a
        # `print @copper` inside the interpreter renders "@copper"
        # (matches mlog's "unknown global resolves to its own name"
        # behavior for content references).
        from mforth.dictionary import _MINDUSTRY_IDENTIFIERS
        for entry in _MINDUSTRY_IDENTIFIERS:
            if entry.tag != "mindustry-magic":
                self.variables.setdefault(entry.name, entry.name)

    # ---- lexer ---------------------------------------------------------

    @staticmethod
    def _lex(text: str) -> list[tuple[str, list[str]]]:
        """Tokenise the mlog text into ``(opcode, operands)`` per
        non-comment line. Comments (``#`` as the first non-whitespace
        character) are skipped entirely — they do NOT consume
        executable line-number space (see module docstring).
        """
        out: list[tuple[str, list[str]]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            tokens = _tokenise_line(line)
            if not tokens:
                continue
            opcode = tokens[0]
            operands = tokens[1:]
            out.append((opcode, operands))
        return out

    # ---- variables -----------------------------------------------------

    def _read(self, token: str) -> Any:
        """Read an operand token — literal or variable name. ``@``-
        prefixed names that aren't bound default to 0 (matches mlog's
        null-as-zero coercion for arithmetic; the read path is also
        used by ``set`` so a copy of an unbound variable still works).

        Quoted string literals are distinguished from bare identifiers
        at the lex level — only the bare-identifier case triggers
        variable lookup. The quoted form returns the unquoted string
        directly.

        mforth-0qi: when the bare identifier names a Forth user
        variable (member of ``self.user_variables``), route the read
        through ``world.read_variable`` so the interpreter emits a
        ``VariableReadEvent`` matching the host REPL. The world's
        coerced float return value is preferred over our local cache
        so the event payload and the dispatcher both see the same
        instrumented value.
        """
        # Quoted string literal — never a variable lookup.
        if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
            return token[1:-1]
        value = _parse_literal(token)
        if isinstance(value, str):
            # variable name (bare identifier or @-prefixed builtin)
            if value in self.user_variables:
                cached = self.variables.get(value)
                # Non-numeric cache entries are block-name handles
                # (Mode B getlink output) — bypass the instrumented
                # path: world.read_variable coerces via float() and
                # would crash on a string, and the REPL never emits
                # VariableReadEvent for link-handle reads either.
                if cached is not None and not isinstance(cached, (int, float)):
                    return cached
                if isinstance(cached, bool):
                    return cached
                # Route through the world surface so the read emits an
                # event. world.read_variable defaults missing names to
                # 0.0, matching the unbound fallback below.
                return self.world.read_variable(value)
            if value in self.variables:
                return self.variables[value]
            # mlog default: unbound name is null; coerce to 0 for
            # arithmetic. For string-handle cases (e.g. block names
            # produced by `getlink`) the value will already be set.
            return 0
        return value

    def _write(self, name: str, value: Any) -> None:
        # mforth-0qi: route writes of names that came from Forth
        # VARIABLE declarations through world.write_variable so the
        # mlog interpreter emits VariableWriteEvent (matching the host
        # REPL). Compiler-internal names (s<i> stack slots,
        # __swap_tmp, @-prefixed magic vars) bypass instrumentation
        # so the event stream stays focused on user-visible state.
        #
        # Non-numeric writes (block-name strings produced by the Mode B
        # `getlink` prologue, or None from out-of-range getlink) bypass
        # instrumentation: world.write_variable coerces via float() and
        # would crash on a string, AND the REPL achieves the same
        # binding via dictionary pre-seeding (without emitting an
        # event), so going through write_variable here would add noise
        # the REPL never produces.
        if name in self.user_variables and isinstance(value, (int, float)) and not isinstance(value, bool):
            self.world.write_variable(name, value)
            # write_variable coerces to float; mirror the coerced value
            # back into our local variables dict so subsequent _read
            # paths see the same value the host REPL would.
            self.variables[name] = float(value)
            return
        self.variables[name] = value

    # ---- execution -----------------------------------------------------

    def run(self, *, iterations: int = 1) -> int:
        """Execute the program for ``iterations`` complete passes.

        A *pass* ends when either:

        * ``@counter`` reaches past the last instruction (auto-loop
          wrap), or
        * an ``end`` opcode executes.

        Returns the number of completed passes (always equals
        ``iterations`` unless an unrecoverable error was raised).
        """
        if not self.instructions:
            return 0
        n = len(self.instructions)
        # Safety guard: bound total instruction dispatches so a
        # pathological infinite loop in a fixture surfaces as a clear
        # error instead of hanging the test suite. The cap is generous
        # (~100K per requested iteration) to leave room for normal
        # DO/LOOP bodies.
        max_steps = 100_000 * max(1, iterations)
        steps = 0
        completed = 0
        self.variables["@counter"] = 0
        while completed < iterations:
            if steps >= max_steps:
                raise MlogInterpError(
                    f"interpreter step budget exceeded "
                    f"({max_steps} steps) — likely an infinite loop"
                )
            pc = int(self.variables["@counter"])
            if pc >= n or pc < 0:
                # Auto-loop wrap.
                self.variables["@counter"] = 0
                completed += 1
                continue
            opcode, operands = self.instructions[pc]
            if opcode == "end":
                # Sugar for `jump 0 always` — counts as completing a pass.
                self.variables["@counter"] = 0
                completed += 1
                steps += 1
                continue
            advance = self._dispatch(opcode, operands)
            steps += 1
            if advance:
                self.variables["@counter"] = pc + 1
        return completed

    def _dispatch(self, opcode: str, operands: list) -> bool:
        """Execute one instruction. Return True if the PC should advance
        by 1 after dispatch, False if the instruction set ``@counter``
        itself (jump/end-style).
        """
        handler = _DISPATCH.get(opcode)
        if handler is None:
            raise MlogInterpError(
                f"unimplemented mlog opcode {opcode!r} "
                f"(operands={operands!r})"
            )
        return handler(self, operands)


# ---------------------------------------------------------------------------
# Line tokeniser
# ---------------------------------------------------------------------------


def _tokenise_line(line: str) -> list[str]:
    """Split a single mlog source line into tokens.

    The tokeniser handles ``"quoted strings"`` as single tokens so
    ``print "hello world"`` parses as two tokens (``print``,
    ``"hello world"``). Outside quotes, runs of whitespace separate
    tokens.
    """
    tokens: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            # Read until the closing quote (preserve quotes in the token).
            j = i + 1
            while j < n and line[j] != '"':
                j += 1
            if j < n:
                tokens.append(line[i:j + 1])
                i = j + 1
            else:
                # Unterminated string — take the rest as-is.
                tokens.append(line[i:])
                i = n
            continue
        # Bare token — read until whitespace.
        j = i
        while j < n and not line[j].isspace():
            j += 1
        tokens.append(line[i:j])
        i = j
    return tokens


# ---------------------------------------------------------------------------
# Per-opcode handlers
# ---------------------------------------------------------------------------


def _h_set(interp: MlogInterpreter, operands: list) -> bool:
    name, src = operands[0], operands[1]
    interp._write(name, interp._read(src))
    return True


def _h_op(interp: MlogInterpreter, operands: list) -> bool:
    # `op <operation> <result> <a> <b>` — `b` is optional for unary
    # ops, but the codegen always emits 4 operands (the unary form
    # uses a sentinel; we tolerate both arities).
    operation = operands[0]
    result = operands[1]
    a = interp._read(operands[2]) if len(operands) > 2 else 0
    b = interp._read(operands[3]) if len(operands) > 3 else 0
    fn = _OP_TABLE.get(operation)
    if fn is None:
        raise MlogInterpError(f"unimplemented op operation {operation!r}")
    value = fn(a, b)
    interp._write(result, value)
    return True


def _h_jump(interp: MlogInterpreter, operands: list) -> bool:
    # `jump <line> <cond> <a> <b>`
    target_str = operands[0]
    cond = operands[1] if len(operands) > 1 else "always"
    a = interp._read(operands[2]) if len(operands) > 2 else 0
    b = interp._read(operands[3]) if len(operands) > 3 else 0
    fn = _COND_TABLE.get(cond)
    if fn is None:
        raise MlogInterpError(f"unimplemented jump condition {cond!r}")
    try:
        target = int(target_str)
    except ValueError as e:
        raise MlogInterpError(
            f"jump target {target_str!r} is not a numeric line"
        ) from e
    if fn(a, b):
        interp.variables["@counter"] = target
        return False
    return True


def _h_print(interp: MlogInterpreter, operands: list) -> bool:
    # `print <value>` — value is literal or variable name.
    value = interp._read(operands[0])
    # The host primitive funnels through world.print(value), which
    # str() 's its input. Pre-format to preserve integer-shaped
    # numerics so int arithmetic results render the same way.
    text = _format_for_print(value)
    interp.world.print(text)
    return True


def _h_printflush(interp: MlogInterpreter, operands: list) -> bool:
    # `printflush <block>` — block is a bare name (variable holding the
    # mforth-name string, or a literal name).
    block_token = operands[0]
    value = interp._read(block_token)
    if value == 0:
        # Unbound — fall back to the bare token (the codegen's bare
        # name shape: `printflush message1` with no preceding `set`).
        block_name = block_token
    else:
        block_name = str(value)
    interp.world.printflush(block_name)
    return True


def _h_wait(interp: MlogInterpreter, operands: list) -> bool:
    seconds = interp._read(operands[0])
    interp.world.wait(float(seconds))
    return True


def _h_sensor(interp: MlogInterpreter, operands: list) -> bool:
    # `sensor <result> <block> <prop>` — block and prop are bare names.
    result = operands[0]
    block_token = operands[1]
    prop_token = operands[2]
    block_value = interp._read(block_token)
    block_name = str(block_value) if block_value != 0 else block_token
    prop_value = interp._read(prop_token)
    prop_name = str(prop_value) if prop_value != 0 else prop_token
    value = interp.world.sensor(block_name, prop_name)
    interp._write(result, value)
    return True


def _h_getlink(interp: MlogInterpreter, operands: list) -> bool:
    # `getlink <result> <i>` — store resolved name (or None) into result.
    result = operands[0]
    i = int(interp._read(operands[1]))
    name = interp.world.getlink(i)
    # `None` (out-of-range) is stored verbatim; the next consumer (e.g.
    # printflush) handles None as the bare token fallback.
    interp._write(result, name if name is not None else None)
    return True


_CONTROL_SUBCOMMAND_ARITY: dict[str, int] = {
    # sub-command name → number of meaningful args after the block (the
    # rest are zero-padded operands the emitter inserts to fill mlog's
    # 5-slot control instruction shape).
    "enabled": 1,
    "config": 1,
    "shoot": 3,
    "shootp": 2,
    "color": 3,
}


def _h_control(interp: MlogInterpreter, operands: list) -> bool:
    """`control <sub> <block> <a> <b> <c> <d>` (bead mforth-cto).

    Dispatches the sub-command to :meth:`MockWorld.control`. Unknown
    sub-commands raise :class:`MlogInterpError` so a typo'd emit
    surfaces loudly rather than silently no-op'ing.
    """
    if not operands:
        raise MlogInterpError("control: missing sub-command")
    sub = operands[0]
    if sub not in _CONTROL_SUBCOMMAND_ARITY:
        raise MlogInterpError(
            f"unimplemented control sub-command {sub!r} "
            f"(known: {sorted(_CONTROL_SUBCOMMAND_ARITY)})"
        )
    if len(operands) < 2:
        raise MlogInterpError(f"control {sub}: missing block operand")
    block_token = operands[1]
    block_value = interp._read(block_token)
    if isinstance(block_value, str) and block_value:
        block_name = block_value
    elif block_value == 0:
        # Unbound bare name — use the operand token directly (matches
        # the printflush bare-name fallback path).
        block_name = block_token
    else:
        block_name = str(block_value)
    arity = _CONTROL_SUBCOMMAND_ARITY[sub]
    # Resolve each meaningful arg via _read so bare names + numerics +
    # quoted strings all work. Trailing zero-padding operands are
    # ignored to keep the ControlEvent.args tuple shape consistent
    # with the host primitive.
    args = tuple(
        interp._read(operands[2 + k])
        for k in range(arity)
        if 2 + k < len(operands)
    )
    interp.world.control(sub, block_name, *args)
    return True


def _h_read(_interp, _operands) -> bool:
    raise MlogInterpError(
        "mlog `read` (memory cell) not supported in v1 — "
        "the cell-free codegen rule means this opcode should not be emitted"
    )


def _h_write(_interp, _operands) -> bool:
    raise MlogInterpError(
        "mlog `write` (memory cell) not supported in v1 — "
        "the cell-free codegen rule means this opcode should not be emitted"
    )


_DISPATCH = {
    "set": _h_set,
    "op": _h_op,
    "jump": _h_jump,
    "print": _h_print,
    "printflush": _h_printflush,
    "wait": _h_wait,
    "sensor": _h_sensor,
    "getlink": _h_getlink,
    "control": _h_control,
    "read": _h_read,
    "write": _h_write,
}


__all__ = [
    "MlogInterpError",
    "MlogInterpreter",
]
