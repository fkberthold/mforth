"""Unit tests for the v2 dead-code-elimination pass (bead mforth-10t.36).

The DCE pass is a STANDALONE v2 optimizer (priority fast > small). It is
NOT wired into the default pipeline — bead mforth-10t.40 owns the -O
level wiring. These tests exercise it directly on a parsed + resolved
``Program``.

Two transforms:

(a) **Literal-flag IF/ELSE pruning.** When the term immediately before an
    ``IfThen`` is an already-present integer/float literal (NOT a folded
    one — composition with the .35 fold pass is .40's job), the branch is
    statically decided: a non-zero flag keeps ``then_body``, a zero flag
    keeps ``else_body``. The literal + the ``IfThen`` are both removed and
    the surviving branch is spliced in (recursively pruned). Net stack
    effect is preserved: ``flag IF ... THEN`` consumes the flag and runs
    the selected arm; the rewrite consumes nothing extra and runs the same
    arm, so depth in == depth out either way.

(b) **Unreachable-definition elimination.** Build a call graph rooted at
    ``main``; any ``Definition`` not transitively reachable from a
    ``WordCall`` in ``main`` is dropped from ``program.definitions`` (and
    from the dictionary, so codegen never sees it).

The headline guard is an EQUIVALENCE test: the pruned program must produce
an IDENTICAL host-REPL EventStream to the un-pruned program.
"""

from __future__ import annotations

import pytest

from mforth.dce import dead_code_eliminate
from mforth.dictionary import Definition, resolve, standard_dictionary
from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    Program,
    SrcLoc,
    WordCall,
)
from mforth.stackcheck import stackcheck


_LOC = SrcLoc("<test>", 1, 1)


def _word(name: str) -> WordCall:
    return WordCall(name=name, src_loc=_LOC)


def _lit(value: int) -> LitInt:
    return LitInt(value=value, src_loc=_LOC)


# ---------------------------------------------------------------------------
# (a) Literal-flag IF/ELSE pruning
# ---------------------------------------------------------------------------


def test_zero_flag_drops_entire_if_no_else() -> None:
    """``0 IF a THEN`` → nothing (flag false, no else arm)."""
    prog = Program(
        definitions=[],
        main=[
            _lit(0),
            IfThen(then_body=[_word("DUP")], else_body=[], src_loc=_LOC),
        ],
    )
    out = dead_code_eliminate(prog)
    assert out.main == []


def test_one_flag_keeps_then_drops_if() -> None:
    """``1 IF a ELSE b THEN`` → ``a`` (flag true keeps the then arm)."""
    prog = Program(
        definitions=[],
        main=[
            _lit(1),
            IfThen(
                then_body=[_word("DUP")],
                else_body=[_word("DROP")],
                src_loc=_LOC,
            ),
        ],
    )
    out = dead_code_eliminate(prog)
    assert out.main == [_word("DUP")]


def test_zero_flag_keeps_else_arm() -> None:
    """``0 IF a ELSE b THEN`` → ``b`` (flag false keeps the else arm)."""
    prog = Program(
        definitions=[],
        main=[
            _lit(0),
            IfThen(
                then_body=[_word("DUP")],
                else_body=[_word("DROP")],
                src_loc=_LOC,
            ),
        ],
    )
    out = dead_code_eliminate(prog)
    assert out.main == [_word("DROP")]


def test_nonzero_int_other_than_one_is_truthy() -> None:
    """Any non-zero integer flag is truthy (Forth convention)."""
    prog = Program(
        definitions=[],
        main=[
            _lit(7),
            IfThen(then_body=[_word("DUP")], else_body=[_word("DROP")], src_loc=_LOC),
        ],
    )
    out = dead_code_eliminate(prog)
    assert out.main == [_word("DUP")]


