"""Web viz HTTP + WebSocket server (bead mforth-10t.20).

Library choice rationale
========================

The bead text offered three options:

1. stdlib ``http.server`` only — no WebSocket support.
2. stdlib ``http.server`` + the ``websockets`` library (async).
3. FastAPI (HTTP + WebSocket in one framework).

mforth's ``pyproject.toml`` lists ``dependencies = []`` — no web
framework is otherwise needed. FastAPI would pull in starlette +
pydantic + anyio for one feature, violating the "minimal dep" stance
shared with the LSP bead's pygls choice. Option 2 wins:

* ``http.server`` is stdlib and trivially serves the four static files
  (``index.html``, ``app.js``, ``app.css``, future ``app.wasm`` etc.)
  from ``src/mforth/viz/static/``.
* ``websockets`` (PyPI) is a single, well-maintained async library
  whose API surface is small and stable. The MockWorld event stream is
  push-only from server to client (plus a small control channel from
  client to server), which is a textbook fit for ``websockets.serve``.
* Both servers run in a background thread so that the embedder (the
  future ``mforth run --serve`` wiring at bead .22) doesn't have to
  contend with our event loop.

Protocol (over WebSocket — JSON, one message per line)
======================================================

Server → client:

  * ``{"type": "snapshot", "world_state": {...}}`` — sent immediately
    on connect. ``world_state`` is ``{"links": {...}, "variables":
    {...}, "tick": <float>}``. Each link is serialized as ``{"type":
    <str>, "state": {...}}``.

  * ``{"type": "event", "event_type": "<ClassName>", "payload":
    {...}}`` — sent for every event MockWorld emits. ``event_type`` is
    the unqualified dataclass name (e.g. ``"MessagePrintEvent"``);
    ``payload`` mirrors the dataclass fields, including the inherited
    ``timestamp``. The serializer walks any frozen dataclass, so new
    Event subclasses added downstream are forward-compatible — the
    viz client (bead .21) does not need to be edited when the event
    catalog grows.

  * ``{"type": "control_ack", "cmd": ...}`` — acknowledgment of a
    client control command.

Client → server:

  * ``{"type": "control", "cmd": "pause"|"resume"|"step"}`` —
    requests an executor lifecycle change. In v1 (this bead) the
    server records the command on ``self.control_commands`` and
    broadcasts a ``control_ack``; bead .22 will hook the queue into
    the actual executor when ``--serve`` wires this server onto
    ``mforth run``.

Threading model
===============

* HTTP server: stdlib ``ThreadingHTTPServer`` on its own thread.
* WebSocket server: ``websockets.serve`` inside a dedicated asyncio
  loop on its own thread. Event-stream callbacks arrive on the
  executor thread; they are forwarded to the loop via
  ``loop.call_soon_threadsafe`` so the broadcast happens on the
  asyncio side.

This avoids any shared mutable state between the HTTP and WS sides
beyond ``MockWorld`` itself (which is the embedder's responsibility
to coordinate).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import websockets

from mforth.backend.world import Event, MockWorld

_STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_event(event: Event) -> dict:
    """Render any frozen-dataclass Event subclass into the wire shape
    ``{"type": "event", "event_type": <ClassName>, "payload": {...}}``.

    Uses ``dataclasses.asdict`` so the serializer is forward-compatible
    with new Event subclasses (any future fields are picked up
    automatically). The payload includes the inherited ``timestamp``.
    """
    payload = dataclasses.asdict(event)
    return {
        "type": "event",
        "event_type": type(event).__name__,
        "payload": payload,
    }


def _serialize_world(world: MockWorld) -> dict:
    """Render a MockWorld snapshot for the ``snapshot`` message."""
    links = {
        name: {"type": block.type, "state": _coerce_state(block.state)}
        for name, block in world.links.items()
    }
    return {
        "links": links,
        "variables": dict(world.variables),
        "tick": world.events.tick,
    }


def _coerce_state(state: dict) -> dict:
    """Coerce a block's state dict into JSON-safe primitives. The state
    dicts that ship today are all JSON-safe (lists / floats / bools /
    strings); this helper is the explicit boundary so future block types
    that store non-JSON state can plug in custom rendering here."""
    out: dict = {}
    for k, v in state.items():
        if isinstance(v, (list, tuple)):
            out[k] = list(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Static-file HTTP handler
# ---------------------------------------------------------------------------


class _StaticHandler(SimpleHTTPRequestHandler):
    """Serves files from ``src/mforth/viz/static/``. ``/`` redirects to
    ``/index.html``."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_STATIC_DIR), **kwargs)

    def log_message(self, format, *args):  # noqa: A002 — stdlib signature
        # Suppress default stderr logging; the test suite would otherwise
        # be noisy and the embedder can wire its own logging.
        pass

    def do_GET(self):  # noqa: N802 — stdlib signature
        if self.path in ("", "/"):
            self.path = "/index.html"
        return super().do_GET()


# ---------------------------------------------------------------------------
# VizServer
# ---------------------------------------------------------------------------


