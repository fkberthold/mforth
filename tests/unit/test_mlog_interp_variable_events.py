"""Unit tests for MlogInterpreter Variable-event emission (mforth-0qi).

The mlog interpreter must emit ``VariableReadEvent`` / ``VariableWriteEvent``
when reading or writing a mlog variable that corresponds to a Forth
``VARIABLE`` declaration in the source — matching the host REPL's
``world.read_variable`` / ``world.write_variable`` instrumentation.

Without this parity the REPL ↔ mlog equivalence property (CLAUDE.md
headline test class) fails on every program touching a VARIABLE.

The interpreter discriminates user-variable names via a
``user_variables`` constructor parameter (a ``set[str]`` of mforth
variable names from the dictionary). Reads/writes of any name NOT in
that set — ``s<i>`` stack slots, ``@``-prefixed magic vars, ``__swap_tmp``
and other compiler-internal scratch — emit NO Variable events,
matching the REPL's "magic and slot variables don't get instrumented"
contract.
"""

from __future__ import annotations

from mforth.backend.world import (
    MessagePrintEvent,
    MockWorld,
    VariableReadEvent,
    VariableWriteEvent,
)
from mforth.mlog_interp import MlogInterpreter


def _run(text: str, *, user_variables=None, iterations: int = 1):
    """Build an interpreter with the supplied user-variable set and run."""
    world = MockWorld()
    interp = MlogInterpreter(
        world=world, text=text, user_variables=user_variables or set()
    )
    interp.run(iterations=iterations)
    return world


def test_set_to_user_variable_emits_write_event():
    """`set counter 5` where `counter` is in user_variables must emit
    VariableWriteEvent(name='counter', value=5)."""
    text = "# header\nset counter 5\nend\n"
    world = _run(text, user_variables={"counter"})
    writes = [e for e in world.events if isinstance(e, VariableWriteEvent)]
    assert len(writes) == 1
    assert writes[0].name == "counter"
    assert writes[0].value == 5.0


def test_set_from_user_variable_emits_read_event():
    """`set s0 counter` (load counter into stack slot) must emit
    VariableReadEvent for `counter`."""
    text = "# header\nset counter 3\nset s0 counter\nend\n"
    world = _run(text, user_variables={"counter"})
    reads = [e for e in world.events if isinstance(e, VariableReadEvent)]
    assert len(reads) == 1
    assert reads[0].name == "counter"
    assert reads[0].value == 3.0


def test_set_between_stack_slots_emits_no_variable_events():
    """`set s1 s0` is pure stack-slot traffic — must emit NO Variable
    events even though both s0 and s1 are 'variables' inside the
    interpreter's variable dict."""
    text = "# header\nset s0 7\nset s1 s0\nend\n"
    world = _run(text, user_variables={"counter"})
    var_events = [
        e for e in world.events
        if isinstance(e, (VariableReadEvent, VariableWriteEvent))
    ]
    assert var_events == []


def test_op_writing_user_variable_emits_write_event():
    """`op add counter counter 1` (the classic increment) reads counter,
    writes counter — must emit ONE read + ONE write event."""
    text = (
        "# header\n"
        "set counter 4\n"
        "op add counter counter 1\n"
        "end\n"
    )
    world = _run(text, user_variables={"counter"})
    reads = [e for e in world.events if isinstance(e, VariableReadEvent)]
    writes = [e for e in world.events if isinstance(e, VariableWriteEvent)]
    # Two writes: the initial `set counter 4` AND the op add. One read:
    # the op add's source operand.
    assert len(writes) == 2
    assert writes[0].name == "counter" and writes[0].value == 4.0
    assert writes[1].name == "counter" and writes[1].value == 5.0
    assert len(reads) == 1
    assert reads[0].name == "counter" and reads[0].value == 4.0


def test_op_on_stack_slots_emits_no_variable_events():
    """`op add s2 s0 s1` is pure stack arithmetic — must emit NO Variable
    events."""
    text = (
        "# header\n"
        "set s0 2\n"
        "set s1 3\n"
        "op add s2 s0 s1\n"
        "end\n"
    )
    world = _run(text, user_variables=set())
    var_events = [
        e for e in world.events
        if isinstance(e, (VariableReadEvent, VariableWriteEvent))
    ]
    assert var_events == []


def test_magic_variable_read_is_not_instrumented():
    """`@time` etc. are pre-seeded as deterministic stubs; reading them
    via `set s0 @time` must NOT emit a VariableReadEvent (matches the
    REPL primitives — magic vars push deterministic stubs without
    going through `world.read_variable`)."""
    text = "# header\nset s0 @time\nend\n"
    world = _run(text, user_variables={"counter"})
    reads = [e for e in world.events if isinstance(e, VariableReadEvent)]
    assert reads == []


def test_swap_scratch_is_not_instrumented():
    """`__swap_tmp` is a compiler-internal scratch — must never appear
    in a Variable event even though it's a non-`@` non-`s<i>` name."""
    text = (
        "# header\n"
        "set s0 1\n"
        "set s1 2\n"
        "set __swap_tmp s0\n"
        "set s0 s1\n"
        "set s1 __swap_tmp\n"
        "end\n"
    )
    world = _run(text, user_variables={"counter"})
    var_events = [
        e for e in world.events
        if isinstance(e, (VariableReadEvent, VariableWriteEvent))
    ]
    assert var_events == []


def test_default_user_variables_is_empty_set():
    """For backward compatibility, MlogInterpreter without a
    user_variables argument must emit NO Variable events on any
    set/op — pre-existing callers continue to see the old behavior."""
    text = "# header\nset counter 7\nset s0 counter\nend\n"
    world = MockWorld()
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    var_events = [
        e for e in world.events
        if isinstance(e, (VariableReadEvent, VariableWriteEvent))
    ]
    assert var_events == []


def test_print_of_user_variable_emits_read_event():
    """`print counter` must emit a VariableReadEvent for counter (the
    print operand IS a read of the user variable's value)."""
    text = "# header\nset counter 9\nprint counter\nend\n"
    world = _run(text, user_variables={"counter"})
    reads = [e for e in world.events if isinstance(e, VariableReadEvent)]
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert len(reads) == 1
    assert reads[0].name == "counter" and reads[0].value == 9.0
    assert len(prints) == 1 and prints[0].text == "9"
