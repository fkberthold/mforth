"""Unit tests for the host REPL executor skeleton (bead mforth-10t.10).

The executor walks an annotated AST term-by-term against a persistent
state quadruple `(data_stack, return_stack, variables, world)`. State
persists across `.execute()` calls because the interactive REPL needs
to keep your `1 2 +` result around when you type `.` on the next line.

This bead ships the skeleton: term dispatcher, pluggable primitive
registry, two stub primitives (`+` and `.`) sufficient for the
acceptance test, and the `VARIABLE`/`@`/`!` triad needed for the
VarRef integration tests. Built-in primitives proper land in bead
mforth-10t.11/.12.
"""

from __future__ import annotations

import pytest

from mforth.backend.host import Executor, ExecutionError
from mforth.backend.world import (
    MessagePrintEvent,
    MockWorld,
    VariableReadEvent,
    VariableWriteEvent,
)
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
    VarRef,
    WordCall,
)
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def L(line: int = 1, col: int = 1) -> SrcLoc:
    return SrcLoc("<test>", line, col)


def make_executor(world: MockWorld | None = None) -> Executor:
    return Executor(world=world if world is not None else MockWorld())


def run_program(src_program: Program, world: MockWorld | None = None) -> Executor:
    result = stackcheck(src_program)
    ex = make_executor(world)
    ex.execute(result)
    return ex


# ---------------------------------------------------------------------------
# Acceptance test (from the bead text)
# ---------------------------------------------------------------------------


def test_acceptance_one_two_plus_dot_prints_three():
    """Hand-build the annotated AST for '1 2 + .' and confirm a hooked
    PRINT path observes '3' via the EventStream.

    This is the headline acceptance criterion in the bead.
    """
    program = Program(
        definitions=[],
        main=[
            LitInt(value=1, src_loc=L(1, 1)),
            LitInt(value=2, src_loc=L(1, 3)),
            WordCall(name="+", src_loc=L(1, 5)),
            WordCall(name=".", src_loc=L(1, 7)),
        ],
    )
    world = MockWorld()
    ex = run_program(program, world=world)

    assert ex.data_stack == []
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert len(prints) == 1
    assert prints[0].text == "3"


# ---------------------------------------------------------------------------
# Literal handling
# ---------------------------------------------------------------------------


def test_litint_pushes_value():
    program = Program(main=[LitInt(value=42, src_loc=L())])
    ex = run_program(program)
    assert ex.data_stack == [42]


def test_litfloat_pushes_python_float():
    # Host-side LitFloat (bead mforth-xk7) pushes a Python float —
    # already the runtime type used by SENSOR returns and `/` division.
    program = Program(main=[LitFloat(value=0.95, src_loc=L())])
    ex = run_program(program)
    assert ex.data_stack == [0.95]
    assert isinstance(ex.data_stack[0], float)


def test_litstr_pushes_string_handle():
    program = Program(main=[LitStr(value="hello", src_loc=L())])
    ex = run_program(program)
    assert ex.data_stack == ["hello"]


def test_multiple_literals_push_in_source_order():
    program = Program(
        main=[
            LitInt(value=1, src_loc=L()),
            LitInt(value=2, src_loc=L()),
            LitInt(value=3, src_loc=L()),
        ]
    )
    ex = run_program(program)
    assert ex.data_stack == [1, 2, 3]


# ---------------------------------------------------------------------------
# Empty program / empty body
# ---------------------------------------------------------------------------


def test_empty_program_runs_with_empty_stack():
    program = Program()
    ex = run_program(program)
    assert ex.data_stack == []
    assert ex.return_stack == []


def test_empty_if_then_branches():
    program = Program(
        main=[
            LitInt(value=1, src_loc=L()),
            IfThen(then_body=[], else_body=[], src_loc=L()),
        ]
    )
    ex = run_program(program)
    assert ex.data_stack == []


# ---------------------------------------------------------------------------
# Primitive registration + stub semantics
# ---------------------------------------------------------------------------


def test_unimplemented_primitive_raises_notimplementederror():
    program = Program(main=[WordCall(name="DUP", src_loc=L())])
    # DUP needs 1 on the stack to pass stackcheck — push first.
    program = Program(
        main=[LitInt(value=5, src_loc=L()), WordCall(name="DUP", src_loc=L())]
    )
    with pytest.raises(NotImplementedError) as exc:
        run_program(program)
    msg = str(exc.value)
    assert "DUP" in msg
    assert "10t.11" in msg or "10t.12" in msg


