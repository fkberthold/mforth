"""Viz launcher — wire a :class:`~mforth.viz.server.VizServer` onto a
running :class:`~mforth.backend.runner.Runner` (bead mforth-10t.22).

This is the small glue layer that keeps ``mforth.cli_run`` clean: the
``run`` subcommand's ``--serve`` flag calls :func:`launch_viz`, which
boots the HTTP + WebSocket viz server bound to the runner's
:class:`~mforth.backend.world.MockWorld` and subscribes it to that
world's :class:`~mforth.backend.world.EventStream`.

The EventStream seam
====================

A :class:`Runner` owns an :class:`~mforth.backend.host.Executor`, which
owns a :class:`~mforth.backend.world.MockWorld`, which owns the single
:class:`~mforth.backend.world.EventStream` at ``world.events``. Every
Mindustry primitive (PRINT, PRINTFLUSH, WAIT, SENSOR, ...) emits onto
that stream. :meth:`VizServer.start` calls ``world.events.subscribe``,
so once :func:`launch_viz` returns, every event the run produces is
broadcast to connected browser clients — the same seam the integration
tests and the LSP runtime diagnostics plug into.

Port model
==========

A single ``port`` is exposed at the CLI surface; it becomes the HTTP
port. The WebSocket server takes an ephemeral port (``0``) so a user
only has to think about one number, and the served ``app.js`` discovers
the WS port from the snapshot handshake. Pass ``port=0`` (the test
default) to let the OS pick the HTTP port too — handy for parallel test
runs that must not collide on a fixed port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mforth.viz.server import VizServer

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mforth.backend.runner import Runner


def launch_viz(
    runner: "Runner", *, port: int = 7878, host: str = "127.0.0.1"
) -> VizServer:
    """Boot a :class:`VizServer` bound to ``runner``'s MockWorld and
    subscribe it to the run's EventStream.

    Parameters
    ----------
    runner
        The loaded :class:`~mforth.backend.runner.Runner`. Its
        ``executor.world`` (and thus ``executor.world.events``) is the
        live world the viz mirrors.
    port
        HTTP port for the static viz client. ``0`` asks the OS for a
        free port (test-friendly; resolved port is on
        ``server.http_port`` after this returns). The WebSocket port is
        always ephemeral.
    host
        Bind address. Defaults to loopback so ``--serve`` never exposes
        the viz beyond the local machine.

    Returns
    -------
    VizServer
        The started server. The caller owns its lifecycle and MUST call
        :meth:`VizServer.stop` when the run ends — ``stop`` detaches the
        event subscriber and joins the server threads.
    """
    server = VizServer(
        world=runner.executor.world,
        http_port=port,
        ws_port=0,
        host=host,
    )
    server.start()
    return server


__all__ = ["launch_viz"]
