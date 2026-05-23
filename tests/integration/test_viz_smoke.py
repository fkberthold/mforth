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
