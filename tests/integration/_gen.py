"""Hypothesis strategy that emits WELL-FORMED v1 mforth programs for the
generative REPL <-> mlog equivalence harness (bead mforth-2p8).

Why a stack-depth model
=======================

The headline mforth property (CLAUDE.md hard rule) is that the same ``.fs``
source produces an identical observable :class:`EventStream` whether run
through the host REPL or compiled-then-interpreted as mlog. To *generatively*
exercise that property we need a stream of programs that are guaranteed to:

* **type-check / stack-check** — every Forth word has a statically-known
  stack effect, and the stack-checker is a mandatory gate (it rejects any
  program that would underflow). So we generate against a *model* of the
  data-stack depth: a term is only emitted when the current modelled depth
  can satisfy its input arity, and the model is updated by the term's net
  effect afterward. This means we never produce a program that pops an
  empty stack.

* **terminate** — both backends run the program under mlog's auto-loop
  semantics for a fixed iteration count, so each *single* pass must halt.
  ``DO/LOOP`` ranges are bounded small and always count up; ``BEGIN/UNTIL``
  bodies are constructed to converge (a monotone counter that reaches the
  ``UNTIL`` flag in a bounded number of trips).

* **stay well under the per-processor instruction budget** — the term count
  is bounded and nesting depth is shallow, so the emitted mlog is far below
  the ~1000-instruction processor limit even after inlining.

Numeric pop-print sink
======================

Both ``.`` (pop-print) and ``PRINT`` are ``( n -- )`` numeric IO sinks: each
pops one value and funnels it through ``world.print`` → ``MessagePrintEvent``,
and the mlog backend lowers both to ``print s<i>`` (bead ``mforth-va2`` landed
the ``.`` emit, mirroring ``PRINT``'s slot form). They render identically —
integer-valued floats drop the trailing ``.0`` and bools render ``1``/``0`` on
both backends — so the generator uses them interchangeably wherever a single
numeric stack value must be consumed.

Generated subset
================

literals (int + float) · arithmetic ``+ - * /`` · stack ops
``DUP DROP SWAP OVER ROT`` · comparisons ``< > =`` · ``IF/ELSE/THEN`` ·
``BEGIN/UNTIL`` · ``DO/LOOP`` (with ``I``) · ``VARIABLE`` + ``@`` / ``!`` ·
``PRINT`` · ``.`` · ``S" ..."``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hypothesis import strategies as st


# A single message-block sidecar shared by every generated program. Mode A
# (``target``) so the equivalence runner's name_map handles the block-name
# binding exactly as it does for the hand-written fixtures.
SIDECAR_TOML = (
    "[links.display]\n"
    'type = "message"\n'
    'target = "message1"\n'
    "\n"
    "[clock]\n"
    "ipt = 8\n"
    "realtime = false\n"
)


# ---------------------------------------------------------------------------
# Generation model
# ---------------------------------------------------------------------------


@dataclass
class _GenState:
    """Mutable generation context threaded through the recursive builder.

    ``depth`` is the modelled data-stack height. ``variables`` is the set of
    already-declared VARIABLE names available for ``@`` / ``!``. ``budget``
    bounds the number of remaining terms so programs stay small and finite.
    """

    depth: int = 0
    variables: list[str] = field(default_factory=list)
    budget: int = 0


# Words that consume from / produce onto the stack, keyed by (in, out). We
# avoid ``/`` divisor==0 and ``MOD`` entirely by only ever dividing by a
# freshly-pushed nonzero literal (see ``_emit_binary``). Comparisons and
# arithmetic share the (2 -> 1) shape.
_STACK_OPS = {
    "DUP": (1, 2),
    "DROP": (1, 0),
    "SWAP": (2, 2),
    "OVER": (2, 3),
    "ROT": (3, 3),
}


def _int_literal(draw) -> str:
    return str(draw(st.integers(min_value=-50, max_value=50)))


def _float_literal(draw) -> str:
    # Keep magnitudes modest and avoid runaway precision; repr round-trips
    # cleanly through both the host (Python float) and mlog (set s<i> <repr>).
    f = draw(
        st.floats(
            min_value=-50.0,
            max_value=50.0,
            allow_nan=False,
            allow_infinity=False,
            width=16,
        )
    )
    # Quantise to 2 decimals so the printed forms are short and stable.
    return repr(round(f, 2))


def _nonzero_int_literal(draw) -> str:
    v = draw(st.integers(min_value=1, max_value=50))
    if draw(st.booleans()):
        v = -v
    return str(v)


def _string_literal(draw) -> str:
    # Alnum-only payloads — no quotes/newlines — so the S" lexer round-trips
    # and the printed text is identical on both backends.
    s = draw(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ",
            min_size=0,
            max_size=8,
        )
    )
    return f'S" {s}"'


def _emit_push(draw, state: _GenState) -> list[str]:
    """Emit one term that nets +1 on the stack (a literal)."""
    state.budget -= 1
    state.depth += 1
    choice = draw(st.integers(min_value=0, max_value=2))
    if choice == 0:
        return [_int_literal(draw)]
    if choice == 1:
        return [_float_literal(draw)]
    return [_nonzero_int_literal(draw)]


def _emit_binary(draw, state: _GenState) -> list[str]:
    """Emit an arithmetic or comparison op (2 -> 1).

    Requires depth >= 2. For ``/`` we guarantee a nonzero divisor by pushing
    a fresh nonzero literal immediately before the operator, so the program
    never produces inf/nan (whose stringification could legitimately differ
    and is out of scope for this harness).
    """
    op = draw(st.sampled_from(["+", "-", "*", "/", "<", ">", "="]))
    if op == "/":
        # depth stays >= 1 going in; push nonzero divisor then divide.
        state.budget -= 1
        toks = [_nonzero_int_literal(draw), "/"]
        # net: +1 (push) then 2->1 == net -1 overall on depth
        state.depth += 1  # the pushed divisor
        state.depth -= 1  # the 2->1 op
        return toks
    state.budget -= 1
    state.depth -= 1  # 2 in, 1 out
    return [op]


def _emit_stack_op(draw, state: _GenState) -> list[str]:
    """Emit a stack-shuffling op whose input arity the current depth meets."""
    candidates = [
        name for name, (i, _o) in _STACK_OPS.items() if state.depth >= i
    ]
    name = draw(st.sampled_from(candidates))
    i, o = _STACK_OPS[name]
    state.budget -= 1
    state.depth += o - i
    return [name]


def _emit_print(draw, state: _GenState) -> list[str]:
    """PRINT consumes one stack item (1 -> 0)."""
    state.budget -= 1
    state.depth -= 1
    return ["PRINT"]


def _emit_dot(draw, state: _GenState) -> list[str]:
    """``.`` consumes one numeric stack item and prints it (1 -> 0).

    Behaviourally identical to PRINT for the equivalence harness: both pop
    one value, funnel it through ``world.print`` → ``MessagePrintEvent``, and
    lower to ``print s<i>`` in the mlog backend (bead mforth-va2). Kept as a
    distinct emitter so the coverage guard can confirm ``.`` is exercised.
    """
    state.budget -= 1
    state.depth -= 1
    return ["."]


def _emit_numeric_sink(draw, state: _GenState) -> list[str]:
    """Consume one numeric stack item via either ``.`` or ``PRINT`` (1 -> 0).

    Picking between the two interchangeable numeric sinks here means every
    place that drains a value (top-level, IF/loop neutral blocks, the final
    stack drain) can emit ``.`` — so a bounded sweep reliably covers it.
    """
    if draw(st.booleans()):
        return _emit_dot(draw, state)
    return _emit_print(draw, state)


def _emit_string_print(draw, state: _GenState) -> list[str]:
    """S" ..." pushes a string; PRINT immediately consumes it (net 0)."""
    state.budget -= 1
    return [_string_literal(draw), "PRINT"]


def _emit_var_store(draw, state: _GenState) -> list[str]:
    """``<value> <var> !`` — needs one value on the stack already."""
    name = draw(st.sampled_from(state.variables))
    state.budget -= 1
    state.depth -= 1  # the value is consumed by `!`
    return [name, "!"]


def _emit_var_fetch(draw, state: _GenState) -> list[str]:
    """``<var> @`` — pushes the stored value (net +1)."""
    name = draw(st.sampled_from(state.variables))
    state.budget -= 1
    state.depth += 1
    return [name, "@"]


def _emit_if(draw, state: _GenState) -> list[str]:
    """``<flag> IF <then> ELSE <else> THEN`` — flag consumed; both arms
    must leave the SAME net depth (stackcheck gate). We make both arms
    stack-neutral so the post-IF depth equals the pre-IF depth minus the
    consumed flag.
    """
    state.budget -= 1
    state.depth -= 1  # flag consumed
    then_body = _gen_neutral_block(draw, state)
    else_body = _gen_neutral_block(draw, state)
    return ["IF", *then_body, "ELSE", *else_body, "THEN"]


def _emit_do_loop(draw, state: _GenState) -> list[str]:
    """``<limit> <start> DO <neutral-body> LOOP`` — bounded small ascending
    range, stack-neutral body. The two range literals are consumed by DO.
    """
    start = draw(st.integers(min_value=0, max_value=3))
    span = draw(st.integers(min_value=0, max_value=4))
    limit = start + span  # always >= start so the loop terminates
    state.budget -= 2
    body = _gen_neutral_block(draw, state, allow_index=True)
    return [str(limit), str(start), "DO", *body, "LOOP"]


def _emit_begin_until(draw, state: _GenState) -> list[str]:
    """``BEGIN <neutral-body> <converging-test> UNTIL``.

    The body is stack-neutral. The test is a self-contained converging
    counter: it reads a dedicated loop VARIABLE, increments it, stores it
    back, then compares against a fixed bound to produce the UNTIL flag.
    Because the counter strictly increases by 1 each trip and the bound is
    reached in a bounded number of trips, the loop always terminates. The
    counter is reset to 0 immediately before BEGIN so each program pass (and
    each auto-loop iteration) starts fresh.
    """
    # A dedicated, freshly-declared counter variable for this loop.
    name = f"__loop{len(state.variables)}"
    state.variables.append(name)
    bound = draw(st.integers(min_value=1, max_value=4))
    state.budget -= 1
    body = _gen_neutral_block(draw, state)
    # Prelude declares + zeroes the counter (net 0 on the data stack).
    prelude = ["VARIABLE", name, "0", name, "!"]
    # test: name @ 1 + DUP name ! <bound> >=   ... but >= isn't in the
    # allowed comparison subset (< > =). Use `>` against bound-1 so the
    # flag is produced by an allowed comparison. name @ -> v; 1 + -> v+1;
    # DUP -> v+1 v+1; name ! consumes one, stores; leaves v+1; bound-1 > ...
    # We want the test to net-produce exactly one flag (stackcheck UNTIL gate).
    test = [
        name, "@", "1", "+", "DUP", name, "!",  # leaves new value on stack
        str(bound - 1), ">",                       # value > bound-1  -> flag
    ]
    return [*prelude, "BEGIN", *body, *test, "UNTIL"]


# Map of neutral-or-pushing emitters usable inside a stack-neutral block.
def _gen_neutral_block(draw, state: _GenState, *, allow_index: bool = False) -> list[str]:
    """Generate a stack-NEUTRAL sequence (net depth change == 0).

    Used for IF arms, DO/LOOP bodies, and BEGIN/UNTIL bodies — all of which
    the stack-checker requires to be neutral (loops) or balanced across arms
    (IF). Strategy: a small number of (push, consume) pairs. Each pair leaves
    depth unchanged. ``allow_index`` permits ``I`` (the DO/LOOP counter) as a
    push source.
    """
    out: list[str] = []
    n_pairs = draw(st.integers(min_value=0, max_value=2))
    for _ in range(n_pairs):
        if state.budget <= 0:
            break
        # Push something...
        if allow_index and draw(st.booleans()):
            out.append("I")
            state.depth += 1
            state.budget -= 1
        else:
            out.extend(_emit_push(draw, state))
        # ...then consume it via a numeric sink (`.` or PRINT, 1 -> 0),
        # keeping the block neutral.
        if state.budget <= 0:
            # Still must consume to stay neutral.
            out.append("." if draw(st.booleans()) else "PRINT")
            state.depth -= 1
            break
        out.extend(_emit_numeric_sink(draw, state))
    return out


def _gen_term(draw, state: _GenState) -> list[str]:
    """Emit one top-level construct, respecting the current modelled depth."""
    # Build the menu of legal moves for the current state.
    options: list = []
    options.append(("push", 5))
    if state.depth >= 1:
        options.append(("print", 4))
        options.append(("dot", 4))
        options.append(("stack_op", 3))
    if state.depth >= 2:
        options.append(("binary", 4))
        options.append(("stack_op2", 2))
    if state.depth >= 3:
        options.append(("stack_op3", 1))
    options.append(("string_print", 3))
    options.append(("do_loop", 2))
    options.append(("begin_until", 1))
    if state.depth >= 1:
        options.append(("if", 2))
    if state.variables:
        options.append(("var_fetch", 2))
        if state.depth >= 1:
            options.append(("var_store", 2))

    kinds = [k for k, _w in options]
    weights = [w for _k, w in options]
    kind = draw(
        st.sampled_from(kinds)
        if not weights
        else st.one_of(*[st.just(k) for k in _weighted_expand(kinds, weights)])
    )

    if kind == "push":
        return _emit_push(draw, state)
    if kind == "print":
        return _emit_print(draw, state)
    if kind == "dot":
        return _emit_dot(draw, state)
    if kind in ("stack_op", "stack_op2", "stack_op3"):
        return _emit_stack_op(draw, state)
    if kind == "binary":
        return _emit_binary(draw, state)
    if kind == "string_print":
        return _emit_string_print(draw, state)
    if kind == "do_loop":
        return _emit_do_loop(draw, state)
    if kind == "begin_until":
        return _emit_begin_until(draw, state)
    if kind == "if":
        return _emit_if(draw, state)
    if kind == "var_fetch":
        return _emit_var_fetch(draw, state)
    if kind == "var_store":
        return _emit_var_store(draw, state)
    raise AssertionError(f"unhandled kind {kind!r}")


def _weighted_expand(kinds: list[str], weights: list[int]) -> list[str]:
    out: list[str] = []
    for k, w in zip(kinds, weights):
        out.extend([k] * w)
    return out


@st.composite
def mforth_program(draw) -> str:
    """Hypothesis strategy → a well-formed v1 mforth program source string.

    The returned program:

    * declares zero or more VARIABLEs up front (so ``@`` / ``!`` always
      reference a declared name);
    * emits a bounded sequence of stack-valid top-level constructs;
    * drains any residual stack with PRINTs so nothing is left dangling;
    * ends with ``display PRINTFLUSH`` (the Mode A sidecar message block).
    """
    state = _GenState(depth=0, variables=[], budget=draw(st.integers(8, 22)))

    lines: list[str] = []

    # Optionally declare a couple of user variables up front.
    n_vars = draw(st.integers(min_value=0, max_value=2))
    for k in range(n_vars):
        name = f"v{k}"
        state.variables.append(name)
        lines.append(f"VARIABLE {name}")
        # Seed it so a later @ has a defined value (mlog vars default null;
        # storing first keeps REPL/mlog read events aligned in value).
        lines.append(f"{draw(st.integers(-20, 20))} {name} !")

    # Body: emit terms until the budget runs out.
    while state.budget > 0:
        toks = _gen_term(draw, state)
        if toks:
            lines.append(" ".join(toks))

    # Drain the stack so nothing dangles (each `.`/PRINT is 1 -> 0).
    while state.depth > 0:
        lines.append("." if draw(st.booleans()) else "PRINT")
        state.depth -= 1

    lines.append("display PRINTFLUSH")
    return "\n".join(lines) + "\n"
