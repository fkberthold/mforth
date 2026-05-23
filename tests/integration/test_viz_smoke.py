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
