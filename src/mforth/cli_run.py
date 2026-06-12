"""CLI subcommand registration for ``mforth run`` (bead mforth-10t.14).

Importing this module side-effect-registers the ``run`` subcommand on
the shared :mod:`mforth.cli` registry. The registration follows the
pattern established by mforth-326 (drawer
``drawer_mforth_decisions_85f5383552bbb0d611c8c989``):

* Each subcommand lives in its own module.
* The module calls :func:`mforth.cli.register_subcommand` at import
  time, guarded by an ``if "run" not in _REGISTRY`` check so re-imports
  (from test reset-and-replay loops) don't raise the duplicate-name
  ``ValueError``.
* ``mforth.cli._load_subcommands`` adds one import line per subcommand
  module — no edits to ``main`` itself.

Surface
=======

The ``run`` subcommand takes one positional ``source`` (path to a
``.fs`` file) and one optional flag ``--no-loop`` (execute exactly
once instead of auto-looping). The handler:

* Translates pipeline errors (lex / parse / resolve / stackcheck) and
  :class:`SidecarError` / :class:`RunnerError` into a stderr message +
  ``exit 1``.
* On ``KeyboardInterrupt`` during the auto-loop, returns POSIX SIGINT
  exit code 130 with a one-line summary on stderr.

The ``configure_parser`` shape is part of the bead .14 contract — bead
mforth-10t.22 (``--serve`` web viz) extends this subcommand with one
additional flag and one additional hook in the handler. Splice points:

* ``configure_parser`` — add ``parser.add_argument("--serve", ...)``.
* ``_handle_run`` — after ``Runner.from_path(...)`` and before the
  execute call, subscribe the viz server to
  ``runner.executor.world.events``; tear down on the handler's exit.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mforth.backend.host import ExecutionError
from mforth.backend.runner import Runner, RunnerError
from mforth.backend.sidecar import SidecarError
from mforth.cli import register_subcommand
from mforth.dictionary import UnresolvedWordError
from mforth.lex import LexError
from mforth.parse import ParseError
from mforth.stackcheck import StackError
from mforth.viz.launcher import launch_viz

_SIGINT_EXIT_CODE = 130  # POSIX: 128 + SIGINT (2)
_DEFAULT_VIZ_PORT = 7878  # bead .22: default HTTP port for `--serve`
_DEFAULT_TICK_MS = 100  # bead .22: default pacing — one iteration per 100ms


def _configure_run_parser(parser: argparse.ArgumentParser) -> None:
    """Configure the ``run`` subparser.

    Surface:

    * ``source`` (positional) — path to a ``.fs`` file.
    * ``--no-loop`` (flag) — run the top-level sequence exactly once
      instead of auto-looping. Test-friendly; programs that have no
      ``WAIT`` would otherwise spin forever under the default
      auto-loop.

    Bead .22 extends this with ``--serve`` for the web viz; do not
    rename or reposition the existing args.
    """
    parser.add_argument(
        "source",
        type=str,
        help="path to a .fs source file",
    )
    parser.add_argument(
        "--no-loop",
        dest="no_loop",
        action="store_true",
        help="execute the top-level sequence once instead of auto-looping",
    )
    parser.add_argument(
        "--serve",
        dest="serve",
        action="store_true",
        help=(
            "launch the web visualizer (bound to 127.0.0.1) and stream the "
            "run's EventStream to connected browsers"
        ),
    )
    parser.add_argument(
        "--port",
        dest="port",
        type=int,
        default=_DEFAULT_VIZ_PORT,
        help=(
            "HTTP port for the --serve web viz (default %(default)s; 0 asks "
            "the OS for a free port). Ignored without --serve."
        ),
    )
    parser.add_argument(
        "--tick-ms",
        dest="tick_ms",
        type=int,
        default=_DEFAULT_TICK_MS,
        help=(
            "pacing delay in milliseconds between auto-loop iterations "
            "(default %(default)s) so a --serve viewer can watch. 0 disables "
            "pacing. Ignored without --serve and under --no-loop."
        ),
    )


def _format_pipeline_error(exc: Exception, source_path: Path) -> str:
    """Convert any pipeline error into a ``file:line:col: <message>``
    string suitable for stderr."""
    # LexError / ParseError carry bare .line + .col attributes.
    line = getattr(exc, "line", None)
    col = getattr(exc, "col", None)
    if line is not None and col is not None:
        return f"{source_path}:{line}:{col}: {getattr(exc, 'message', str(exc))}"
    # UnresolvedWordError / StackError / ExecutionError carry .src_loc
    # (a SrcLoc(file, line, col)) and stringify with the prefix already
    # in place, so just str(exc).
    src_loc = getattr(exc, "src_loc", None)
    if src_loc is not None:
        return str(exc)
    return f"{source_path}: {exc}"


def _handle_run(args: argparse.Namespace) -> int:
    """Boot a :class:`Runner` and execute the program.

    Returns an exit code:

    * ``0`` — clean completion (under ``--no-loop``) or clean SIGINT
      exit (under the auto-loop, exit code 130 is returned for SIGINT).
    * ``1`` — any pipeline / sidecar / runner-construction error.
    * ``130`` — SIGINT during the auto-loop (POSIX convention).
    """
    source_path = Path(args.source)
    try:
        runner = Runner.from_path(source_path)
    except RunnerError as e:
        print(str(e), file=sys.stderr)
        return 1
    except SidecarError as e:
        print(str(e), file=sys.stderr)
        return 1
    except (LexError, ParseError, UnresolvedWordError, StackError) as e:
        print(_format_pipeline_error(e, source_path), file=sys.stderr)
        return 1

    # --serve (bead .22): boot the web viz subscribed to the run's
    # EventStream. The server is torn down on every exit path via the
    # finally below — `VizServer.stop` detaches the subscriber + joins
    # the HTTP/WS threads, so a `--no-loop --serve` run leaves no
    # lingering daemons.
    viz_server = None
    if getattr(args, "serve", False):
        viz_server = launch_viz(
            runner, port=getattr(args, "port", _DEFAULT_VIZ_PORT)
        )
        print(f"Viz: http://127.0.0.1:{viz_server.http_port}", flush=True)

    try:
        if args.no_loop:
            try:
                runner.run_once()
            except ExecutionError as e:
                print(str(e), file=sys.stderr)
                return 1
            return 0

        # Pacing: when serving, sleep `--tick-ms` between iterations so a
        # human watching the viz can follow the auto-loop. Disabled (no
        # callback) when not serving or when tick-ms is 0, preserving the
        # bead .14 unpaced behaviour for headless runs + tests.
        on_iteration = None
        tick_ms = getattr(args, "tick_ms", _DEFAULT_TICK_MS)
        if viz_server is not None and tick_ms and tick_ms > 0:
            delay = tick_ms / 1000.0

            def on_iteration(_count: int, _delay: float = delay) -> None:
                time.sleep(_delay)

        try:
            iterations = runner.run_forever(on_iteration=on_iteration)
        except KeyboardInterrupt:
            # `Runner.run_forever` catches KeyboardInterrupt and returns
            # cleanly, but if a callback or realtime sleep raises one
            # outside that catch we still want the clean exit path.
            iterations = runner.iterations
        except ExecutionError as e:
            print(str(e), file=sys.stderr)
            return 1
        else:
            # Reaching the bottom of the try without a KeyboardInterrupt
            # only happens if `run_forever` returned (which currently only
            # happens via SIGINT or a callback raising). Treat as a
            # SIGINT-style exit so the summary path runs.
            pass

        tick = runner.executor.world.events.tick
        print(
            f"mforth: interrupted after {iterations} iteration(s); "
            f"simulated tick={tick}",
            file=sys.stderr,
        )
        return _SIGINT_EXIT_CODE
    finally:
        if viz_server is not None:
            viz_server.stop()


def register() -> None:
    """Register the ``run`` subcommand on the shared CLI registry.

    Called from :func:`mforth.cli._load_subcommands`. Idempotent — a
    second call after a registry clear (the test pattern from
    ``test_cli.py`` / ``test_repl_prompt.py`` / ``test_lsp_diagnostics.py``)
    re-registers without raising the duplicate-name ``ValueError``,
    so test isolation is preserved without forcing every reset helper
    to know about every subcommand module.
    """
    import mforth.cli as _cli_mod

    if "run" in _cli_mod._REGISTRY:
        return
    register_subcommand(
        "run",
        help="Execute a .fs file against MockWorld (with mlog auto-loop).",
        configure_parser=_configure_run_parser,
        handler=_handle_run,
    )


# Auto-register on import — so users who `from mforth import cli_run`
# without going through `_load_subcommands` still see the side effect.
# Guarded against duplicate registration so re-imports are safe.
register()