class VizServer:
    """HTTP + WebSocket server that bridges ``MockWorld.events`` to
    connected browser clients.

    Lifecycle:

      srv = VizServer(world, http_port=..., ws_port=...)
      srv.start()   # spawns two threads (HTTP + WS asyncio loop)
      ...
      srv.stop()    # joins both threads, unsubscribes from world.events

    Both ports may be ``0`` to ask the OS for a free port; after
    ``start()`` returns the resolved ports are available as
    ``srv.http_port`` and ``srv.ws_port``.
    """

    def __init__(
        self,
        world: MockWorld,
        http_port: int = 0,
        ws_port: int = 0,
        host: str = "127.0.0.1",
    ) -> None:
        self.world = world
        self.host = host
        self.http_port = http_port
        self.ws_port = ws_port

        self._http_server: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None

        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_server = None  # websockets.Server
        self._ws_ready = threading.Event()

        # Set of connected client WebSocket objects. Mutated only on the
        # asyncio loop thread.
        self._clients: set = set()

        # FIFO of control commands received from clients. Bead .22 polls
        # this from the executor side; tests assert on its contents.
        self.control_commands: list[dict] = []

        # Subscriber callback registered on world.events. Kept as an attr
        # so stop() can detach it cleanly.
        self._subscriber = self._on_world_event

    # ---- public lifecycle ----

    def start(self) -> None:
        self._start_http()
        self._start_ws()
        # Subscribe AFTER the WS loop is up so the first event has a
        # broadcast target if it arrives instantly.
        self.world.events.subscribe(self._subscriber)

    def stop(self) -> None:
        # Detach subscriber first so no further events queue up.
        try:
            self.world.events.subscribers.remove(self._subscriber)
        except ValueError:
            pass

        if self._ws_loop is not None and self._ws_server is not None:
            async def _shutdown():
                self._ws_server.close()
                await self._ws_server.wait_closed()
                # Force-close any lingering clients so the loop can stop.
                for ws in list(self._clients):
                    try:
                        await ws.close()
                    except Exception:
                        pass

            fut = asyncio.run_coroutine_threadsafe(_shutdown(), self._ws_loop)
            try:
                fut.result(timeout=2.0)
            except Exception:
                pass
            self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)

        if self._ws_thread is not None:
            self._ws_thread.join(timeout=2.0)

        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
        if self._http_thread is not None:
            self._http_thread.join(timeout=2.0)

    # ---- HTTP side ----

    def _start_http(self) -> None:
        server = ThreadingHTTPServer((self.host, self.http_port), _StaticHandler)
        self.http_port = server.server_address[1]
        self._http_server = server

        def _serve():
            server.serve_forever(poll_interval=0.05)

        t = threading.Thread(target=_serve, name="mforth-viz-http", daemon=True)
        t.start()
        self._http_thread = t

    # ---- WS side ----

    def _start_ws(self) -> None:
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._ws_loop = loop

            async def _boot():
                self._ws_server = await websockets.serve(
                    self._client_handler, self.host, self.ws_port
                )
                # Resolve the actual port if 0 was requested.
                for sock in self._ws_server.sockets:
                    self.ws_port = sock.getsockname()[1]
                    break
                self._ws_ready.set()

            loop.run_until_complete(_boot())
            try:
                loop.run_forever()
            finally:
                loop.close()

        t = threading.Thread(target=_run, name="mforth-viz-ws", daemon=True)
        t.start()
        self._ws_thread = t
        # Wait for the server to bind before returning so callers can
        # connect immediately after start() returns.
        if not self._ws_ready.wait(timeout=5.0):
            raise RuntimeError("VizServer WS loop failed to start within 5s")

    async def _client_handler(self, ws) -> None:
        """Per-connection coroutine: send snapshot, then process incoming
        control frames until the client disconnects. Outbound events are
        pushed by the world-event subscriber on the asyncio loop."""
        self._clients.add(ws)
        try:
            snapshot = {"type": "snapshot", "world_state": _serialize_world(self.world)}
            await ws.send(json.dumps(snapshot))
            async for raw in ws:
                await self._handle_client_message(ws, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    async def _handle_client_message(self, ws, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            # Malformed message — drop silently; do not crash the server.
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "control":
            cmd = msg.get("cmd")
            if cmd in ("pause", "resume", "step"):
                self.control_commands.append({"cmd": cmd})
                try:
                    await ws.send(json.dumps({"type": "control_ack", "cmd": cmd}))
                except websockets.ConnectionClosed:
                    pass
        # Unknown message types are silently ignored — keeps the
        # protocol forward-compatible with future client additions.

    # ---- world-event subscriber ----

    def _on_world_event(self, event: Event) -> None:
        """Called on the executor thread when MockWorld emits an event.
        Forward to the asyncio loop for broadcast."""
        if self._ws_loop is None:
            return
        wire = json.dumps(_serialize_event(event))
        try:
            self._ws_loop.call_soon_threadsafe(self._broadcast, wire)
        except RuntimeError:
            # Loop already stopped; drop the event.
            pass

    def _broadcast(self, wire: str) -> None:
        """Schedule sends to every connected client on the asyncio loop."""
        for ws in list(self._clients):
            asyncio.create_task(self._safe_send(ws, wire))

    async def _safe_send(self, ws, wire: str) -> None:
        try:
            await ws.send(wire)
        except websockets.ConnectionClosed:
            self._clients.discard(ws)
        except Exception:
            self._clients.discard(ws)


__all__ = ["VizServer"]
