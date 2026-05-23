"""CLI subcommand registration for `mforth lsp`.

Bead mforth-10t.23. Importing this module side-effect-registers the
``lsp`` subcommand on the shared :mod:`mforth.cli` registry. The
registration follows the pattern established by mforth-326 (drawer
``drawer_mforth_decisions_85f5383552bbb0d611c8c989``):

* Each subcommand lives in its own module.
* The module calls :func:`mforth.cli.register_subcommand` at import
  time.
* ``mforth.cli._load_subcommands`` adds one import line per
  subcommand module — no edits to ``main`` itself.

The ``lsp`` subcommand is argumentless (stdio LSP, no positional
args, no options in v1). The handler boots a fresh server via
:func:`mforth.lsp.server.serve_stdio` and returns the server's exit
code.
"""

from __future__ import annotations

import argparse

from mforth.cli import register_subcommand


def _configure_lsp_parser(parser: argparse.ArgumentParser) -> None:
    """The `lsp` subcommand takes no arguments — stdio LSP."""
    # Kept as a named function so the registry pattern is exercised
    # the same way every subcommand exercises it (matches the
    # `_configure_version_parser` shape from mforth-326).
    return None


def _handle_lsp(args: argparse.Namespace) -> int:
    """Boot the stdio LSP server."""
    # Local import so the test harness can import this module without
    # forcing a server instantiation at registration time. The real
    # import cost (lsprotocol, attrs, cattrs) only fires when the
    # user actually invokes `mforth lsp`.
    from mforth.lsp.server import serve_stdio

    return serve_stdio()


if "lsp" not in __import__("mforth.cli", fromlist=["_REGISTRY"])._REGISTRY:
    register_subcommand(
        "lsp",
        help="Run the mforth language server over stdio.",
        configure_parser=_configure_lsp_parser,
        handler=_handle_lsp,
    )
