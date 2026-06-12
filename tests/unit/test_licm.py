"""Unit + equivalence tests for the loop-invariant code motion pass
(bead mforth-10t.38).

LICM is a v2 ``-Ofast`` Tier-B optimization. It hoists loop-invariant,
side-effect-free Term runs out of ``DO/LOOP`` and ``BEGIN`` bodies so
they execute once instead of every iteration. The canonical case where
*fast > small* actually matters: per-iteration savings compound.

This pass is **standalone** — it is not wired into the default pipeline
(``mforth-10t.40`` wires the ``-O`` levels). These tests drive it
directly.

The headline guarantee (CLAUDE.md hard rule) is that the transformed
program produces an **identical EventStream** to the original. Because
the only runs LICM lifts are provably pure (literals + arithmetic /
stack ops, no ``I``/``J``, no variables, no IO / Mindustry side
effects), they emit no events at all — so whether they run once or N
times, the observable behaviour is byte-for-byte unchanged. The
equivalence test below proves this by compiling both forms and running
them through the in-repo mlog interpreter, and by running both ASTs
through the host backend.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

import pytest

from mforth.backend.host import Executor
from mforth.backend.primitives import register_all
from mforth.backend.world import MockWorld
from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.finalize import finalize
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.sidecar import WorldConfig
from mforth.dictionary import resolve, standard_dictionary
from mforth.licm import licm
from mforth.mlog_interp import MlogInterpreter
from mforth.parse import (
    Begin,
    DoLoop,
    LitFloat,
    LitInt,
    WordCall,
    parse,
)
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_host_events(program) -> list:
    """Execute an AST ``Program`` once through the host backend and return
    its event list."""
    dictionary = resolve(program, dictionary=standard_dictionary())
    result = stackcheck(program, dictionary=dictionary)
    world = MockWorld()
    executor = Executor(world=world, dictionary=dictionary)
    register_all(executor)
    executor.execute(result)
    return list(world.events)


def _compile_and_count(program, *, iterations: int = 1):
    """Compile an AST ``Program`` to mlog, run it through the interpreter
    for ``iterations`` passes, and return ``(events, executed_instr)``.

    ``executed_instr`` counts every dispatched instruction — the dynamic
    metric the bead's acceptance criterion ("executed-instructions-per-tick
    drops by >=30%") is stated in.
    """
    dictionary = resolve(program, dictionary=standard_dictionary())
    result = stackcheck(program, dictionary=dictionary)
    slots = allocate_slots(result)
    instrs = emit(result, slots)
    text = finalize(
        instrs, world_config=WorldConfig(), source_path="<licm-test>"
    )
    world = MockWorld()
    interp = MlogInterpreter(world=world, text=text)

    counter = {"n": 0}
    original_dispatch = interp._dispatch

    def counting_dispatch(opcode, operands):
        counter["n"] += 1
        return original_dispatch(opcode, operands)

    interp._dispatch = counting_dispatch  # type: ignore[method-assign]
    interp.run(iterations=iterations)
    return list(world.events), counter["n"]


def _payload_eq(a, b) -> bool:
    if type(a) is not type(b):
        return False
    if not (is_dataclass(a) and is_dataclass(b)):
        return a == b
    for f in fields(a):
        if f.name == "timestamp":
            continue
        if getattr(a, f.name) != getattr(b, f.name):
            return False
    return True


def _events_equal(xs: list, ys: list) -> bool:
    return len(xs) == len(ys) and all(
        _payload_eq(x, y) for x, y in zip(xs, ys)
    )


def _count_leaf_terms(terms: list) -> int:
    """Count literal + WordCall leaves recursively across nested loop /
    branch bodies — a structural proxy for in-loop instruction pressure."""
    total = 0
    for t in terms:
        if isinstance(t, (LitInt, LitFloat, WordCall)):
            total += 1
        elif isinstance(t, DoLoop):
            total += _count_leaf_terms(t.body)
        elif isinstance(t, Begin):
            total += _count_leaf_terms(t.body) + _count_leaf_terms(t.cond_body)
    return total


# ---------------------------------------------------------------------------
# Structural tests — the pass identifies and lifts invariant runs
# ---------------------------------------------------------------------------


def test_invariant_run_is_hoisted_out_of_do_loop_body():
    """A pure invariant run inside a DO/LOOP body gets shortened: the
    in-loop term count drops because the invariant computation is
    pre-evaluated / hoisted."""
    program = parse("3 0 DO 10 20 + 5 * PRINT LOOP", file="<t>")
    do = program.main[2]
    assert isinstance(do, DoLoop)
    before = _count_leaf_terms(do.body)

    out = licm(program)
    out_do = out.main[2]
    assert isinstance(out_do, DoLoop)
    after = _count_leaf_terms(out_do.body)

    # The invariant 5-term expression `10 20 + 5 *` collapsed; PRINT
    # (the consumer, which has a side effect) stays in the loop.
    assert after < before


def test_begin_until_invariant_run_is_hoisted():
    """LICM also fires inside BEGIN/UNTIL bodies, not just DO/LOOP."""
    program = parse(
        ": demo 0 BEGIN 100 50 - PRINT 1 + DUP 3 >= UNTIL ; demo",
        file="<t>",
    )
    out = licm(program)
    demo_before = program.definitions[0]
    demo_after = out.definitions[0]
    begin_before = next(t for t in demo_before.body if isinstance(t, Begin))
    begin_after = next(t for t in demo_after.body if isinstance(t, Begin))
    assert _count_leaf_terms(begin_after.body) < _count_leaf_terms(
        begin_before.body
    )


# ---------------------------------------------------------------------------
# NEGATIVE tests — conservative: never hoist counter- or side-effect-
# dependent runs.
# ---------------------------------------------------------------------------


def test_does_not_hoist_expression_depending_on_loop_counter_I():
    """A run that reads the loop counter ``I`` is NOT loop-invariant and
    must be left in place."""
    program = parse("3 0 DO I 10 + PRINT LOOP", file="<t>")
    before = _count_leaf_terms(program.main[2].body)
    out = licm(program)
    after = _count_leaf_terms(out.main[2].body)
    assert after == before  # nothing hoisted


def test_does_not_hoist_run_with_side_effect_PRINT():
    """A run containing a side-effecting word (PRINT) must NOT be
    hoisted — even though its inputs are constant, executing it once
    would drop print events from every later iteration."""
    program = parse("3 0 DO 42 PRINT LOOP", file="<t>")
    before = _count_leaf_terms(program.main[2].body)
    out = licm(program)
    after = _count_leaf_terms(out.main[2].body)
    assert after == before


def test_does_not_hoist_variable_fetch():
    """A run that fetches a VARIABLE (`@`) reads mutable state and must
    not be hoisted (the variable could be written elsewhere in the
    loop / iteration)."""
    program = parse(
        "VARIABLE v 5 v ! 3 0 DO v @ 1 + PRINT LOOP", file="<t>"
    )
    do = next(t for t in program.main if isinstance(t, DoLoop))
    before = _count_leaf_terms(do.body)
    out = licm(program)
    out_do = next(t for t in out.main if isinstance(t, DoLoop))
    assert _count_leaf_terms(out_do.body) == before


# ---------------------------------------------------------------------------
# EQUIVALENCE — the non-negotiable property: optimized events == original
# events, on BOTH backends, AND the dynamic metric improves.
# ---------------------------------------------------------------------------


def test_licm_preserves_events_host_backend():
    """Host backend: optimized AST yields an identical EventStream."""
    program = parse("3 0 DO 10 20 + 5 * PRINT LOOP", file="<t>")
    out = licm(program)
    assert _events_equal(_run_host_events(program), _run_host_events(out))


def test_licm_preserves_events_and_cuts_executed_instructions():
    """The headline equivalence-+-improvement assertion: compiled
    optimized program emits the SAME events as the compiled original
    AND executes >=30% fewer instructions per program tick."""
    program = parse(
        "10 0 DO 100 50 - 2 * 3 + PRINT LOOP", file="<t>"
    )
    out = licm(program)

    base_events, base_count = _compile_and_count(program, iterations=1)
    opt_events, opt_count = _compile_and_count(out, iterations=1)

    assert _events_equal(base_events, opt_events)
    assert opt_count < base_count
    reduction = (base_count - opt_count) / base_count
    assert reduction >= 0.30, (
        f"executed-instruction reduction {reduction:.0%} < 30% "
        f"(base={base_count}, opt={opt_count})"
    )
