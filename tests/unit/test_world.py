"""Unit tests for MockWorld + EventStream.

Bead mforth-10t.8. The MockWorld is the host-side seam every REPL/viz/
LSP subscriber attaches to — drives the equivalence property by giving
the host executor and (later) the in-repo mlog interpreter the same
method surface.
"""

from __future__ import annotations

from mforth.backend.world import (
    Block,
    EventStream,
    LinkResolvedEvent,
    MessagePrintEvent,
    MessagePrintflushEvent,
    MockWorld,
    SensorReadEvent,
    VariableReadEvent,
    VariableWriteEvent,
    WaitEvent,
)


# ---------------------------------------------------------------------------
# EventStream
# ---------------------------------------------------------------------------


def test_eventstream_starts_empty():
    es = EventStream()
    assert len(es) == 0
    assert list(es) == []


def test_eventstream_emit_appends_with_timestamp():
    es = EventStream()
    es.emit(MessagePrintEvent, text="hello")
    assert len(es) == 1
    ev = es.events[0]
    assert isinstance(ev, MessagePrintEvent)
    assert ev.text == "hello"
    assert ev.timestamp == 0.0


def test_eventstream_subscriber_is_called():
    es = EventStream()
    seen = []
    es.subscribe(seen.append)
    es.emit(MessagePrintEvent, text="hi")
    assert len(seen) == 1
    assert seen[0].text == "hi"


def test_eventstream_multiple_subscribers_all_called():
    es = EventStream()
    a, b = [], []
    es.subscribe(a.append)
    es.subscribe(b.append)
    es.emit(WaitEvent, seconds=1.0)
    assert len(a) == 1 and len(b) == 1
    assert a[0] is b[0]  # same event instance


def test_eventstream_tick_advances_on_wait():
    es = EventStream()
    es.tick = 5.0
    es.emit(MessagePrintEvent, text="x")
    assert es.events[-1].timestamp == 5.0


# ---------------------------------------------------------------------------
# Block factories
# ---------------------------------------------------------------------------


def test_block_message_factory():
    b = Block.message("display")
    assert b.name == "display"
    assert b.type == "message"
    assert b.state["buffer"] == []


def test_block_memory_cell_factory_default_size():
    b = Block.memory_cell("cell1")
    assert b.type == "memory-cell"
    assert len(b.state["data"]) == 512
    assert all(v == 0.0 for v in b.state["data"])


def test_block_memory_cell_factory_custom_size():
    b = Block.memory_cell("small", size=16)
    assert len(b.state["data"]) == 16


def test_block_switch_factory():
    b = Block.switch("sw", on=True)
    assert b.type == "switch"
    assert b.state["on"] is True


def test_block_generic_factory():
    b = Block.generic("any")
    assert b.type == "generic"
    assert b.state == {}


# ---------------------------------------------------------------------------
# MockWorld setup + lookup
# ---------------------------------------------------------------------------


def test_empty_world_has_default_variables():
    w = MockWorld()
    assert w.variables["@counter"] == 0.0
    assert w.variables["@ipt"] == 8.0  # logic-processor default
    assert w.variables["@links"] == 0.0


def test_add_link_registers_block_and_updates_links_count():
    w = MockWorld()
    w.add_link(Block.message("display"))
    assert w.lookup_block("display").type == "message"
    assert w.variables["@links"] == 1.0


def test_lookup_unknown_block_returns_none():
    w = MockWorld()
    assert w.lookup_block("missing") is None


# ---------------------------------------------------------------------------
# print + printflush
# ---------------------------------------------------------------------------


def test_print_emits_message_print_event():
    w = MockWorld()
    w.print("hi")
    types = [type(e).__name__ for e in w.events]
    assert types == ["MessagePrintEvent"]
    assert w.events.events[0].text == "hi"


def test_printflush_flushes_queued_prints_to_block_buffer():
    w = MockWorld()
    w.add_link(Block.message("display"))
    w.print("hello ")
    w.print("world")
    w.printflush("display")
    block = w.lookup_block("display")
    assert block.state["buffer"] == ["hello world"]


def test_printflush_emits_printflush_event_with_accumulated_buffer():
    w = MockWorld()
    w.add_link(Block.message("display"))
    w.print("a")
    w.print("b")
    w.printflush("display")
    pf = w.events.events[-1]
    assert isinstance(pf, MessagePrintflushEvent)
    assert pf.block_name == "display"
    assert pf.buffer == "ab"


