"""`python -m mforth.lsp` — convenience entry point for the stdio LSP.

Bead mforth-10t.23. Mirrors the `python -m mforth` pattern from
mforth-326 so editors that prefer module-style invocations (Helix,
some neovim configs) can start the LSP without relying on the
`[project.scripts]` console-script being on PATH.

The `mforth lsp` subcommand (registered via
:mod:`mforth.lsp.cli_subcommand`) is the canonical entry point;
this module just delegates to :func:`mforth.lsp.server.serve_stdio`.
"""

from __future__ import annotations

import sys

from mforth.lsp.server import serve_stdio


def main() -> int:
    return serve_stdio()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
