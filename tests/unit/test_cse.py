"""Common subexpression elimination (CSE) — bead mforth-10t.37.

A v2 Tier-B optimizer pass (priority fast > small). Within a single
Definition body (one basic block — the linear top-level term sequence,
NOT descending into IF / loop bodies, which are separate blocks),
per-block value numbering recognizes when the same *value* is produced
twice and reuses the first result instead of recomputing it.

What CSE may reuse
==================

The eliminable class is **event-free pure** value-producing terms:

* literals (``LitInt`` / ``LitFloat`` / ``LitStr``),
* arithmetic / comparison / logical builtins
  (``+ - * / MOD = <> < > <= >= AND OR NOT``),
* a variable *fetch* ``n @`` keyed on the variable name — the canonical
  Forth idiom the bead targets (``n @ 1 + n @ 2 +`` reuses ``n @``).

Side-effecting / event-emitting terms — ``!`` (store), ``PRINT`` / ``.``,
``PRINTFLUSH``, ``WAIT``, ``SENSOR``, ``GETLINK``, ``CONTROL-*`` —
INVALIDATE the value table (a store to ``n`` makes a later ``n @``
produce a *new* value, so the prior value number is no longer valid).
They are never themselves CSE targets.

Equivalence preservation (the non-negotiable)
=============================================

CSE must not change observable behaviour. The pure-arithmetic
elimination is proven event-IDENTICAL: ``test_cse_preserves_event_stream``
runs the un-optimized and CSE-optimized programs through the SAME host
backend and asserts the EventStream is byte-for-byte identical while the
term count drops.

The ``n @`` reuse intentionally elides the *redundant* second
``VariableReadEvent`` — that read is, per the REPL ↔ mlog convergence
drawer, a simulator instrumentation artifact mirroring a no-op variable
read in real mlog; eliding a provably-redundant read changes nothing
in-game. The observable *sink* events (PRINT output) stay identical,
which the ``@``-reuse test asserts explicitly.

Standalone / do-not-wire (bead mforth-10t.40 wires the -O levels): this
module is intentionally dead code in the default pipeline. The tests
drive it directly.
"""

from __future__ import annotations

from mforth.backend.host import Executor
from mforth.backend.primitives import register_all
from mforth.backend.world import (
    MessagePrintEvent,
    MockWorld,
    VariableReadEvent,
)
from mforth.cse import cse_definition, cse_program, cse_terms
from mforth.dictionary import resolve, standard_dictionary
from mforth.parse import (
    Definition,
    LitInt,
    Program,
    WordCall,
    parse,
)
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers — run a Program through the host backend, return its events.
# ---------------------------------------------------------------------------


def _run(program: Program) -> list:
    """Resolve + stackcheck + execute ``program`` via the host backend.

    Returns the MockWorld event list. A fresh dictionary + executor are
    built each call so the two runs in an equivalence assertion never
    share variable state.
    """
    dictionary = resolve(program, dictionary=standard_dictionary())
    result = stackcheck(program, dictionary=dictionary)
    world = MockWorld()
    executor = Executor(world=world, dictionary=dictionary)
    register_all(executor)
    executor.execute(result)
    return list(world.events)


def _count(terms: list, name: str) -> int:
    return sum(
        1 for t in terms if isinstance(t, WordCall) and t.name == name
    )


# ---------------------------------------------------------------------------
# The headline acceptance: 'n @ 1 + n @ 2 +' reuses 'n @'.
# ---------------------------------------------------------------------------


def test_cse_reuses_repeated_fetch() -> None:
    """``n @ 1 + n @ 2 +`` — the second ``n @`` is reused, not recomputed.

    Structural check: the optimized body contains exactly ONE ``@``
    WordCall (the first fetch); the second is replaced by stack reuse.
    """
    src = "VARIABLE n  : f  n @ 1 + n @ 2 + ;"
    program = parse(src, file="<cse>")
    dictionary = resolve(program, dictionary=standard_dictionary())

    defn = next(d for d in program.definitions if d.name == "f")
    assert _count(defn.body, "@") == 2  # baseline: two fetches

    optimized = cse_definition(defn, dictionary)
    assert _count(optimized.body, "@") == 1, (
        "CSE must reuse the first 'n @' rather than emit a second fetch"
    )


