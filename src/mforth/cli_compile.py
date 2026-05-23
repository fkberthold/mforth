"""CLI subcommand registration for ``mforth compile`` (bead mforth-10t.19).

Importing this module is safe but does NOT auto-register the subcommand
— callers must invoke :func:`register` to populate the shared CLI
registry. This is the .14 explicit-``register()`` pattern (drawer
``drawer_mforth_decisions_3512ddd6b37218dfaec1c812``): idempotent, safe
across pytest's registry-clear-and-replay isolation pattern.

Surface
=======

``mforth compile <source.fs> -o <output.mlog> [--emit-comments]``

* ``source`` (positional) — path to a ``.fs`` file. A sibling
  ``<source>.world.toml`` is loaded if it exists; absent sidecar means
  an empty :class:`WorldConfig` (consistent with ``mforth run``'s
  behaviour from bead .14).
* ``-o / --output`` (required) — destination path for the emitted mlog
  text. Overwrites the file if it exists.
* ``--emit-comments`` (flag, default off) — interleave per-term source
  location comments in the output. Off by default because the
  in-game mlog editor can choke on comment lines.

Pipeline
========

The handler runs::

    lex → parse → resolve → stackcheck → allocate_slots → emit → finalize

then writes the resulting text to ``args.output``. Any pipeline error
is formatted as ``file:line:col: <message>`` on stderr and exits 1.

Cross-bead notes
================

* The output round-trips through bead .29's golden harness body shape
  (single space-joined ``opcode op1 ... opN``; single trailing newline)
  with the addition of one header ``#`` comment line per the .19
  contract.
* The output is what bead .31's in-repo mlog interpreter consumes for
  the REPL ↔ mlog equivalence property — the headline test class.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from mforth.backend.mlog import allocate_slots, emit
from mforth.backend.mlog.finalize import SidecarSubstitutionError, finalize
from mforth.backend.sidecar import SidecarError, WorldConfig, load_sidecar
from mforth.cli import register_subcommand
from mforth.dictionary import UnresolvedWordError, UserVariable, resolve, standard_dictionary
from mforth.lex import LexError
from mforth.parse import ParseError, SrcLoc, parse
from mforth.stackcheck import StackError, stackcheck


# ---------------------------------------------------------------------------
# Argparse glue
# ---------------------------------------------------------------------------


def _configure_compile_parser(parser: argparse.ArgumentParser) -> None:
    """Configure the ``compile`` subparser.

    Surface intentionally narrow — ``source`` + ``--output`` are the v1
    must-haves; ``--emit-comments`` is the only optional knob. Future
    optimization-level flags (``-Ofast``, ``-Osize``) land with bead
    .39 / Tier C work.
    """
    parser.add_argument(
        "source",
        type=str,
        help="path to a .fs source file",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=str,
        help="destination path for the emitted .mlog text",
    )
    parser.add_argument(
        "--emit-comments",
        dest="emit_comments",
        action="store_true",
        help=(
            "interleave per-term source-location comments in the output "
            "(off by default — the in-game mlog editor can choke on "
            "comment lines)"
        ),
    )


# ---------------------------------------------------------------------------
# Error formatting (mirror cli_run.py for consistency)
# ---------------------------------------------------------------------------


def _format_pipeline_error(exc: Exception, source_path: Path) -> str:
    """Convert a pipeline error into a ``file:line:col: <message>``
    string suitable for stderr."""
    line = getattr(exc, "line", None)
    col = getattr(exc, "col", None)
    if line is not None and col is not None:
        return f"{source_path}:{line}:{col}: {getattr(exc, 'message', str(exc))}"
    src_loc = getattr(exc, "src_loc", None)
    if src_loc is not None:
        return str(exc)
    return f"{source_path}: {exc}"


def _sidecar_for(source_path: Path) -> Optional[Path]:
    """Return the sibling ``.world.toml`` path if it exists, else None."""
    candidate = source_path.with_suffix(".world.toml")
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _handle_compile(args: argparse.Namespace) -> int:
    """Compile ``args.source`` to ``args.output``. Returns exit code.

    Exit codes:
    * 0 — successful write.
    * 1 — any pipeline / sidecar / IO error.
    """
    source_path = Path(args.source)
    if not source_path.exists():
        print(f"source file not found: {source_path}", file=sys.stderr)
        return 1

    sidecar_path = _sidecar_for(source_path)
    try:
        world_config = (
            load_sidecar(sidecar_path) if sidecar_path is not None else WorldConfig()
        )
    except SidecarError as e:
        print(str(e), file=sys.stderr)
        return 1

    # Pre-seed dictionary with sidecar link names (mirrors Runner.from_path
    # from bead .14 so the host REPL and the compile path agree on the
    # bare-link-name idiom).
    dictionary = standard_dictionary()
    seed_loc = SrcLoc(
        str(sidecar_path) if sidecar_path is not None else str(source_path),
        1,
        1,
    )
    for spec in world_config.links:
        if spec.mforth_name not in dictionary:
            dictionary.add_variable(
                UserVariable(name=spec.mforth_name, src_loc=seed_loc)
            )

    try:
        text = source_path.read_text()
        program = parse(text, file=str(source_path))
        dictionary = resolve(program, dictionary=dictionary)
        result = stackcheck(program, dictionary=dictionary)
        slots = allocate_slots(result)
        instrs = emit(result, slots)
    except (LexError, ParseError, UnresolvedWordError, StackError) as e:
        print(_format_pipeline_error(e, source_path), file=sys.stderr)
        return 1
    except NotImplementedError as e:
        # Catch the emit-time cell-free / not-yet-supported errors so the
        # user gets a one-line stderr instead of a Python traceback.
        print(f"{source_path}: {e}", file=sys.stderr)
        return 1

    try:
        output_text = finalize(
            instrs,
            world_config=world_config,
            source_path=source_path,
            sidecar_path=sidecar_path,
            emit_comments=args.emit_comments,
        )
    except SidecarSubstitutionError as e:
        print(str(e), file=sys.stderr)
        return 1

    output_path = Path(args.output)
    try:
        output_path.write_text(output_text)
    except OSError as e:
        print(f"failed to write {output_path}: {e}", file=sys.stderr)
        return 1

    return 0


# ---------------------------------------------------------------------------
# Registry plumbing
# ---------------------------------------------------------------------------


def register() -> None:
    """Register the ``compile`` subcommand on the shared CLI registry.

    Called from :func:`mforth.cli._load_subcommands`. Idempotent — a
    second call after a registry clear (the pattern from .14 test
    isolation) re-registers without raising the duplicate-name
    :class:`ValueError`.
    """
    import mforth.cli as _cli_mod

    if "compile" in _cli_mod._REGISTRY:
        return
    register_subcommand(
        "compile",
        help="Compile a .fs file to mlog text.",
        configure_parser=_configure_compile_parser,
        handler=_handle_compile,
    )
