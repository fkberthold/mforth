"""Unit tests for the host REPL Mindustry primitive bindings (bead
mforth-10t.12).

Wires the five v1 Mindustry primitives (PRINT, PRINTFLUSH, WAIT,
SENSOR, GETLINK) to their MockWorld methods and asserts that each
emits the corresponding Event on the world's EventStream.

## Block-handle representation contract (LOAD-BEARING)

A block handle on the data stack is the **bare mforth-name string**
(e.g. `"message1"`), NOT a prefixed form like `"block:message1"`. This
matches:

* `MockWorld.lookup_block(name)` — accepts the bare name directly.
* `MockWorld.getlink(i)` — returns the bare name directly.
* mlog itself, where block identifiers (`message1`, `cell1`, `switch1`)
  are bare names in the source.

The bead description recommended `'block:<mforth-name>'` as a
disambiguator on the stack, but at the host-REPL layer there's no
collision risk (the stack holds a heterogeneous Python `list`; a bare
string is already structurally distinct from a numeric value). At the
mlog backend layer there's no separate handle type at all — the slot
just holds the name. Pinning the bare-name shape here keeps REPL ↔
mlog equivalence trivial: the same `WordCall("GETLINK")` produces the
same observable stack value on both surfaces (the name string).

If you find yourself "fixing" a test here to expect `'block:foo'`,
stop and re-read the module docstring of
`src/mforth/backend/primitives.py`.

## Event-shape contract

The mlog interpreter (bead mforth-10t.31) and the future web viz
subscribe to the same EventStream. The event payloads emitted by these
primitives MUST match what world.py declares — the dataclasses are
frozen and the host primitives are thin pass-throughs:

* `MessagePrintEvent(text)` — from PRINT via `world.print`.
* `MessagePrintflushEvent(block_name, buffer)` — from PRINTFLUSH.
* `WaitEvent(seconds)` — from WAIT.
* `SensorReadEvent(block_name, prop, value)` — from SENSOR.
* `LinkResolvedEvent(index, block_name)` — from GETLINK (in-range only).

The bead .16 mlog emitter must produce code whose in-repo interpreter
execution emits the same event shapes for the same source program.
That is the REPL ↔ mlog equivalence contract for Mindustry primitives.
"""

from __future__ import annotations

import pytest

from mforth.backend.host import Executor
from mforth.backend.primitives import register_all
from mforth.backend.world import (
    Block,
    LinkResolvedEvent,
    MessagePrintEvent,
    MessagePrintflushEvent,
    MockWorld,
    SensorReadEvent,
    WaitEvent,
)
from mforth.parse import LitInt, LitStr, Program, SrcLoc, WordCall
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers (mirror tests/unit/test_primitives.py)
# ---------------------------------------------------------------------------


def L(line: int = 1, col: int = 1) -> SrcLoc:
    return SrcLoc("<test>", line, col)


def lit(n: int, col: int = 1) -> LitInt:
    return LitInt(value=n, src_loc=L(1, col))


def lits(s: str, col: int = 1) -> LitStr:
    return LitStr(value=s, src_loc=L(1, col))


def call(name: str, col: int = 1) -> WordCall:
    return WordCall(name=name, src_loc=L(1, col))


def run(
    terms: list,
    *,
    defs: list | None = None,
    world: MockWorld | None = None,
) -> Executor:
    """Build a Program from a list of main-terms, stackcheck it, register
    every primitive on a fresh Executor, run it, and return the executor.
    """
    program = Program(definitions=defs or [], main=terms)
    result = stackcheck(program)
    w = world if world is not None else MockWorld()
    ex = Executor(world=w)
    register_all(ex)
    ex.execute(result)
    return ex


# ---------------------------------------------------------------------------
# PRINT — ( str -- ) → world.print → MessagePrintEvent
# ---------------------------------------------------------------------------