def test_custom_primitive_can_be_registered():
    """The executor exposes a `register_primitive(name, fn)` hook so that
    bead .11/.12 (and tests) can add primitives without monkey-patching."""
    program = Program(
        main=[
            LitInt(value=10, src_loc=L()),
            WordCall(name="DOUBLE", src_loc=L()),
        ]
    )
    # Pre-register DOUBLE in a custom dictionary entry so resolution succeeds.
    from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary

    d = standard_dictionary()
    d.add_builtin(BuiltinWord("DOUBLE", StackEffect(1, 1), "double top", "arith"))
    result = stackcheck(program, dictionary=d)

    ex = make_executor()

    def double(ex: Executor) -> None:
        ex.data_stack.append(ex.data_stack.pop() * 2)

    ex.register_primitive("DOUBLE", double)
    ex.execute(result)
    assert ex.data_stack == [20]


# ---------------------------------------------------------------------------
# IF/THEN/ELSE
# ---------------------------------------------------------------------------


def test_if_then_true_branch_runs():
    program = Program(
        main=[
            LitInt(value=1, src_loc=L()),
            IfThen(
                then_body=[LitInt(value=99, src_loc=L())],
                else_body=[LitInt(value=11, src_loc=L())],
                src_loc=L(),
            ),
        ]
    )
    ex = run_program(program)
    assert ex.data_stack == [99]


def test_if_then_false_branch_runs_when_flag_zero():
    program = Program(
        main=[
            LitInt(value=0, src_loc=L()),
            IfThen(
                then_body=[LitInt(value=99, src_loc=L())],
                else_body=[LitInt(value=11, src_loc=L())],
                src_loc=L(),
            ),
        ]
    )
    ex = run_program(program)
    assert ex.data_stack == [11]


def test_if_without_else_no_op_when_false():
    """Stack-balanced IF (both branches push exactly one value)."""
    program = Program(
        main=[
            LitInt(value=0, src_loc=L()),
            IfThen(
                then_body=[LitInt(value=42, src_loc=L())],
                else_body=[LitInt(value=0, src_loc=L())],
                src_loc=L(),
            ),
        ]
    )
    ex = run_program(program)
    # flag=0 → else branch → push 0
    assert ex.data_stack == [0]


def test_nested_if_then():
    # outer-flag=1 → enter outer-then; inner-flag=1 → push 7
    program = Program(
        main=[
            LitInt(value=1, src_loc=L()),
            IfThen(
                then_body=[
                    LitInt(value=1, src_loc=L()),
                    IfThen(
                        then_body=[LitInt(value=7, src_loc=L())],
                        else_body=[LitInt(value=8, src_loc=L())],
                        src_loc=L(),
                    ),
                ],
                else_body=[LitInt(value=9, src_loc=L())],
                src_loc=L(),
            ),
        ]
    )
    ex = run_program(program)
    assert ex.data_stack == [7]


# ---------------------------------------------------------------------------
# BEGIN / UNTIL
# ---------------------------------------------------------------------------


def test_begin_until_runs_body_once_when_immediately_true():
    """BEGIN body UNTIL — body executes, then flag pops; non-zero exits.

    Body: LitInt 1   (push truthy flag)
    UNTIL pops it; loop exits on first iteration.
    """
    program = Program(
        main=[
            Begin(
                body=[LitInt(value=1, src_loc=L())],
                kind="until",
                cond_body=[],
                src_loc=L(),
            ),
        ]
    )
    ex = run_program(program)
    assert ex.data_stack == []


def test_begin_until_with_external_counter_via_primitive():
    """A counter decremented by a custom primitive runs the loop N times."""
    from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary

    program = Program(
        main=[
            LitInt(value=3, src_loc=L()),  # counter
            Begin(
                # body: DEC-AND-TEST  ( n -- n-1 flag )  flag=1 iff n-1==0
                body=[WordCall(name="DEC-AND-TEST", src_loc=L())],
                kind="until",
                cond_body=[],
                src_loc=L(),
            ),
        ]
    )
    d = standard_dictionary()
    d.add_builtin(BuiltinWord("DEC-AND-TEST", StackEffect(1, 2), "", "arith"))
    result = stackcheck(program, dictionary=d)

    ex = make_executor()
    iter_count = [0]

    def dec_and_test(ex: Executor) -> None:
        iter_count[0] += 1
        n = ex.data_stack.pop() - 1
        ex.data_stack.append(n)
        ex.data_stack.append(1 if n == 0 else 0)

    ex.register_primitive("DEC-AND-TEST", dec_and_test)
    ex.execute(result)
    assert iter_count[0] == 3
    assert ex.data_stack == [0]  # the decremented counter