def test_float_zero_flag_is_falsy() -> None:
    """A float literal ``0.0`` flag is falsy → keep else arm."""
    prog = Program(
        definitions=[],
        main=[
            LitFloat(value=0.0, src_loc=_LOC),
            IfThen(then_body=[_word("DUP")], else_body=[_word("DROP")], src_loc=_LOC),
        ],
    )
    out = dead_code_eliminate(prog)
    assert out.main == [_word("DROP")]


def test_float_nonzero_flag_is_truthy() -> None:
    prog = Program(
        definitions=[],
        main=[
            LitFloat(value=2.5, src_loc=_LOC),
            IfThen(then_body=[_word("DUP")], else_body=[_word("DROP")], src_loc=_LOC),
        ],
    )
    out = dead_code_eliminate(prog)
    assert out.main == [_word("DUP")]


def test_non_literal_flag_is_left_untouched() -> None:
    """A runtime flag (a WordCall computing it) must NOT be pruned."""
    prog = Program(
        definitions=[],
        main=[
            _word("FLAG-COMPUTER"),
            IfThen(then_body=[_word("DUP")], else_body=[_word("DROP")], src_loc=_LOC),
        ],
    )
    out = dead_code_eliminate(prog)
    assert len(out.main) == 2
    assert isinstance(out.main[1], IfThen)


def test_if_with_no_preceding_literal_is_untouched() -> None:
    """An IF at the very start of a body (no preceding term) is left as-is —
    the flag was pushed by an earlier construct we can't see statically."""
    prog = Program(
        definitions=[],
        main=[
            IfThen(then_body=[_word("DUP")], else_body=[], src_loc=_LOC),
        ],
    )
    out = dead_code_eliminate(prog)
    assert len(out.main) == 1
    assert isinstance(out.main[0], IfThen)


def test_pruning_recurses_into_surviving_branch() -> None:
    """A literal-flag IF nested INSIDE the kept arm of an outer IF is also
    pruned (the rewrite is applied to the spliced-in branch)."""
    inner = [
        _lit(0),
        IfThen(then_body=[_word("DUP")], else_body=[_word("DROP")], src_loc=_LOC),
    ]
    prog = Program(
        definitions=[],
        main=[
            _lit(1),
            IfThen(then_body=inner, else_body=[_word("OVER")], src_loc=_LOC),
        ],
    )
    out = dead_code_eliminate(prog)
    # outer flag 1 → keep then (inner); inner flag 0 → keep else (DROP).
    assert out.main == [_word("DROP")]


def test_pruning_recurses_into_loop_bodies() -> None:
    """Literal-flag IFs inside DO/LOOP and BEGIN bodies are pruned too."""
    prog = Program(
        definitions=[],
        main=[
            _lit(2),
            _lit(0),
            DoLoop(
                body=[
                    _lit(1),
                    IfThen(
                        then_body=[_word("DUP")],
                        else_body=[_word("DROP")],
                        src_loc=_LOC,
                    ),
                ],
                src_loc=_LOC,
            ),
        ],
    )
    out = dead_code_eliminate(prog)
    loop = out.main[-1]
    assert isinstance(loop, DoLoop)
    assert loop.body == [_word("DUP")]


def test_pruning_recurses_into_definition_bodies() -> None:
    """Literal-flag IFs inside a (reachable) definition body are pruned."""
    defn = Definition(
        name="foo",
        body=[
            _lit(0),
            IfThen(then_body=[_word("DUP")], else_body=[_word("DROP")], src_loc=_LOC),
        ],
        src_loc=_LOC,
    )
    prog = Program(definitions=[defn], main=[_word("foo")])
    out = dead_code_eliminate(prog)
    kept = out.definitions[0]
    assert kept.body == [_word("DROP")]


# ---------------------------------------------------------------------------
# (b) Unreachable-definition elimination
# ---------------------------------------------------------------------------


def test_uncalled_definition_is_removed() -> None:
    used = Definition(name="used", body=[_word("DUP")], src_loc=_LOC)
    dead = Definition(name="dead", body=[_word("DROP")], src_loc=_LOC)
    prog = Program(definitions=[used, dead], main=[_word("used")])
    out = dead_code_eliminate(prog)
    names = {d.name for d in out.definitions}
    assert names == {"used"}


