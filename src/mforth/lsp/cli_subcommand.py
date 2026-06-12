"""CLI subcommand registration for `mforth lsp`.

Bead mforth-10t.23, converged to the .14 explicit-``register()`` pattern
by bead mforth-d1o. Importing this module is safe but does NOT auto-
register the subcommand — callers must invoke :func:`register` to
populate the shared :mod:`mforth.cli` registry. This mirrors
``cli_run.py`` (.14) and ``cli_compile.py`` (.19):

* Each subcommand lives in its own module.
* The module exposes a ``register()`` callable that
  :func:`mforth.cli._load_subcommands` invokes after import. The
  explicit call survives pytest's registry-clear-and-replay isolation
  pattern — a module-import side-effect would not, because a cached
  module re-import is a no-op and the registration would never re-run.
* ``register()`` is idempotent: a second call after a registry clear
  returns early instead of raising the duplicate-name ``ValueError``.

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


def register() -> None:
    """Register the ``lsp`` subcommand on the shared CLI registry.

    Called from :func:`mforth.cli._load_subcommands`. Idempotent — a
    second call after a registry clear (the test pattern from
    ``test_cli.py`` / ``test_lsp_diagnostics.py``) re-registers without
    raising the duplicate-name ``ValueError``, so test isolation is
    preserved without forcing every reset helper to know about every
    subcommand module.
    """
    import mforth.cli as _cli_mod

    if "lsp" in _cli_mod._REGISTRY:
        return
    register_subcommand(
        "lsp",
        help="Run the mforth language server over stdio.",
        configure_parser=_configure_lsp_parser,
        handler=_handle_lsp,
    )


# Auto-register on import — so callers who `from mforth.lsp import
# cli_subcommand` without going through `_load_subcommands` still see the
# side effect. Guarded against duplicate registration so re-imports are
# safe.
register()