# ---------------------------------------------------------------------------
# BEGIN / WHILE / REPEAT
# ---------------------------------------------------------------------------


def test_begin_while_repeat_exits_when_test_false():
    """Test pushes 0 → WHILE exits immediately, body never runs."""
    program = Program(
        main=[
            Begin(
                body=[LitInt(value=0, src_loc=L())],  # the test
                kind="while-repeat",
                cond_body=[LitInt(value=99, src_loc=L())],  # body; never runs
                src_loc=L(),
            ),
        ]
    )
    # cond_body would push 99 each iter; stackcheck would catch non-neutral.
    # Use a stack-neutral body instead via a custom primitive.
    from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary

    program = Program(
        main=[
            Begin(
                body=[LitInt(value=0, src_loc=L())],
                kind="while-repeat",
                cond_body=[WordCall(name="NOOP", src_loc=L())],
                src_loc=L(),
            ),
        ]
    )
    d = standard_dictionary()
    d.add_builtin(BuiltinWord("NOOP", StackEffect(0, 0), "", "arith"))
    result = stackcheck(program, dictionary=d)
    ex = make_executor()
    runs = [0]
    ex.register_primitive("NOOP", lambda ex: runs.__setitem__(0, runs[0] + 1))
    ex.execute(result)
    assert runs[0] == 0


def test_begin_while_repeat_runs_body_three_times():
    """Counter starts at 3; WHILE test pops n and pushes (n,flag=(n>0));
    body decrements n. Loop runs while n>0 → 3 iterations.
    """
    from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary

    program = Program(
        main=[
            LitInt(value=3, src_loc=L()),
            Begin(
                body=[WordCall(name="TEST-POS", src_loc=L())],
                kind="while-repeat",
                cond_body=[WordCall(name="DEC", src_loc=L())],
                src_loc=L(),
            ),
        ]
    )
    d = standard_dictionary()
    # TEST-POS: ( n -- n flag )   flag = (n > 0)
    d.add_builtin(BuiltinWord("TEST-POS", StackEffect(1, 2), "", "arith"))
    # DEC: ( n -- n-1 )
    d.add_builtin(BuiltinWord("DEC", StackEffect(1, 1), "", "arith"))
    result = stackcheck(program, dictionary=d)
    ex = make_executor()

    def test_pos(ex: Executor) -> None:
        n = ex.data_stack[-1]
        ex.data_stack.append(1 if n > 0 else 0)

    def dec(ex: Executor) -> None:
        ex.data_stack.append(ex.data_stack.pop() - 1)

    ex.register_primitive("TEST-POS", test_pos)
    ex.register_primitive("DEC", dec)
    ex.execute(result)
    assert ex.data_stack == [0]


# ---------------------------------------------------------------------------
# DO / LOOP
# ---------------------------------------------------------------------------


def test_do_loop_iterates_from_index_to_limit_exclusive():
    """DO ( limit index -- ) ... LOOP runs index, index+1, ..., limit-1."""
    from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary

    program = Program(
        main=[
            LitInt(value=4, src_loc=L()),  # limit
            LitInt(value=0, src_loc=L()),  # index start
            DoLoop(
                body=[WordCall(name="TICK", src_loc=L())],
                src_loc=L(),
            ),
        ]
    )
    d = standard_dictionary()
    d.add_builtin(BuiltinWord("TICK", StackEffect(0, 0), "", "arith"))
    result = stackcheck(program, dictionary=d)
    ex = make_executor()
    indices: list[int] = []

    def tick(ex: Executor) -> None:
        # Current loop index lives on top of return stack.
        indices.append(ex.return_stack[-1])

    ex.register_primitive("TICK", tick)
    ex.execute(result)
    assert indices == [0, 1, 2, 3]
    # return_stack popped clean
    assert ex.return_stack == []


def test_do_loop_with_i_reads_index():
    """I primitive pushes current loop index onto data stack."""
    from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary

    program = Program(
        main=[
            LitInt(value=3, src_loc=L()),
            LitInt(value=0, src_loc=L()),
            DoLoop(
                body=[
                    WordCall(name="I", src_loc=L()),
                    WordCall(name="PUSH-AND-DROP", src_loc=L()),
                ],
                src_loc=L(),
            ),
        ]
    )
    d = standard_dictionary()
    # PUSH-AND-DROP just records value then drops it (stack-neutral with I's push).
    d.add_builtin(
        BuiltinWord("PUSH-AND-DROP", StackEffect(1, 0), "", "arith")
    )
    result = stackcheck(program, dictionary=d)
    ex = make_executor()
    seen: list[int] = []

    def push_and_drop(ex: Executor) -> None:
        seen.append(ex.data_stack.pop())

    ex.register_primitive("PUSH-AND-DROP", push_and_drop)
    ex.execute(result)
    assert seen == [0, 1, 2]


