"""Web visualizer for mforth — HTTP + WebSocket server (bead mforth-10t.20).

Subscribes to ``MockWorld.events`` and pushes events to browser clients
over a JSON WebSocket protocol. The viz CLIENT (world pane, block
detail, Forth pane, stack pane, event log) is bead ``mforth-10t.21``
and lives in ``src/mforth/viz/static/``; this package owns the
SERVER + PROTOCOL only.

The ``--serve`` flag wiring on ``mforth run`` is bead
``mforth-10t.22``; this package exposes ``VizServer`` as the import
surface that wiring will use.
"""

from mforth.viz.server import VizServer

__all__ = ["VizServer"]