def test_cse_fetch_reuse_preserves_stack_result_and_sink_events() -> None:
    """The ``n @`` reuse keeps the program's observable sink (PRINT)
    output identical and stays stack-valid.

    Program: ``42 n !  n @ 1 +  n @ 2 +  . .`` — store 42, compute
    (n+1)=43 and (n+2)=44, print both. The optimized form must print the
    same two values; the only event difference is the elided *redundant*
    second VariableReadEvent.
    """
    src = (
        "VARIABLE n\n"
        ": f  n @ 1 +  n @ 2 +  . . ;\n"
        "42 n !\n"
        "f\n"
    )
    program = parse(src, file="<cse>")
    dictionary = resolve(program, dictionary=standard_dictionary())

    base_events = _run(program)

    # Apply CSE to definition f, splice it back, re-run.
    opt_program = cse_program(program, dictionary)
    opt_events = _run(opt_program)

    base_prints = [e for e in base_events if isinstance(e, MessagePrintEvent)]
    opt_prints = [e for e in opt_events if isinstance(e, MessagePrintEvent)]
    assert [e.text for e in base_prints] == [e.text for e in opt_prints]
    # f reads n twice in the baseline; CSE elides the redundant read.
    base_reads = sum(
        1 for e in base_events if isinstance(e, VariableReadEvent)
    )
    opt_reads = sum(
        1 for e in opt_events if isinstance(e, VariableReadEvent)
    )
    assert opt_reads == base_reads - 1


# ---------------------------------------------------------------------------
# NEGATIVE test (bead-mandated): a side effect between two occurrences
# correctly PREVENTS reuse.
# ---------------------------------------------------------------------------


def test_cse_store_between_fetches_blocks_reuse() -> None:
    """``n @ ... n ! ... n @`` — the intervening ``!`` invalidates the
    first fetch's value number, so the second ``n @`` is NOT reused.

    Body: ``n @ DROP  99 n !  n @`` — fetch, discard, store a new value,
    fetch again. The second fetch MUST survive (it reads the freshly
    stored value); eliding it would be a correctness bug.
    """
    src = "VARIABLE n  : f  n @ DROP  99 n !  n @ ;"
    program = parse(src, file="<cse>")
    dictionary = resolve(program, dictionary=standard_dictionary())

    defn = next(d for d in program.definitions if d.name == "f")
    optimized = cse_definition(defn, dictionary)
    assert _count(optimized.body, "@") == 2, (
        "an intervening '!' must invalidate the fetch — both 'n @' survive"
    )


def test_cse_print_between_pure_exprs_does_not_block_pure_reuse() -> None:
    """A side-effecting term between two *pure* recomputations does not
    block reuse of a constant-only expression (PRINT cannot change a
    literal value). But a store to a fetched variable DOES — covered by
    the negative test above. This pins that the invalidator set is
    scoped to memory/world state, not the whole table.
    """
    # 3 4 + . 3 4 +  — the print can't change 3 4 +, so reuse is sound.
    src = ": f  3 4 +  .  3 4 + ;"
    program = parse(src, file="<cse>")
    dictionary = resolve(program, dictionary=standard_dictionary())
    defn = next(d for d in program.definitions if d.name == "f")
    optimized = cse_definition(defn, dictionary)
    # The second '+' is elided (its inputs 3,4 are reused, result reused).
    assert _count(optimized.body, "+") == 1


# ---------------------------------------------------------------------------
# EQUIVALENCE PRESERVATION (the hard rule): the CSE-optimized program
# yields a byte-identical EventStream to the un-optimized one, executed
# through the same host backend — AND the term count strictly drops.
# ---------------------------------------------------------------------------