def test_nested_do_loop_with_j():
    """J reads outer loop index; I reads inner."""
    from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary

    program = Program(
        main=[
            LitInt(value=2, src_loc=L()),
            LitInt(value=0, src_loc=L()),
            DoLoop(
                body=[
                    LitInt(value=2, src_loc=L()),
                    LitInt(value=0, src_loc=L()),
                    DoLoop(
                        body=[
                            WordCall(name="J", src_loc=L()),
                            WordCall(name="I", src_loc=L()),
                            WordCall(name="RECORD-PAIR", src_loc=L()),
                        ],
                        src_loc=L(),
                    ),
                ],
                src_loc=L(),
            ),
        ]
    )
    d = standard_dictionary()
    d.add_builtin(BuiltinWord("RECORD-PAIR", StackEffect(2, 0), "", "arith"))
    result = stackcheck(program, dictionary=d)
    ex = make_executor()
    pairs: list[tuple[int, int]] = []

    def record(ex: Executor) -> None:
        i = ex.data_stack.pop()
        j = ex.data_stack.pop()
        pairs.append((j, i))

    ex.register_primitive("RECORD-PAIR", record)
    ex.execute(result)
    assert pairs == [(0, 0), (0, 1), (1, 0), (1, 1)]


# ---------------------------------------------------------------------------
# User definitions
# ---------------------------------------------------------------------------


def test_user_definition_inlined_at_call_site():
    """Calling a user definition runs its body in the caller's stack."""
    defn = Definition(
        name="TRIPLE-FIVE",
        body=[
            LitInt(value=5, src_loc=L()),
            LitInt(value=5, src_loc=L()),
            LitInt(value=5, src_loc=L()),
        ],
        src_loc=L(),
    )
    program = Program(
        definitions=[defn],
        main=[WordCall(name="TRIPLE-FIVE", src_loc=L())],
    )
    ex = run_program(program)
    assert ex.data_stack == [5, 5, 5]


def test_user_definition_calls_another_definition():
    """Definitions can call other definitions."""
    inner = Definition(
        name="ONE", body=[LitInt(value=1, src_loc=L())], src_loc=L()
    )
    outer = Definition(
        name="TWO-ONES",
        body=[
            WordCall(name="ONE", src_loc=L()),
            WordCall(name="ONE", src_loc=L()),
        ],
        src_loc=L(),
    )
    program = Program(
        definitions=[inner, outer],
        main=[WordCall(name="TWO-ONES", src_loc=L())],
    )
    ex = run_program(program)
    assert ex.data_stack == [1, 1]


# ---------------------------------------------------------------------------
# Variables — VARIABLE / @ / !
# ---------------------------------------------------------------------------


def test_variable_declaration_creates_address_pushable_entry():
    """`VARIABLE FOO` followed by `FOO` pushes the variable's address-handle.
    The executor treats the WordCall sequence `VARIABLE <name>` as a no-op
    at runtime (the dictionary registration happened at resolve time); the
    subsequent `WordCall("FOO")` pushes the variable name as its handle.
    """
    program = Program(
        main=[
            WordCall(name="VARIABLE", src_loc=L()),
            WordCall(name="FOO", src_loc=L()),
        ]
    )
    ex = run_program(program)
    # After VARIABLE FOO with no further reference, the runtime stack is empty.
    assert ex.data_stack == []


def test_variable_store_and_fetch_round_trip():
    """`42 FOO !` then `FOO @` leaves 42 on top.

    Sequence (parsed as plain WordCalls — the parser doesn't synthesise
    VarRef; that's deferred per the overnight decision drawer):

        VARIABLE FOO     ( declare )
        42 FOO !         ( store )
        FOO @            ( fetch )
    """
    program = Program(
        main=[
            WordCall(name="VARIABLE", src_loc=L()),
            WordCall(name="FOO", src_loc=L()),
            LitInt(value=42, src_loc=L()),
            WordCall(name="FOO", src_loc=L()),
            WordCall(name="!", src_loc=L()),
            WordCall(name="FOO", src_loc=L()),
            WordCall(name="@", src_loc=L()),
        ]
    )
    world = MockWorld()
    ex = run_program(program, world=world)
    assert ex.data_stack == [42]

    # Variable events fired through the world's EventStream.
    writes = [e for e in world.events if isinstance(e, VariableWriteEvent)]
    reads = [e for e in world.events if isinstance(e, VariableReadEvent)]
    assert any(e.name == "FOO" and e.value == 42.0 for e in writes)
    assert any(e.name == "FOO" and e.value == 42.0 for e in reads)


