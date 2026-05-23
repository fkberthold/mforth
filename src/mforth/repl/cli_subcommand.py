"""CLI subcommand registration for ``mforth repl`` (bead mforth-10t.13).

Follows the registry pattern established by mforth-326 (drawer
``drawer_mforth_decisions_85f5383552bbb0d611c8c989``):

* Each subcommand lives in its own module.
* The module calls :func:`mforth.cli.register_subcommand` at import time.
* ``mforth.cli._load_subcommands`` adds one import line per subcommand
  module — no edits to ``main`` itself.

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


if "repl" not in __import__("mforth.cli", fromlist=["_REGISTRY"])._REGISTRY:
    register_subcommand(
        "repl",
        help="Drop to an interactive mforth REPL prompt.",
        configure_parser=_configure_repl_parser,
        handler=_handle_repl,
    )