def test_transitively_reachable_definition_is_kept() -> None:
    """``main`` calls A; A calls B. Both are reachable; C is not."""
    a = Definition(name="a", body=[_word("b")], src_loc=_LOC)
    b = Definition(name="b", body=[_word("DUP")], src_loc=_LOC)
    c = Definition(name="c", body=[_word("DROP")], src_loc=_LOC)
    prog = Program(definitions=[a, b, c], main=[_word("a")])
    out = dead_code_eliminate(prog)
    names = {d.name for d in out.definitions}
    assert names == {"a", "b"}


def test_definition_called_only_from_pruned_branch_becomes_dead() -> None:
    """A definition referenced ONLY inside a statically-pruned IF branch is
    no longer reachable after the prune → it gets removed. This is the
    combined win the bead calls out: prune the branch, then the def the
    branch called drops out of the reachable set."""
    expensive = Definition(
        name="expensive", body=[_word("DUP")], src_loc=_LOC
    )
    prog = Program(
        definitions=[expensive],
        main=[
            _lit(0),
            IfThen(then_body=[_word("expensive")], else_body=[], src_loc=_LOC),
        ],
    )
    out = dead_code_eliminate(prog)
    assert out.main == []
    assert out.definitions == []


def test_definition_reachable_only_via_nested_control_flow() -> None:
    """Reachability sees through IF / BEGIN / DO bodies in main and defs."""
    helper = Definition(name="helper", body=[_word("DUP")], src_loc=_LOC)
    prog = Program(
        definitions=[helper],
        main=[
            _word("FLAG"),
            IfThen(
                then_body=[_word("helper")], else_body=[], src_loc=_LOC
            ),
        ],
    )
    out = dead_code_eliminate(prog)
    assert {d.name for d in out.definitions} == {"helper"}


def test_dictionary_dead_definition_removed_when_dict_provided() -> None:
    """When a dictionary is passed, dead definitions are dropped from it so
    codegen never resolves them."""
    used = Definition(name="used", body=[_word("DUP")], src_loc=_LOC)
    dead = Definition(name="dead", body=[_word("DROP")], src_loc=_LOC)
    prog = Program(definitions=[used, dead], main=[_word("used")])
    dictionary = resolve(prog, dictionary=standard_dictionary())
    assert "dead" in dictionary
    out = dead_code_eliminate(prog, dictionary=dictionary)
    assert "dead" not in dictionary
    assert "used" in dictionary
    # builtins untouched
    assert "DUP" in dictionary
    assert {d.name for d in out.definitions} == {"used"}


# ---------------------------------------------------------------------------
# Stack-validity: the transformed program must still stackcheck.
# ---------------------------------------------------------------------------


def test_transformed_program_passes_stackcheck() -> None:
    """A program with a pruned IF and a removed def still type-checks after
    DCE — the pass preserves static stack validity (briefing constraint)."""
    src_used = Definition(name="dup2", body=[_word("DUP")], src_loc=_LOC)
    dead = Definition(name="dead", body=[_word("DROP")], src_loc=_LOC)
    prog = Program(
        definitions=[src_used, dead],
        main=[
            _lit(5),  # value for dup2 to consume/produce
            _lit(1),
            IfThen(then_body=[_word("dup2")], else_body=[_word("DROP")], src_loc=_LOC),
            _word("DROP"),
            _word("DROP"),
        ],
    )
    out = dead_code_eliminate(prog)
    # Pruned: flag 1 keeps dup2 → main is [5, dup2, DROP, DROP]; dead removed.
    dictionary = resolve(out, dictionary=standard_dictionary())
    stackcheck(out, dictionary=dictionary)  # must not raise
    assert {d.name for d in out.definitions} == {"dup2"}


