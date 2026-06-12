"""Integration smoke test for the web viz HTTP surface (bead mforth-10t.20).

The unit tests in tests/unit/test_viz_server.py drive the WebSocket
protocol contract end-to-end. This smoke test focuses on the HTTP
static-file boundary — booting the real server and confirming
``/index.html`` returns 200 with the basic shell markup the viz client
(bead .21) will extend.
"""

from __future__ import annotations

import socket
import urllib.request

import pytest

from mforth.backend.world import MockWorld
from mforth.viz.server import VizServer


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server():
    srv = VizServer(world=MockWorld(), http_port=_free_port(), ws_port=_free_port())
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


def test_index_html_returns_200_with_shell_markup(server: "VizServer") -> None:
    url = f"http://127.0.0.1:{server.http_port}/index.html"
    with urllib.request.urlopen(url, timeout=2.0) as r:
        assert r.status == 200
        body = r.read().decode("utf-8")
    assert "<html" in body.lower()
    assert "mforth" in body.lower()


def test_root_path_serves_index(server: "VizServer") -> None:
    """`/` MUST also serve the index shell (convenience for browser users)."""
    url = f"http://127.0.0.1:{server.http_port}/"
    with urllib.request.urlopen(url, timeout=2.0) as r:
        assert r.status == 200
        body = r.read().decode("utf-8")
    assert "<html" in body.lower()


def test_static_app_js_and_css_reachable(server: "VizServer") -> None:
    """`app.js` and `app.css` static assets MUST be reachable (200)."""
    for path in ("/app.js", "/app.css"):
        url = f"http://127.0.0.1:{server.http_port}{path}"
        with urllib.request.urlopen(url, timeout=2.0) as r:
            assert r.status == 200, path


def test_index_html_contains_all_five_pane_ids(server: "VizServer") -> None:
    """Bead mforth-10t.21: index.html must wire up the five-pane client.

    The viz client is browser code (vanilla JS, no framework, no build
    step) — pytest cannot drive a headless browser as part of the
    pinned suite. This test acts as the DOM-contract gate: every pane
    the client manipulates must exist in the served HTML by its
    documented id, so the JS can find it via getElementById on load.

    Manual end-to-end test pages live in tests/manual/viz/ (drive a
    browser by hand).
    """
    url = f"http://127.0.0.1:{server.http_port}/index.html"
    with urllib.request.urlopen(url, timeout=2.0) as r:
        body = r.read().decode("utf-8")
    # Five pane ids — world, block-detail, forth, stack, event-log.
    for pane_id in (
        "mforth-world-pane",
        "mforth-block-detail",
        "mforth-forth-pane",
        "mforth-stack-pane",
        "mforth-event-log",
    ):
        assert f'id="{pane_id}"' in body, f"missing pane: {pane_id}"
    # Control bar buttons (pause/resume/step) — wired to the
    # client→server control protocol shipped in bead .20.
    for btn_id in (
        "mforth-ctrl-pause",
        "mforth-ctrl-resume",
        "mforth-ctrl-step",
    ):
        assert f'id="{btn_id}"' in body, f"missing control button: {btn_id}"
    # Tick counter (prominent) — advanced by WaitEvent per the .20
    # protocol notes.
    assert 'id="mforth-tick"' in body


def test_app_js_under_500_lines(server: "VizServer") -> None:
    """JS budget: under 500 lines per bead mforth-10t.21 description."""
    url = f"http://127.0.0.1:{server.http_port}/app.js"
    with urllib.request.urlopen(url, timeout=2.0) as r:
        body = r.read().decode("utf-8")
    line_count = body.count("\n") + (0 if body.endswith("\n") else 1)
    assert line_count < 500, f"app.js is {line_count} lines (budget: <500)"


# ---------------------------------------------------------------------------
# Bead mforth-10t.22 — `mforth run --serve` wiring
#
# These tests pin the seam: the viz launcher boots a VizServer bound to a
# Runner's MockWorld and subscribes it to the run's EventStream. The
# headline assertion (per the bead's acceptance) is that running a program
# that PRINTFLUSHes streams a ``MessagePrintflushEvent`` to the viz
# subscriber. We assert at the EventStream subscription boundary (no live
# socket required), so the test is deterministic and browser-free.
# ---------------------------------------------------------------------------

EXAMPLES_DIR = (
    __import__("pathlib").Path(__file__).resolve().parents[2] / "examples"
)


def test_launch_viz_subscribes_to_runner_event_stream() -> None:
    """``launch_viz`` MUST attach the VizServer's event subscriber to the
    runner's ``executor.world.events`` stream, on ephemeral ports."""
    from mforth.backend.runner import Runner
    from mforth.viz.launcher import launch_viz

    runner = Runner.from_path(EXAMPLES_DIR / "blink.fs")
    stream = runner.executor.world.events
    before = len(stream.subscribers)

    srv = launch_viz(runner, port=0)
    try:
        # A subscriber was added, and it is the VizServer's own callback.
        assert len(stream.subscribers) == before + 1
        assert srv._subscriber in stream.subscribers
        # Ephemeral binding resolved to a real port.
        assert srv.http_port > 0
        assert srv.ws_port > 0
    finally:
        srv.stop()
    # stop() detaches the subscriber cleanly.
    assert srv._subscriber not in stream.subscribers


def test_serve_streams_printflush_event_to_viz_subscriber() -> None:
    """Running blink.fs once with the viz attached MUST deliver at least
    one ``MessagePrintflushEvent`` to the viz subscriber. We tap the same
    EventStream the viz subscribes to (no browser, no socket)."""
    from mforth.backend.runner import Runner
    from mforth.backend.world import MessagePrintflushEvent
    from mforth.viz.launcher import launch_viz

    runner = Runner.from_path(EXAMPLES_DIR / "blink.fs")
    stream = runner.executor.world.events

    received: list = []
    stream.subscribe(received.append)

    srv = launch_viz(runner, port=0)
    try:
        runner.run_once()
    finally:
        srv.stop()

    assert any(isinstance(e, MessagePrintflushEvent) for e in received), (
        "expected a MessagePrintflushEvent to flow through the EventStream "
        "the viz is subscribed to"
    )


def test_run_handler_serve_flag_boots_viz_and_executes() -> None:
    """The ``run`` CLI handler MUST, when ``--serve`` is set, boot the viz
    and execute the program. Driven via the handler (not a subprocess) with
    ``--no-loop`` so it terminates, ``--port 0`` for ephemeral binding, and
    ``--tick-ms 0`` so no real time is spent."""
    import argparse

    from mforth import cli_run

    captured: dict = {}
    real_launch = cli_run.launch_viz

    def _spy_launch(runner, port, host="127.0.0.1"):
        srv = real_launch(runner, port=port, host=host)
        captured["server"] = srv
        captured["stream"] = runner.executor.world.events
        return srv

    cli_run.launch_viz = _spy_launch
    try:
        args = argparse.Namespace(
            source=str(EXAMPLES_DIR / "blink.fs"),
            no_loop=True,
            serve=True,
            port=0,
            tick_ms=0,
        )
        rc = cli_run._handle_run(args)
    finally:
        cli_run.launch_viz = real_launch

    assert rc == 0
    # The viz was actually booted and wired to the run's EventStream.
    srv = captured["server"]
    stream = captured["stream"]
    assert srv.http_port > 0
    # Handler tore the server down on exit (subscriber detached).
    assert srv._subscriber not in stream.subscribers