def test_printflush_to_unknown_block_still_emits_event_but_no_crash():
    w = MockWorld()
    w.print("a")
    w.printflush("ghost")  # no such block; mlog behavior: silent no-op on buffer
    pf = w.events.events[-1]
    assert isinstance(pf, MessagePrintflushEvent)
    assert pf.block_name == "ghost"
    assert pf.buffer == "a"


def test_printflush_clears_queue():
    w = MockWorld()
    w.add_link(Block.message("display"))
    w.print("a")
    w.printflush("display")
    w.print("b")
    w.printflush("display")
    block = w.lookup_block("display")
    # Second printflush replaces buffer with second batch (matches mlog
    # semantics where each printflush sends a fresh message)
    assert block.state["buffer"] == ["b"]


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


def test_wait_emits_event_and_advances_tick():
    w = MockWorld()
    w.wait(0.5)
    w.wait(0.25)
    assert w.events.tick == 0.75
    types = [type(e).__name__ for e in w.events]
    assert types == ["WaitEvent", "WaitEvent"]
    assert [e.seconds for e in w.events.events] == [0.5, 0.25]


def test_wait_timestamp_on_next_event_reflects_advanced_tick():
    w = MockWorld()
    w.wait(1.0)
    w.print("x")
    assert w.events.events[-1].timestamp == 1.0


# ---------------------------------------------------------------------------
# sensor
# ---------------------------------------------------------------------------


def test_sensor_on_known_block_property_returns_value_and_emits():
    w = MockWorld()
    b = Block.switch("sw", on=True)
    w.add_link(b)
    val = w.sensor("sw", "on")
    assert val == 1.0  # True coerces to 1.0
    ev = w.events.events[-1]
    assert isinstance(ev, SensorReadEvent)
    assert ev.block_name == "sw" and ev.prop == "on" and ev.value == 1.0


def test_sensor_on_unknown_block_returns_zero():
    w = MockWorld()
    val = w.sensor("ghost", "any")
    assert val == 0.0
    ev = w.events.events[-1]
    assert isinstance(ev, SensorReadEvent)
    assert ev.value == 0.0


def test_sensor_on_unknown_prop_returns_zero():
    w = MockWorld()
    w.add_link(Block.message("display"))
    val = w.sensor("display", "nonexistent")
    assert val == 0.0


# ---------------------------------------------------------------------------
# getlink
# ---------------------------------------------------------------------------


def test_getlink_in_range_returns_block_name_and_emits():
    w = MockWorld()
    w.add_link(Block.message("a"))
    w.add_link(Block.message("b"))
    name = w.getlink(0)
    assert name == "a"
    ev = w.events.events[-1]
    assert isinstance(ev, LinkResolvedEvent)
    assert ev.index == 0 and ev.block_name == "a"


def test_getlink_second_index():
    w = MockWorld()
    w.add_link(Block.message("a"))
    w.add_link(Block.message("b"))
    assert w.getlink(1) == "b"


def test_getlink_out_of_range_returns_none():
    w = MockWorld()
    w.add_link(Block.message("a"))
    assert w.getlink(5) is None


def test_getlink_negative_returns_none():
    w = MockWorld()
    w.add_link(Block.message("a"))
    assert w.getlink(-1) is None


# ---------------------------------------------------------------------------
# variable read/write
# ---------------------------------------------------------------------------


def test_read_variable_returns_current_and_emits():
    w = MockWorld()
    w.variables["@counter"] = 7.0
    val = w.read_variable("@counter")
    assert val == 7.0
    ev = w.events.events[-1]
    assert isinstance(ev, VariableReadEvent)
    assert ev.name == "@counter" and ev.value == 7.0


def test_read_unknown_variable_returns_zero():
    w = MockWorld()
    assert w.read_variable("@unknown") == 0.0


def test_write_variable_updates_and_emits():
    w = MockWorld()
    w.write_variable("n", 42)
    assert w.variables["n"] == 42.0
    ev = w.events.events[-1]
    assert isinstance(ev, VariableWriteEvent)
    assert ev.name == "n" and ev.value == 42.0


# ---------------------------------------------------------------------------
# Subscriber sees a full session's events in order
# ---------------------------------------------------------------------------


def test_subscriber_records_full_session_event_order():
    w = MockWorld()
    w.add_link(Block.message("display"))
    seen = []
    w.events.subscribe(seen.append)
    w.print("hi")
    w.printflush("display")
    w.wait(1.0)
    types = [type(e).__name__ for e in seen]
    assert types == ["MessagePrintEvent", "MessagePrintflushEvent", "WaitEvent"]