def test_print_emits_message_print_event_with_string():
    world = MockWorld()
    run([lits("hello"), call("PRINT")], world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert len(prints) == 1
    assert prints[0].text == "hello"


def test_print_consumes_string_from_stack():
    """PRINT has stack effect (1, 0) — it must leave the stack empty
    after consuming its argument."""
    ex = run([lits("hi"), call("PRINT")])
    assert ex.data_stack == []


def test_print_coerces_numeric_arg_via_str():
    """PRINT is documented as ( str -- ) but mlog `print` accepts any
    value and stringifies. The host primitive mirrors that — `world.print`
    already calls `str()` on its input, so pushing a numeric and calling
    PRINT must emit `MessagePrintEvent` with the str() of that number.
    """
    world = MockWorld()
    run([lit(42), call("PRINT")], world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert prints[0].text == "42"


# ---------------------------------------------------------------------------
# mforth-05h — PRINT renders integer-valued floats WITHOUT a trailing ".0".
#
# In-game mlog `print` stringifies whole-number doubles as integers
# ("1" not "1.0"); the in-repo mlog interpreter mirrors that via
# `mforth.mlog_interp._format_for_print`. Before this fix the host
# PRINT primitive str()'d the value verbatim, so a Python float on the
# stack (e.g. result of `1 +` after a counter read that came back as
# float via VariableReadEvent → 1.0) rendered as "1.0" — diverging
# from mlog on every numeric PRINT.
# ---------------------------------------------------------------------------


def test_print_integer_valued_float_renders_without_decimal():
    """Pushing the float 1.0 then PRINTing must emit text "1", matching
    the in-game `print` behavior and the mlog interpreter."""
    # The lex-time literal `1` produces an int, not a float. To test the
    # float-shaped path we need a real Python float on the stack. Use
    # `1 1 /` — both operands int, but `/` is float division so the
    # result is the float 1.0.
    world = MockWorld()
    run([lit(1), lit(1), call("/"), call("PRINT")], world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert prints[0].text == "1", (
        f"integer-valued float must render without .0; got {prints[0].text!r}"
    )


def test_print_non_integer_float_keeps_decimal():
    """Pushing 2.5 must still print as "2.5" — the rule only strips .0
    from integer-valued floats."""
    world = MockWorld()
    run([lit(5), lit(2), call("/"), call("PRINT")], world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert prints[0].text == "2.5"


def test_print_int_literal_renders_as_int():
    """Pushing a true int literal (no float coercion) must render as
    that int's string — regression guard so the new branch doesn't
    accidentally re-route int values through float coercion."""
    world = MockWorld()
    run([lit(42), call("PRINT")], world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert prints[0].text == "42"


# ---------------------------------------------------------------------------
# PRINTFLUSH — ( block -- ) → world.printflush → MessagePrintflushEvent
# ---------------------------------------------------------------------------


def test_printflush_emits_event_with_block_name_and_buffer():
    """The bead's acceptance shape: PRINT some text, PRINTFLUSH to a
    message block, assert MessagePrintflushEvent fires with the buffered
    text and the named block."""
    world = MockWorld()
    world.add_link(Block.message("message1"))
    terms = [
        lits("hello"), call("PRINT"),
        lits("message1"), call("PRINTFLUSH"),
    ]
    run(terms, world=world)
    flushes = [e for e in world.events if isinstance(e, MessagePrintflushEvent)]
    assert len(flushes) == 1
    assert flushes[0].block_name == "message1"
    assert flushes[0].buffer == "hello"


def test_printflush_consumes_block_handle_from_stack():
    """Stack effect (1, 0) — handle is consumed; stack is empty after."""
    world = MockWorld()
    world.add_link(Block.message("message1"))
    ex = run([lits("message1"), call("PRINTFLUSH")], world=world)
    assert ex.data_stack == []


def test_printflush_to_nonexistent_block_still_emits_event():
    """world.printflush emits the event even when the block doesn't
    exist (mlog behavior: silent on bad block). Pinned here so the host
    matches."""
    world = MockWorld()
    # No links added.
    run([lits("ghost"), call("PRINTFLUSH")], world=world)
    flushes = [e for e in world.events if isinstance(e, MessagePrintflushEvent)]
    assert len(flushes) == 1
    assert flushes[0].block_name == "ghost"


def test_print_then_printflush_event_ordering():
    """MessagePrintEvent must fire BEFORE MessagePrintflushEvent for the
    program `"a" PRINT  "b" PRINT  msg PRINTFLUSH`. Subscribers
    (web viz, mlog equivalence harness) rely on event ordering."""
    world = MockWorld()
    world.add_link(Block.message("message1"))
    terms = [
        lits("a"), call("PRINT"),
        lits("b"), call("PRINT"),
        lits("message1"), call("PRINTFLUSH"),
    ]
    run(terms, world=world)
    kinds = [type(e).__name__ for e in world.events]
    assert kinds == [
        "MessagePrintEvent",
        "MessagePrintEvent",
        "MessagePrintflushEvent",
    ]
    flush = [e for e in world.events if isinstance(e, MessagePrintflushEvent)][0]
    # PRINTFLUSH joins all queued prints in order, per world.py.
    assert flush.buffer == "ab"


# ---------------------------------------------------------------------------
# WAIT — ( seconds -- ) → world.wait → WaitEvent
# ---------------------------------------------------------------------------


def test_wait_emits_wait_event_with_seconds():
    world = MockWorld()
    run([lit(3), call("WAIT")], world=world)
    waits = [e for e in world.events if isinstance(e, WaitEvent)]
    assert len(waits) == 1
    assert waits[0].seconds == 3.0


def test_wait_consumes_seconds_from_stack():
    ex = run([lit(1), call("WAIT")])
    assert ex.data_stack == []


def test_wait_advances_simulation_clock():
    """world.wait advances `events.tick`. Subsequent events should carry
    the advanced timestamp. Pin the cross-event timeline contract."""
    world = MockWorld()
    run([lit(5), call("WAIT"), lits("hi"), call("PRINT")], world=world)
    print_ev = [e for e in world.events if isinstance(e, MessagePrintEvent)][0]
    assert print_ev.timestamp == 5.0


def test_wait_zero_seconds_still_emits_event():
    """0-second WAIT is legal in mlog (no-op in time, still consumes a
    cycle). Event must still fire so subscribers can count."""
    world = MockWorld()
    run([lit(0), call("WAIT")], world=world)
    waits = [e for e in world.events if isinstance(e, WaitEvent)]
    assert len(waits) == 1
    assert waits[0].seconds == 0.0


# ---------------------------------------------------------------------------
# SENSOR — ( block prop -- value ) → world.sensor → SensorReadEvent
# ---------------------------------------------------------------------------


def test_sensor_pushes_value_and_emits_event():
    """SENSOR pops (block, prop) and pushes the property's value.
    Block 'cell1' with state {'@health': 100} → SENSOR returns 100."""
    world = MockWorld()
    block = Block(name="cell1", type="memory-cell", state={"@health": 100})
    world.add_link(block)
    terms = [lits("cell1"), lits("@health"), call("SENSOR")]
    ex = run(terms, world=world)
    assert ex.data_stack == [100.0]
    reads = [e for e in world.events if isinstance(e, SensorReadEvent)]
    assert len(reads) == 1
    assert reads[0].block_name == "cell1"
    assert reads[0].prop == "@health"
    assert reads[0].value == 100.0


def test_sensor_missing_block_returns_zero():
    """world.sensor returns 0.0 for missing blocks (community-lore
    mlog behavior). The event still fires; the host primitive must
    push 0.0 — NOT raise — to preserve REPL ↔ mlog equivalence."""
    world = MockWorld()
    terms = [lits("ghost"), lits("@health"), call("SENSOR")]
    ex = run(terms, world=world)
    assert ex.data_stack == [0.0]


def test_sensor_unknown_property_returns_zero():
    world = MockWorld()
    block = Block(name="cell1", type="memory-cell", state={"@health": 100})
    world.add_link(block)
    terms = [lits("cell1"), lits("@nonexistent"), call("SENSOR")]
    ex = run(terms, world=world)
    assert ex.data_stack == [0.0]


def test_sensor_stack_effect_is_two_in_one_out():
    """Stack effect (2, 1). Before: [..., block, prop]. After: [..., value]."""
    world = MockWorld()
    block = Block(name="cell1", type="memory-cell", state={"@x": 7})
    world.add_link(block)
    # Pre-load a sentinel under the SENSOR inputs so we can verify
    # exactly two items were consumed and exactly one was pushed.
    ex = run(
        [lit(999), lits("cell1"), lits("@x"), call("SENSOR")],
        world=world,
    )
    assert ex.data_stack == [999, 7.0]


# ---------------------------------------------------------------------------
# GETLINK — ( i -- block ) → world.getlink → LinkResolvedEvent
# ---------------------------------------------------------------------------


def test_getlink_pushes_block_name_and_emits_event():
    """GETLINK 0 → the first linked block's mforth-name. The pushed
    value is the BARE name string (not 'block:<name>') — see the module
    docstring's block-handle representation contract."""
    world = MockWorld()
    world.add_link(Block.message("message1"))
    world.add_link(Block.generic("cell1"))
    ex = run([lit(0), call("GETLINK")], world=world)
    assert ex.data_stack == ["message1"]
    links = [e for e in world.events if isinstance(e, LinkResolvedEvent)]
    assert len(links) == 1
    assert links[0].index == 0
    assert links[0].block_name == "message1"


def test_getlink_second_index():
    world = MockWorld()
    world.add_link(Block.message("message1"))
    world.add_link(Block.generic("cell1"))
    ex = run([lit(1), call("GETLINK")], world=world)
    assert ex.data_stack == ["cell1"]


def test_getlink_out_of_range_pushes_none():
    """world.getlink returns None for out-of-range i (mlog: null). The
    host primitive must push that None so the StackEffect (1, 1)
    contract holds even at the edge — pushing nothing would underflow
    a subsequent consumer.

    The mlog interpreter (bead .31) will produce the same observable:
    `getlink result 99` on a 2-link world leaves `result` as null;
    the host's `None` is the equivalent sentinel.
    """
    world = MockWorld()
    world.add_link(Block.message("message1"))
    ex = run([lit(99), call("GETLINK")], world=world)
    assert ex.data_stack == [None]


def test_getlink_out_of_range_does_not_emit_link_event():
    """world.getlink only emits LinkResolvedEvent for in-range lookups
    (per its docstring). Pin that contract — out-of-range is silent."""
    world = MockWorld()
    world.add_link(Block.message("message1"))
    run([lit(5), call("GETLINK")], world=world)
    links = [e for e in world.events if isinstance(e, LinkResolvedEvent)]
    assert len(links) == 0


def test_getlink_consumes_index_from_stack():
    """Stack effect (1, 1). Before: [..., i]. After: [..., block-or-None]."""
    world = MockWorld()
    world.add_link(Block.message("message1"))
    ex = run([lit(42), lit(0), call("GETLINK")], world=world)
    # 42 stays underneath; index 0 consumed; block name pushed.
    assert ex.data_stack == [42, "message1"]


# ---------------------------------------------------------------------------
# Integration — the bead's acceptance shape: PRINT + PRINTFLUSH end-to-end
# ---------------------------------------------------------------------------


def test_integration_acceptance_print_hello_printflush_message1():
    """The bead's acceptance test, paraphrased:

        "hello" PRINT  "message1" PRINTFLUSH

    Asserts MessagePrintflushEvent fires with buffer 'hello' on
    block_name 'message1', AND the target message block's buffer state
    is updated."""
    world = MockWorld()
    world.add_link(Block.message("message1"))
    terms = [
        lits("hello"), call("PRINT"),
        lits("message1"), call("PRINTFLUSH"),
    ]
    run(terms, world=world)
    flushes = [e for e in world.events if isinstance(e, MessagePrintflushEvent)]
    assert len(flushes) == 1
    assert flushes[0].block_name == "message1"
    assert flushes[0].buffer == "hello"
    # And the block itself received the buffer.
    block = world.lookup_block("message1")
    assert block is not None
    assert block.state["buffer"] == ["hello"]


def test_integration_getlink_then_sensor_then_printflush():
    """End-to-end Mindustry-primitive chain:
        0 GETLINK             ( -- 'message1' )
        DUP "@type" SENSOR    ( 'message1' -- 'message1' typeval )
        DROP                  ( -- 'message1' )
        "hi" PRINT            ( 'message1' -- 'message1' )
        PRINTFLUSH            ( 'message1' -- )

    Exercises all five primitives except WAIT in one program; relies on
    the bare-name block-handle representation (GETLINK's output is fed
    directly to PRINTFLUSH).
    """
    world = MockWorld()
    msg = Block(name="message1", type="message",
                state={"buffer": [], "@type": 7})
    world.add_link(msg)
    terms = [
        lit(0), call("GETLINK"),
        call("DUP"), lits("@type"), call("SENSOR"),
        call("DROP"),
        lits("hi"), call("PRINT"),
        call("PRINTFLUSH"),
    ]
    ex = run(terms, world=world)
    assert ex.data_stack == []
    flushes = [e for e in world.events if isinstance(e, MessagePrintflushEvent)]
    assert flushes[0].buffer == "hi"
    assert flushes[0].block_name == "message1"
    reads = [e for e in world.events if isinstance(e, SensorReadEvent)]
    assert reads[0].value == 7.0


def test_integration_wait_between_prints_advances_timeline():
    """`"a" PRINT  2 WAIT  "b" PRINT  msg PRINTFLUSH`:
    the second print event's timestamp must reflect the WAIT."""
    world = MockWorld()
    world.add_link(Block.message("message1"))
    terms = [
        lits("a"), call("PRINT"),
        lit(2), call("WAIT"),
        lits("b"), call("PRINT"),
        lits("message1"), call("PRINTFLUSH"),
    ]
    run(terms, world=world)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert prints[0].timestamp == 0.0
    assert prints[1].timestamp == 2.0
    flush = [e for e in world.events if isinstance(e, MessagePrintflushEvent)][0]
    assert flush.timestamp == 2.0
    assert flush.buffer == "ab"


# ---------------------------------------------------------------------------
# register_all wiring contract — extended for Mindustry primitives
# ---------------------------------------------------------------------------


def test_register_all_installs_every_mindustry_primitive():
    """After register_all, each of the five Mindustry-tagged BuiltinWords
    must have a registered callable. This is the .11 wiring-contract
    test extended to cover the .12 deliverable."""
    from mforth.dictionary import BuiltinWord, standard_dictionary

    d = standard_dictionary()
    ex = Executor()
    register_all(ex)

    for name in ["PRINT", "PRINTFLUSH", "WAIT", "SENSOR", "GETLINK"]:
        entry = d.lookup(name)
        assert isinstance(entry, BuiltinWord), f"{name} should be a BuiltinWord"
        assert entry.tag == "mindustry", (
            f"{name} should be tagged 'mindustry', got {entry.tag!r}"
        )
        assert ex._primitives.get(name.upper()) is not None, (
            f"primitive {name!r} not registered by register_all"
        )


def test_register_all_does_not_break_prior_primitives():
    """Adding the Mindustry primitives must not regress the arith/stack/
    var/io coverage from .11. Spot-check a few representative names."""
    ex = Executor()
    register_all(ex)
    for name in ["+", "-", "DUP", "ROT", "@", "!", ".", "AND", "="]:
        assert ex._primitives.get(name.upper()) is not None
