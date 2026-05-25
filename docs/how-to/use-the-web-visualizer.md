# Use the web visualizer

> **Goal:** run a `.fs` snippet against `MockWorld` with the web
> visualizer attached, watching events fire live in the browser. The
> viz is mforth's teaching surface — same event stream the
> equivalence tests subscribe to ([events.md](../reference/events.md)),
> rendered for human eyes.
>
> **Prerequisites:**
>
> - mforth installed (`pip install -e .` from the repo root) — this
>   pulls in `websockets`, which the viz server needs.
> - A `.fs` snippet plus its paired
>   [`.world.toml` sidecar](../reference/sidecar-schema.md).
>   `examples/blink.fs` + `examples/blink.world.toml` are the canonical
>   demo and ship in this repo.
> - A modern browser (anything with `WebSocket` support; the client is
>   vanilla JS, no framework, no build step).

> **Status — read me first.** The polished one-liner
> `mforth run example.fs --serve` is the v1 target surface and is
> tracked by [bead `mforth-10t.22`](https://github.com/fkberthold/mforth/issues?q=mforth-10t.22).
> Until that wiring lands, the viz server runs as a standalone
> embedding: a tiny Python launcher that builds a `Runner`, attaches a
> `VizServer` to its `MockWorld`, and drives the runner inside a
> background thread. Both paths are documented below — pick the one
> that matches the version of mforth you're on.

## Path A — `mforth run --serve` (after `mforth-10t.22` ships)

Once `--serve` is plumbed onto the `run` subcommand, the recipe
collapses to one shell command:

```bash
mforth run examples/blink.fs --serve [--port 7878] [--tick-ms 100]
```

Then point a browser at the URL printed on stdout (default
`http://127.0.0.1:7878`). The runner paces itself at `--tick-ms`
milliseconds per simulated tick so events fire at a rate the eye can
track; without pacing, `blink.fs` would saturate the event log in
milliseconds.

Skip to [What the viz shows](#what-the-viz-shows) — the browser-side
experience is the same either way.

## Path B — standalone embedding (works today)

The `VizServer` class in `src/mforth/viz/server.py` is the underlying
mechanism; the future `--serve` flag is purely the CLI surface on
top. You can embed it directly today:

```python
# tools/serve_viz.py — drop this anywhere on your PYTHONPATH.
import time
from pathlib import Path

from mforth.backend.runner import Runner
from mforth.viz.server import VizServer

SOURCE = Path("examples/blink.fs")
HTTP_PORT = 7878   # browser hits this
WS_PORT = 7879    # client convention: wsPort = httpPort + 1

runner = Runner.from_path(SOURCE)
srv = VizServer(runner.executor.world, http_port=HTTP_PORT, ws_port=WS_PORT)
srv.start()
print(f"Viz: http://127.0.0.1:{srv.http_port}")

try:
    while True:
        runner.run_once()
        time.sleep(0.1)   # pacing — one tick per 100 ms
except KeyboardInterrupt:
    pass
finally:
    srv.stop()
```

Run it:

```bash
python tools/serve_viz.py
```

Then open `http://127.0.0.1:7878` in your browser.

> **Why two ports?** HTTP serves the static `index.html` / `app.js` /
> `app.css`; the WebSocket carries the event stream. The viz client
> defaults to `wsPort = httpPort + 1` when no `?wsPort=N` query
> parameter is present — keep that convention or pass an explicit
> `?wsPort=NNNN` in the URL.

> **Why pacing?** `Runner.run_once` executes one mlog auto-loop
> iteration as fast as Python can; without a `time.sleep` between
> iterations the browser sees a blur. 100 ms per tick is comfortable
> for human inspection; drop it lower if you want to stress-test the
> protocol.

## What the viz shows

Once the browser connects, the page is split into four panes:

- **World pane** — every linked block from the sidecar, drawn as a
  tile labelled with its mforth name and its in-game type
  (`message`, `display`, `switch`, `sensor`, etc.). Tile state
  updates live as events fire — message blocks show their current
  text; switches show on/off; sensors show their last read value.
  Click a tile to open the **block detail** sub-pane with the full
  state dictionary.
- **Forth pane** — the source text of the `.fs` file with the
  currently-executing term highlighted. Source plumb-through is
  staged behind `mforth-10t.22`; until then the pane is empty and
  the rest of the viz still works.
- **Stack pane** — the data stack live, synthesised from
  `VariableWriteEvent`s on the `s0..sN` slot variables the codegen
  uses. Top of stack on the right.
- **Event log** — every event the server pushes, newest at the top,
  with a small icon per `event_type`. Unknown event types still
  appear here verbatim — the client deliberately does not enumerate
  the event catalog, so new event subclasses are
  forward-compatible.

A connection-status dot in the corner reads `connecting`,
`connected`, or `reconnecting` — the client auto-reconnects with
exponential backoff (500 ms → 15 s cap) if the server restarts.

## Driving execution from the browser

The client exposes three control buttons — **pause**, **resume**,
**step** — that send `{"type": "control", "cmd": ...}` frames. Until
`mforth-10t.22` wires the queue into the executor, these are
no-op-with-ack: the server records the command on
`srv.control_commands` and pushes a `control_ack` back to confirm
receipt. Useful today as a smoke test that the control channel is
healthy; useful tomorrow as the actual pause/resume/step surface.

For the full event-payload shape — what fields each
`MessagePrintEvent` / `SensorReadEvent` / etc. carries — see
[reference/events.md](../reference/events.md). For the wire-level
protocol (the JSON envelope shape), the docstring at the top of
`src/mforth/viz/server.py` is the authoritative source.

## Troubleshooting

- **`OSError: [Errno 98] Address already in use` on startup.** Either
  another process holds the port or a previous `VizServer` didn't
  clean up. Pick a different port (`HTTP_PORT = 7888` etc.), or pass
  `http_port=0` / `ws_port=0` to let the OS assign free ports — then
  read the resolved values back off `srv.http_port` / `srv.ws_port`
  and print them.
- **Page loads but stays blank / "connecting".** Open browser
  devtools → **Network** → **WS**. If the WebSocket connection is
  failing, the most common cause is a port mismatch — the client
  computed `httpPort + 1` for the WS port but you started the server
  on something else. Hit the URL with an explicit
  `?wsPort=NNNN` query parameter, or align your `WS_PORT` value with
  the client's default.
- **Events never appear in the log.** Check that
  `runner.run_once()` is actually being called in your loop — without
  the runner ticking, `MockWorld.events` never emits and the viz
  has nothing to push. A snapshot frame on initial connect is
  always sent, so a populated **World pane** with an empty event log
  usually means the runner stalled, not the viz.
- **Cache wedged after a viz update.** The static files are served
  with stdlib defaults (no cache-control headers), but browsers
  sometimes hold onto an old `app.js`. Hard-reload
  (Ctrl-Shift-R / Cmd-Shift-R) or open in a private window.

## What to read next

- [Reference / Events](../reference/events.md) — the catalogue of
  every event type the world emits and the viz renders.
- [Reference / Sidecar schema](../reference/sidecar-schema.md) — what
  goes into the `.world.toml` whose links populate the **World
  pane**.
- [Tutorials / Writing mforth for Mindustry](../tutorials/writing-mforth-for-mindustry.md) —
  the guided walkthrough that uses the viz as its teaching surface.
