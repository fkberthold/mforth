"""mforth command-line interface — argparse scaffold with a subcommand registry.

Bead mforth-326. This module is the single entry point referenced by
``[project.scripts]`` (``mforth = "mforth.cli:main"``) and by
``src/mforth/__main__.py`` (so ``python -m mforth`` works).

Architecture
============

The CLI is built on stdlib :mod:`argparse` — no new runtime deps. The
project deliberately minimises dependencies (see pyproject.toml), and
the subcommand set in v1 (just ``version``) plus the planned v1
subcommands (``run``, ``compile``, ``repl``, ``lsp``, ``--serve``) all
fit comfortably inside argparse's subparser model.

Subcommand registry
-------------------

Subcommands register themselves via :func:`register_subcommand`. The
registry is module-level state:

.. code:: python

    from mforth.cli import register_subcommand

    def _configure(parser):
        parser.add_argument("source")
        parser.add_argument("-o", "--output", required=True)

    def _handler(args):
        ...  # compile args.source → args.output
        return 0

    register_subcommand(
        "compile",
        help="Compile a .fs file to mlog text",
        configure_parser=_configure,
        handler=_handler,
    )

For the registration to take effect, the module owning the subcommand
must be imported BEFORE :func:`main` builds the top-level parser. That
import lives inside :func:`_load_subcommands` — future subcommand beads
(.20 viz, .23 LSP, run, compile, repl) extend that function with a
single import line per new subcommand module. They do NOT edit
:func:`main` itself.

The version subcommand is itself wired through the registry (see the
bottom of this file) — no special-casing. This is load-bearing: it
proves the pattern is exercised by the v1 ship, so a future broken
integration surfaces immediately instead of silently.

Version resolution
------------------

:func:`_get_version` returns the version string. Preferred source is
:func:`importlib.metadata.version` (single source of truth for
installed packages). When that raises
:exc:`importlib.metadata.PackageNotFoundError` — which happens in some
editable-install configurations that lose dist-info — the function
falls back to :data:`mforth.__version__`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from importlib import metadata as _metadata
from typing import Callable, Optional, Sequence

import mforth


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _SubcommandEntry:
    """Internal record for a registered subcommand."""

    help: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    handler: Callable[[argparse.Namespace], int]


_REGISTRY: dict[str, _SubcommandEntry] = {}


def register_subcommand(
    name: str,
    *,
    help: str,
    configure_parser: Callable[[argparse.ArgumentParser], None],
    handler: Callable[[argparse.Namespace], int],
) -> None:
    """Register a subcommand in the module-level registry.

    Parameters
    ----------
    name
        The subcommand name as it appears on the command line
        (``mforth <name> ...``). Must be unique across all registered
        subcommands.
    help
        One-line help text shown in ``mforth --help``.
    configure_parser
        Callable that takes the :class:`argparse.ArgumentParser` for
        this subcommand and adds positional arguments, options, etc.
        Called once at parser-build time. May be a no-op for
        subcommands with no arguments.
    handler
        Callable that takes the parsed :class:`argparse.Namespace` and
        returns an exit code (``int``). Returning ``None`` is treated
        as ``0`` by :func:`main`.

    Raises
    ------
    ValueError
        If ``name`` is already registered. This catches the worst-case
        parallel-bead bug — two subcommand beads silently overwriting
        each other's registration.
    """
    if name in _REGISTRY:
        raise ValueError(
            f"subcommand {name!r} is already registered; "
            "two subcommand modules cannot share a name"
        )
    _REGISTRY[name] = _SubcommandEntry(
        help=help,
        configure_parser=configure_parser,
        handler=handler,
    )


# --------------------------------------------------------------------------
# Version subcommand
# --------------------------------------------------------------------------


def _get_version() -> str:
    """Return the mforth version string.

    Prefer :func:`importlib.metadata.version`. Fall back to
    :data:`mforth.__version__` when metadata is unavailable (editable
    installs sometimes miss dist-info).
    """
    try:
        return _metadata.version("mforth")
    except _metadata.PackageNotFoundError:
        return mforth.__version__


def _configure_version_parser(parser: argparse.ArgumentParser) -> None:
    """``version`` has no arguments of its own."""
    # No-op. Kept as a named function so the registry pattern is
    # exercised the same way every subcommand will exercise it.
    return None


def _handle_version(args: argparse.Namespace) -> int:
    """Print ``mforth <version>`` to stdout and exit 0."""
    print(f"mforth {_get_version()}")
    return 0


# --------------------------------------------------------------------------
# Subcommand loader
# --------------------------------------------------------------------------


def _load_subcommands() -> None:
    """Populate the registry with every known subcommand.

    Called by :func:`main` before the parser is built. Future
    subcommand beads add their import + registration here — one line
    per subcommand module. They do NOT edit :func:`main`.

    The version subcommand is registered inline (no separate module)
    because it's trivial and proves the registry pattern works
    end-to-end in the v1 ship. Larger subcommands (run, compile, lsp,
    viz) will live in their own modules under ``mforth/`` and register
    themselves at module import time; :func:`_load_subcommands` will
    grow one import line per such module.
    """
    if "version" not in _REGISTRY:
        register_subcommand(
            "version",
            help="Print the mforth package version and exit.",
            configure_parser=_configure_version_parser,
            handler=_handle_version,
        )
    # Future subcommand beads add their imports here:
    #   from mforth import run as _run_mod  # noqa: F401  -- side-effect import
    #   from mforth import compile as _compile_mod  # noqa: F401
    #   ... etc.
    from mforth.lsp import cli_subcommand as _lsp_cli  # noqa: F401  -- side-effect import
    from mforth import cli_run as _run_cli  # noqa: F401  -- side-effect import (bead .14)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser, iterating the registry for subparsers."""
    parser = argparse.ArgumentParser(
        prog="mforth",
        description=(
            "mforth — a pragmatic Forth dialect that compiles to "
            "Mindustry mlog."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="_subcommand",
        metavar="<subcommand>",
        required=True,
    )
    for name, entry in _REGISTRY.items():
        sub = subparsers.add_parser(name, help=entry.help, description=entry.help)
        entry.configure_parser(sub)
        sub.set_defaults(_handler=entry.handler)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns an integer exit code.

    Parameters
    ----------
    argv
        Argument list (without the program name). When ``None``,
        argparse falls back to :data:`sys.argv[1:]`.
    """
    _load_subcommands()
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args._handler
    rc = handler(args)
    return 0 if rc is None else int(rc)