def test_pass_is_pure_does_not_mutate_input_program() -> None:
    """DCE returns a new Program; the caller's input is left intact (so the
    un-optimized program is still available for the equivalence comparison)."""
    if_node = IfThen(then_body=[_word("DUP")], else_body=[], src_loc=_LOC)
    main = [_lit(0), if_node]
    prog = Program(definitions=[], main=main)
    out = dead_code_eliminate(prog)
    assert prog.main == [_lit(0), if_node]  # input untouched
    assert out.main == []
    assert out is not prog


# ---------------------------------------------------------------------------
# EQUIVALENCE PRESERVATION — the headline guard (CLAUDE.md hard rule).
# ---------------------------------------------------------------------------


def _run_host(program: Program) -> list:
    """Run a Program through the host backend and return its EventStream.

    Mirrors the equivalence runner in tests/integration/test_equivalence.py:
    resolve + stackcheck, then execute against a fresh MockWorld via the
    Executor with the full primitive table.
    """
    from mforth.backend.host import Executor
    from mforth.backend.primitives import register_all
    from mforth.backend.world import MockWorld

    dictionary = resolve(program, dictionary=standard_dictionary())
    result = stackcheck(program, dictionary=dictionary)
    executor = Executor(world=MockWorld(), dictionary=dictionary)
    register_all(executor)
    executor.execute(result)
    return list(executor.world.events)


def _events_eq(a: list, b: list) -> bool:
    from dataclasses import fields, is_dataclass

    if len(a) != len(b):
        return False
    for ea, eb in zip(a, b):
        if type(ea) is not type(eb):
            return False
        if is_dataclass(ea) and is_dataclass(eb):
            for f in fields(ea):
                if f.name == "timestamp":
                    continue
                if getattr(ea, f.name) != getattr(eb, f.name):
                    return False
        elif ea != eb:
            return False
    return True


def test_equivalence_pruned_if_matches_unpruned_events() -> None:
    """HEADLINE: the DCE-optimized program yields an IDENTICAL host
    EventStream to the un-optimized program. Both are executed; events
    compared field-by-field (timestamp excluded).

    Program: ``1 IF 7 PRINT ELSE 9 PRINT THEN  display PRINTFLUSH`` —
    the un-optimized form runs the IF at runtime and prints "7"; the
    optimized form statically selects the then-arm. Same events.
    """
    def make() -> Program:
        return Program(
            definitions=[],
            main=[
                _lit(1),
                IfThen(
                    then_body=[_lit(7), _word("PRINT")],
                    else_body=[_lit(9), _word("PRINT")],
                    src_loc=_LOC,
                ),
            ],
        )

    unopt = make()
    opt = dead_code_eliminate(make())

    # The optimization must actually fire (else the test is vacuous).
    assert not any(isinstance(t, IfThen) for t in opt.main)

    events_unopt = _run_host(unopt)
    events_opt = _run_host(opt)
    assert _events_eq(events_unopt, events_opt), (
        f"DCE changed observable behavior!\n"
        f"unopt={events_unopt!r}\nopt={events_opt!r}"
    )


def test_equivalence_dead_def_removal_matches_events() -> None:
    """Removing an uncalled definition cannot change observable behavior:
    the def was never executed in either form. Equivalence holds and the
    instruction count (proxy: fewer definitions) strictly decreases."""
    used = Definition(
        name="emit7", body=[_lit(7), _word("PRINT")], src_loc=_LOC
    )
    dead = Definition(
        name="never", body=[_lit(99), _word("PRINT")], src_loc=_LOC
    )

    def make() -> Program:
        return Program(
            definitions=[
                Definition(name="emit7", body=[_lit(7), _word("PRINT")], src_loc=_LOC),
                Definition(name="never", body=[_lit(99), _word("PRINT")], src_loc=_LOC),
            ],
            main=[_word("emit7")],
        )

    unopt = make()
    opt = dead_code_eliminate(make())

    assert len(opt.definitions) < len(unopt.definitions)  # metric improved
    events_unopt = _run_host(unopt)
    events_opt = _run_host(opt)
    assert _events_eq(events_unopt, events_opt)
