# Manual viz harnesses (bead mforth-10t.21)

These pages drive `app.js` in isolation — no WebSocket connection — so
each pane can be eyeballed without booting the Python server. Open
them in a browser straight off disk via `file://` or via any static
file server pointed at `src/mforth/viz/static/`.

The `__mforthViz` global (exposed by `app.js`) is the test surface;
each fixture below calls `__mforthViz.dispatch(...)` with synthetic
snapshot + event messages that mirror the wire shape defined in
drawer `drawer_mforth_decisions_a8e467829495804153cd193d`.

## Files

| File | What it exercises |
|---|---|
| `fixture-three-blocks.html` | World pane layout, block-detail click-through, message buffer rendering, sensor edge pulse. |
| `fixture-stack-and-tick.html` | Stack pane (top-at-bottom convention), tick counter advance via WaitEvent, VariableWriteEvent → stack synthesis. |
| `fixture-event-log.html` | Event log scrolling, filter input, log-clear, log collapse/expand toggle, forward-compat unknown event_type. |

## How to drive a live server (the .20 → .21 → .22 path)

Until bead `.22` lands the `--serve` flag on `mforth run`, boot the
server by hand from a Python REPL in the project root:

```python
from mforth.backend.world import MockWorld
from mforth.viz.server import VizServer

world = MockWorld()
srv = VizServer(world=world, http_port=8000, ws_port=8001)
srv.start()
print(f"http://127.0.0.1:{srv.http_port}/?wsPort={srv.ws_port}")
# Open the printed URL in a browser; drive world.events.emit(...) to
# observe live updates. Ctrl-C / srv.stop() when done.
```

Then call `world.events.emit(MessagePrintEvent, text="hello")` etc.
from the REPL to verify the live path.