def test_explicit_varref_node_also_supported():
    """If a future pass synthesises VarRef nodes directly, the executor
    handles them too (fetch / store modes)."""
    program = Program(
        main=[
            WordCall(name="VARIABLE", src_loc=L()),
            WordCall(name="BAR", src_loc=L()),
            LitInt(value=7, src_loc=L()),
            VarRef(name="BAR", mode="store", src_loc=L()),
            VarRef(name="BAR", mode="fetch", src_loc=L()),
        ]
    )
    # VarRef("store") consumes 1 (value); VarRef("fetch") produces 1.
    # Skip stackcheck for the explicit-VarRef path since stackcheck doesn't
    # yet know about VarRef; build the executor with a manually-prepared
    # StackcheckResult.
    from mforth.stackcheck import StackcheckResult
    from mforth.dictionary import resolve

    d = resolve(program)
    # Drive a fake StackcheckResult with empty depth annotations — the
    # executor doesn't rely on per-term depths at runtime.
    result = StackcheckResult(program=program, effects={}, dictionary=d)

    ex = make_executor()
    ex.execute(result)
    assert ex.data_stack == [7]


# ---------------------------------------------------------------------------
# State persistence across .execute() calls
# ---------------------------------------------------------------------------


def test_state_persists_across_two_execute_calls():
    """The interactive REPL types `1 2` then later `+ .` — the second
    execute must see the 1 and 2 left by the first."""
    first = Program(
        main=[
            LitInt(value=1, src_loc=L()),
            LitInt(value=2, src_loc=L()),
        ]
    )
    second = Program(
        main=[
            WordCall(name="+", src_loc=L()),
            WordCall(name=".", src_loc=L()),
        ]
    )
    world = MockWorld()
    ex = make_executor(world=world)
    # Each program must be stackchecked with the *current* depth-in as 0
    # because stackcheck assumes main starts at depth 0. The executor
    # carries state through; the stack-checker checks each fragment in
    # isolation. The second fragment's `+` would underflow under strict
    # static checking, so we relax checking for the second fragment by
    # using a custom StackcheckResult with effects pre-populated.
    from mforth.dictionary import resolve
    from mforth.stackcheck import StackcheckResult

    # First fragment: normal stackcheck.
    ex.execute(stackcheck(first))
    assert ex.data_stack == [1, 2]

    # Second fragment: bypass stackcheck (REPL mode).
    d = resolve(second)
    ex.execute(StackcheckResult(program=second, effects={}, dictionary=d))
    assert ex.data_stack == []
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert prints and prints[-1].text == "3"


def test_execute_can_be_called_with_only_definitions():
    """A program that only adds definitions (no main terms) leaves the
    runtime stack untouched and registers the definitions for later
    execute() calls."""
    defn = Definition(
        name="FIVE", body=[LitInt(value=5, src_loc=L())], src_loc=L()
    )
    first = Program(definitions=[defn], main=[])
    second = Program(main=[WordCall(name="FIVE", src_loc=L())])
    ex = make_executor()
    ex.execute(stackcheck(first))
    assert ex.data_stack == []
    # Second fragment must see FIVE in its dictionary.
    ex.execute(stackcheck(second, dictionary=ex.dictionary))
    assert ex.data_stack == [5]


# ---------------------------------------------------------------------------
# Error surfaces
# ---------------------------------------------------------------------------


def test_runtime_underflow_raises_execution_error_with_src_loc():
    """If an unexpected primitive pops from an empty stack at runtime
    (something stackcheck didn't catch — e.g. via a custom primitive that
    misbehaves), the executor wraps the IndexError into ExecutionError.
    """
    from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary

    program = Program(
        main=[WordCall(name="POP-ANYWAY", src_loc=L(7, 3))],
    )
    d = standard_dictionary()
    d.add_builtin(
        BuiltinWord("POP-ANYWAY", StackEffect(0, 0), "", "arith")
    )
    from mforth.stackcheck import StackcheckResult

    result = StackcheckResult(program=program, effects={}, dictionary=d)
    ex = make_executor()
    ex.register_primitive("POP-ANYWAY", lambda ex: ex.data_stack.pop())
    with pytest.raises(ExecutionError) as exc:
        ex.execute(result)
    assert exc.value.src_loc.line == 7
    assert exc.value.src_loc.col == 3