def test_cse_preserves_event_stream() -> None:
    """Pure-arithmetic CSE: identical EventStream, fewer terms.

    Program (event-free recompute): ``: f  5 3 +  DUP .  5 3 +  . ;``
    The two ``5 3 +`` are the same pure value; CSE reuses the first.
    Arithmetic + literals emit NO events, so the PRINT-only event stream
    is provably identical between un-optimized and optimized runs.
    """
    src = ": f  5 3 +  DUP .  5 3 +  . ;  f"
    program = parse(src, file="<cse>")
    dictionary = resolve(program, dictionary=standard_dictionary())

    defn = next(d for d in program.definitions if d.name == "f")
    base_terms = list(defn.body)
    opt_defn = cse_definition(defn, dictionary)

    # Metric improved: optimized body has strictly fewer terms.
    assert len(opt_defn.body) < len(base_terms)

    base_events = _run(program)
    opt_program = cse_program(program, dictionary)
    opt_events = _run(opt_program)

    assert len(base_events) == len(opt_events)
    for a, b in zip(base_events, opt_events):
        assert type(a) is type(b)
        assert a == b  # frozen dataclasses → field-wise equality


def test_cse_preserves_compiled_mlog_event_stream() -> None:
    """Headline class (REPL ↔ mlog): the CSE-optimized program, compiled
    to mlog and run through the in-repo interpreter, yields the SAME
    EventStream as the un-optimized program compiled+run.

    Uses the pure-arithmetic case so the (PRINT-only) stream is provably
    identical. This is the strict equivalence proof the v2 design drawer
    requires of every optimization pass: the optimized path preserves
    observable behaviour on BOTH backends.
    """
    from mforth.backend.mlog.emit import emit
    from mforth.backend.mlog.finalize import finalize
    from mforth.backend.mlog.slots import allocate_slots
    from mforth.backend.sidecar import WorldConfig
    from mforth.backend.world import MockWorld
    from mforth.mlog_interp import MlogInterpreter

    def compile_and_run(prog: Program) -> list:
        d = resolve(prog, dictionary=standard_dictionary())
        result = stackcheck(prog, dictionary=d)
        slots = allocate_slots(result)
        instrs = emit(result, slots)
        text = finalize(
            instrs, world_config=WorldConfig(), source_path="<cse>"
        )
        world = MockWorld()
        MlogInterpreter(world=world, text=text).run(iterations=1)
        return list(world.events)

    src = ": f  5 3 +  DUP .  5 3 +  . ;  f"
    program = parse(src, file="<cse>")
    dictionary = resolve(program, dictionary=standard_dictionary())
    opt_program = cse_program(program, dictionary)

    base_events = compile_and_run(program)
    opt_events = compile_and_run(opt_program)

    assert len(base_events) == len(opt_events)
    for a, b in zip(base_events, opt_events):
        assert type(a) is type(b)
        assert a == b


def test_cse_terms_is_a_noop_on_empty_and_single_term() -> None:
    """Degenerate inputs: empty and single-term bodies are returned
    unchanged (no spurious rewrites)."""
    dictionary = standard_dictionary()
    assert cse_terms([], dictionary) == []
    one = [LitInt(value=7, src_loc=_loc())]
    out = cse_terms(one, dictionary)
    assert len(out) == 1


def test_cse_does_not_descend_into_control_flow_blocks() -> None:
    """v1 CSE is single-basic-block: it must not reach into IF / loop
    bodies (those are separate blocks). A definition whose body is a
    single IfThen is returned structurally unchanged at the top level.
    """
    src = ": f  1 IF  2 3 +  2 3 +  THEN ;"
    program = parse(src, file="<cse>")
    dictionary = resolve(program, dictionary=standard_dictionary())
    defn = next(d for d in program.definitions if d.name == "f")
    optimized = cse_definition(defn, dictionary)
    # Top-level term count unchanged: the IfThen is one term either way;
    # CSE does not optimize across / into the branch in v1.
    assert len(optimized.body) == len(defn.body)


# ---------------------------------------------------------------------------
# tiny helper
# ---------------------------------------------------------------------------


def _loc():
    from mforth.parse import SrcLoc

    return SrcLoc("<cse>", 1, 1)
