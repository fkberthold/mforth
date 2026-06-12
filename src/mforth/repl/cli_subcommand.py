"""CLI subcommand registration for ``mforth repl`` (bead mforth-10t.13).

Converged to the .14 explicit-``register()`` pattern by bead mforth-d1o.
Importing this module is safe but does NOT auto-register the subcommand
— callers must invoke :func:`register` to populate the shared
:mod:`mforth.cli` registry. This mirrors ``cli_run.py`` (.14) and
``cli_compile.py`` (.19):

* Each subcommand lives in its own module.
* The module exposes a ``register()`` callable that
  :func:`mforth.cli._load_subcommands` invokes after import. The
  explicit call survives pytest's registry-clear-and-replay isolation
  pattern — a module-import side-effect would not, because a cached
  module re-import is a no-op and the registration would never re-run.
* ``register()`` is idempotent: a second call after a registry clear
  returns early instead of raising the duplicate-name ``ValueError``.

The ``repl`` subcommand takes one optional argument:

* ``--load <file.fs>`` — preload the named source file before dropping
  to the prompt. Definitions and variables in the file are available at
  the first prompt.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mforth.cli import register_subcommand


def _configure_repl_parser(parser: argparse.ArgumentParser) -> None:
    """``mforth repl [--load FILE]`` — interactive Forth prompt."""
    parser.add_argument(
        "--load",
        metavar="FILE",
        default=None,
        help="Preload the given .fs file before dropping to the prompt.",
    )


def _handle_repl(args: argparse.Namespace) -> int:
    """Boot the interactive REPL.

    Local import of the driver keeps the registration cheap (importing
    `mforth` for ``mforth --help`` doesn't pull readline or the host
    executor's full primitive table).
    """
    from mforth.repl.driver import run_interactive

    preload_source: str | None = None
    preload_file: str = "<preload>"
    if args.load:
        try:
            preload_source = Path(args.load).read_text()
            preload_file = args.load
        except OSError as e:
            print(f"mforth repl: cannot read --load file: {e}", file=sys.stderr)
            return 2

    return run_interactive(
        preload_source=preload_source, preload_file=preload_file
    )


def register() -> None:
    """Register the ``repl`` subcommand on the shared CLI registry.

    Called from :func:`mforth.cli._load_subcommands`. Idempotent — a
    second call after a registry clear (the test pattern from
    ``test_cli.py`` / ``test_repl_prompt.py``) re-registers without
    raising the duplicate-name ``ValueError``, so test isolation is
    preserved without forcing every reset helper to know about every
    subcommand module.
    """
    import mforth.cli as _cli_mod

    if "repl" in _cli_mod._REGISTRY:
        return
    register_subcommand(
        "repl",
        help="Drop to an interactive mforth REPL prompt.",
        configure_parser=_configure_repl_parser,
        handler=_handle_repl,
    )


# Auto-register on import — so callers who `from mforth.repl import
# cli_subcommand` without going through `_load_subcommands` still see the
# side effect. Guarded against duplicate registration so re-imports are
# safe.
register()
