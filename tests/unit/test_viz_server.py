"""Unit tests for the web viz server (bead mforth-10t.20).

Pins the contract:

* Booting the viz server in a fixture, opening a WebSocket against
  it, MUST receive a ``{"type": "snapshot", "world_state": {...}}``
  message immediately on connect.
* Subsequent MockWorld events MUST stream as
  ``{"type": "event", "event_type": "<ClassName>", "payload": {...}}``
  with the payload mirroring the dataclass fields.
* HTTP ``/index.html`` MUST return 200 with the basic shell markup.
* Client ``{"type": "control", "cmd": "pause"|"resume"|"step"}``
  messages MUST be acknowledged with
  ``{"type": "control_ack", "cmd": ...}``.

These tests run the real server on an ephemeral port — no mocks at the
network boundary. The integration test (tests/integration/test_viz_smoke.py)
covers the HTTP-only smoke path; the unit tests here cover the
WebSocket protocol contract.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

import pytest
import websockets

from mforth.backend.world import Block, MockWorld
from mforth.viz.server import VizServer


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def world() -> MockWorld:
    w = MockWorld()
    w.add_link(Block.message("message1"))
    w.add_link(Block.switch("switch1", on=True))
    return w


@pytest.fixture
def server(world: MockWorld):
    """Boot a VizServer on ephemeral ports; yield the server; shut down."""
    http_port = _free_port()
    ws_port = _free_port()
    srv = VizServer(world=world, http_port=http_port, ws_port=ws_port)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


def _ws_url(srv: "VizServer") -> str:
    return f"ws://127.0.0.1:{srv.ws_port}"


def _http_url(srv: "VizServer", path: str) -> str:
    return f"http://127.0.0.1:{srv.http_port}{path}"


# ---------------------------------------------------------------------------
# Snapshot-on-connect contract
# ---------------------------------------------------------------------------


def test_snapshot_received_on_connect(server: "VizServer") -> None:
    """Opening a WS MUST yield a snapshot message containing world_state."""

    async def run() -> dict:
        async with websockets.connect(_ws_url(server)) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(run())
    assert msg["type"] == "snapshot"
    assert "world_state" in msg
    state = msg["world_state"]
    assert "links" in state
    assert "variables" in state
    assert "tick" in state
    # the message1 + switch1 from the fixture round-trip
    assert "message1" in state["links"]
    assert "switch1" in state["links"]
    assert state["links"]["message1"]["type"] == "message"
    assert state["links"]["switch1"]["type"] == "switch"


# ---------------------------------------------------------------------------
# Event-streaming contract
# ---------------------------------------------------------------------------


def test_events_stream_to_connected_client(
    server: "VizServer", world: MockWorld
) -> None:
    """A MockWorld event emitted after connect MUST be pushed as an
    event message with event_type + payload mirroring the dataclass."""

    async def run() -> dict:
        async with websockets.connect(_ws_url(server)) as ws:
            # consume snapshot first
            await asyncio.wait_for(ws.recv(), timeout=2.0)
            # emit an event server-side
            world.print("hello")
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(run())
    assert msg["type"] == "event"
    assert msg["event_type"] == "MessagePrintEvent"
    assert msg["payload"]["text"] == "hello"
    assert "timestamp" in msg["payload"]


def test_event_types_are_forward_compatible(
    server: "VizServer", world: MockWorld
) -> None:
    """The serializer MUST handle any frozen-dataclass Event subclass —
    not just a hardcoded set. Exercises four different event types in
    one go (PRINT, PRINTFLUSH, WAIT, SENSOR)."""

    async def run() -> list:
        events: list = []
        async with websockets.connect(_ws_url(server)) as ws:
            await asyncio.wait_for(ws.recv(), timeout=2.0)  # snapshot
            world.print("hi")
            world.printflush("message1")
            world.wait(0.5)
            world.sensor("switch1", "on")
            for _ in range(4):
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                events.append(json.loads(raw))
        return events

    events = asyncio.run(run())
    types = [e["event_type"] for e in events]
    assert types == [
        "MessagePrintEvent",
        "MessagePrintflushEvent",
        "WaitEvent",
        "SensorReadEvent",
    ]
    # field-level smoke
    assert events[1]["payload"]["block_name"] == "message1"
    assert events[2]["payload"]["seconds"] == 0.5


# ---------------------------------------------------------------------------
# Control-command contract (v1: ack only; .22 wires real semantics)
# ---------------------------------------------------------------------------


def test_control_command_is_acknowledged(server: "VizServer") -> None:
    """Sending a control command MUST yield a control_ack with the same cmd."""

    async def run() -> dict:
        async with websockets.connect(_ws_url(server)) as ws:
            await asyncio.wait_for(ws.recv(), timeout=2.0)  # snapshot
            await ws.send(json.dumps({"type": "control", "cmd": "pause"}))
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(run())
    assert msg["type"] == "control_ack"
    assert msg["cmd"] == "pause"


def test_control_command_is_recorded(server: "VizServer") -> None:
    """The server MUST record control commands so bead .22 can poll them."""

    async def run() -> None:
        async with websockets.connect(_ws_url(server)) as ws:
            await asyncio.wait_for(ws.recv(), timeout=2.0)  # snapshot
            await ws.send(json.dumps({"type": "control", "cmd": "step"}))
            await asyncio.wait_for(ws.recv(), timeout=2.0)  # ack

    asyncio.run(run())
    assert "step" in [c["cmd"] for c in server.control_commands]


# ---------------------------------------------------------------------------
# Negative-cases coverage (M5)
# ---------------------------------------------------------------------------


def test_malformed_client_message_does_not_crash_server(
    server: "VizServer", world: MockWorld
) -> None:
    """A client sending non-JSON or unknown message types MUST NOT
    crash the server; the subsequent connection still works."""

    async def run() -> dict:
        async with websockets.connect(_ws_url(server)) as ws:
            await asyncio.wait_for(ws.recv(), timeout=2.0)  # snapshot
            await ws.send("not json at all {{{")
            await ws.send(json.dumps({"type": "totally-unknown"}))
            # server still alive — emit an event and confirm we receive it
            world.print("still here")
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(run())
    assert msg["type"] == "event"
    assert msg["payload"]["text"] == "still here"


def test_multiple_clients_each_receive_snapshot_and_events(
    server: "VizServer", world: MockWorld
) -> None:
    """Two simultaneous clients MUST each receive their own snapshot
    on connect and both receive subsequent events."""

    async def run() -> tuple:
        async with websockets.connect(_ws_url(server)) as a, websockets.connect(
            _ws_url(server)
        ) as b:
            snap_a = json.loads(await asyncio.wait_for(a.recv(), timeout=2.0))
            snap_b = json.loads(await asyncio.wait_for(b.recv(), timeout=2.0))
            world.print("broadcast")
            evt_a = json.loads(await asyncio.wait_for(a.recv(), timeout=2.0))
            evt_b = json.loads(await asyncio.wait_for(b.recv(), timeout=2.0))
            return snap_a, snap_b, evt_a, evt_b

    snap_a, snap_b, evt_a, evt_b = asyncio.run(run())
    assert snap_a["type"] == "snapshot"
    assert snap_b["type"] == "snapshot"
    assert evt_a["event_type"] == "MessagePrintEvent"
    assert evt_b["event_type"] == "MessagePrintEvent"


def test_client_disconnect_does_not_break_remaining_subscribers(
    server: "VizServer", world: MockWorld
) -> None:
    """If one client drops, remaining clients MUST keep receiving events."""

    async def run() -> dict:
        async with websockets.connect(_ws_url(server)) as keeper:
            await asyncio.wait_for(keeper.recv(), timeout=2.0)  # snapshot
            # open + close a transient client
            async with websockets.connect(_ws_url(server)) as transient:
                await asyncio.wait_for(transient.recv(), timeout=2.0)
            # emit after disconnect; keeper must still get it
            await asyncio.sleep(0.05)
            world.print("after disconnect")
            raw = await asyncio.wait_for(keeper.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(run())
    assert msg["type"] == "event"
    assert msg["payload"]["text"] == "after disconnect"


def test_static_index_html_served(server: "VizServer") -> None:
    """The HTTP server MUST serve /index.html (200 + basic shell markup)."""
    with urllib.request.urlopen(_http_url(server, "/index.html"), timeout=2.0) as r:
        assert r.status == 200
        body = r.read().decode("utf-8")
    assert "<html" in body.lower()
    assert "mforth" in body.lower()
