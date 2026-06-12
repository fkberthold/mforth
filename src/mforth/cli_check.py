"""CLI subcommand for ``mforth check`` (bead mforth-roz.1).

The auto-checker that the "Learn Forth with mforth" tutorial is built
on. A learner writes a ``.fs`` solving an exercise, runs ``mforth check
<file>``, and gets instant pass/fail feedback — no pytest, no scripts.

Design drawer: ``drawer_mforth_decisions_00f669348c36c8702bb88dcc``.

Surface
=======

* ``mforth check <solution.fs>`` — read the file, extract its
  ``\\ @exercise <id>`` marker, load the bundled spec, run every case
  (``<learner code>\\n<driver>``) through the host
  :class:`~mforth.backend.runner.Runner`, and compare the printed
  strings to the spec's ``expect``. Prints ``✓ <id> — N/N cases pass``
  (exit 0) or, on the first failure, ``✗ case K: <driver> → printed
  "X", expected "Y"`` plus the hint (exit 1).
* ``mforth check --list`` — enumerate bundled ids + prompts.
* ``mforth check --scaffold <id>`` — write a starter ``<name>.fs`` stub
  (marker + prompt-as-comment + a TODO) to the cwd. Refuses to clobber.
* ``mforth check --solution <id>`` — print the reference solution.

Execution seam
==============

Reuses the EXISTING host backend with no new execution path: each case
is materialized as a temp ``<learner>\\n<driver>.fs`` (+ the spec's
inline ``sidecar`` written as the sibling ``.world.toml`` when present,
else a minimal default — none), then run via
:meth:`Runner.from_path` + :meth:`Runner.run_once`. Printed output is
the ordered sequence of :class:`MessagePrintEvent` ``text`` payloads on
``runner.executor.world.events`` — i.e. exactly what ``.`` and ``PRINT``
surface. This is the same Runner→MockWorld→EventStream path the
equivalence harness drives, so a passing check means the learner's word
behaves identically on the host REPL (and, by the headline equivalence
property, when compiled to mlog).

Registration follows the explicit-``register()`` pattern (mirror
:mod:`mforth.cli_compile`): importing this module does not register the
subcommand; :func:`register` does, and is called from
:func:`mforth.cli._load_subcommands`. Idempotent across the
registry-clear-and-replay test-isolation loop.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mforth import exercises
from mforth.backend.runner import Runner, RunnerError
from mforth.backend.sidecar import SidecarError
from mforth.backend.world import MessagePrintEvent
from mforth.cli import register_subcommand
from mforth.dictionary import UnresolvedWordError
from mforth.lex import LexError
from mforth.parse import ParseError
from mforth.stackcheck import StackError


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExerciseMarkerError(Exception):
    """Raised when a learner ``.fs`` has no ``\\ @exercise <id>`` marker."""


# The marker lives in an mforth line comment (a standalone ``\`` word
# followed by whitespace; the lexer DISCARDS line comments, so we read the
# id straight off the raw source text with this regex rather than via the
# token stream). Tolerant of leading blank lines and extra spacing around
# both the backslash and the id.
_MARKER_RE = re.compile(
    r"^\s*\\\s*@exercise\s+(\S+)\s*$",
    re.MULTILINE,
)


def extract_exercise_id(source_text: str) -> str:
    """Return the exercise id from the ``\\ @exercise <id>`` marker line.

    Raises
    ------
    ExerciseMarkerError
        If no marker line is present.
    """
    m = _MARKER_RE.search(source_text)
    if m is None:
        raise ExerciseMarkerError(
            "no '\\ @exercise <id>' marker found — a solution file must "
            "carry one metadata line, e.g. '\\ @exercise forth-101/01-double'"
        )
    return m.group(1)


# ---------------------------------------------------------------------------
# Check result records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseFailure:
    """A single failing case within a check run."""

    index: int  # 1-based case number
    driver: str
    printed: list[str]
    expect: list[str]


@dataclass(frozen=True)
class CheckResult:
    """The outcome of checking one solution against its spec."""

    exercise_id: str
    total: int
    num_passed: int
    failures: list[CaseFailure] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.num_passed == self.total and self.total > 0


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------


def _printed_strings(runner: Runner) -> list[str]:
    """Collect the ordered ``MessagePrintEvent.text`` payloads — exactly
    what ``.`` and ``PRINT`` surface on the event stream."""
    return [
        e.text
        for e in runner.executor.world.events
        if isinstance(e, MessagePrintEvent)
    ]


def _run_case(learner_code: str, case: exercises.Case, sidecar: Optional[str]) -> list[str]:
    """Run ``<learner_code>\\n<driver>`` once against a MockWorld and return
    the ordered printed strings.

    The combined program is written to a temp ``.fs`` (plus the spec's
    inline ``sidecar`` as the sibling ``.world.toml`` when present) so the
    existing :meth:`Runner.from_path` sidecar-resolution path is reused
    verbatim — no second sidecar codepath to keep in sync.
    """
    program = f"{learner_code.rstrip()}\n{case.driver}\n"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        fs_path = tmp_dir / "exercise.fs"
        fs_path.write_text(program, encoding="utf-8")
        if sidecar is not None:
            (tmp_dir / "exercise.world.toml").write_text(sidecar, encoding="utf-8")
        runner = Runner.from_path(fs_path)
        runner.run_once()
        return _printed_strings(runner)


def run_check(solution_path: Path) -> CheckResult:
    """Check the solution at ``solution_path`` against its bundled spec.

    Reads the file, extracts the ``\\ @exercise <id>`` marker, loads the
    spec, and runs every case. Returns a :class:`CheckResult`.

    Raises
    ------
    ExerciseMarkerError
        If the file carries no ``@exercise`` marker.
    exercises.UnknownExerciseError
        If the marker names an id with no bundled spec.
    RunnerError / LexError / ParseError / UnresolvedWordError / StackError /
    SidecarError
        Propagated from the host pipeline — the CLI handler formats these
        as a one-line learner-facing error.
    """
    source_text = solution_path.read_text(encoding="utf-8")
    exercise_id = extract_exercise_id(source_text)
    spec = exercises.load_spec(exercise_id)

    failures: list[CaseFailure] = []
    num_passed = 0
    for i, case in enumerate(spec.cases, start=1):
        printed = _run_case(source_text, case, spec.sidecar)
        if printed == case.expect:
            num_passed += 1
        else:
            failures.append(
                CaseFailure(
                    index=i,
                    driver=case.driver,
                    printed=printed,
                    expect=case.expect,
                )
            )
    return CheckResult(
        exercise_id=exercise_id,
        total=len(spec.cases),
        num_passed=num_passed,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Scaffold body
# ---------------------------------------------------------------------------


def scaffold_text(spec: exercises.ExerciseSpec) -> str:
    """Return the starter-stub body for ``--scaffold <id>``: the marker, the
    prompt-as-comment, the hint, and a TODO for the learner."""
    lines = [
        f"\\ @exercise {spec.id}",
        f"\\ {spec.prompt}",
        f"\\ Hint: {spec.hint}",
        "",
        "\\ TODO: write your solution below, then run:  mforth check "
        f"{spec.name}.fs",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def _format_check_failure(spec_hint: str, result: CheckResult) -> str:
    """Render the first failure as the learner-facing ``✗`` block."""
    f = result.failures[0]
    printed = ", ".join(f'"{s}"' for s in f.printed) or "(nothing)"
    expect = ", ".join(f'"{s}"' for s in f.expect) or "(nothing)"
    return (
        f"✗ {result.exercise_id} — {result.num_passed}/{result.total} cases pass\n"
        f"  case {f.index}: {f.driver} → printed {printed}, expected {expect}\n"
        f"  hint: {spec_hint}"
    )


def _handle_check_solution(path: Path) -> int:
    if not path.exists():
        print(f"solution file not found: {path}", file=sys.stderr)
        return 1
    try:
        result = run_check(path)
    except ExerciseMarkerError as e:
        print(str(e), file=sys.stderr)
        return 1
    except exercises.UnknownExerciseError as e:
        print(str(e), file=sys.stderr)
        return 1
    except (
        LexError,
        ParseError,
        UnresolvedWordError,
        StackError,
        SidecarError,
        RunnerError,
    ) as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1

    if result.passed:
        print(f"✓ {result.exercise_id} — {result.num_passed}/{result.total} cases pass")
        return 0

    spec = exercises.load_spec(result.exercise_id)
    print(_format_check_failure(spec.hint, result), file=sys.stderr)
    return 1


def _handle_list() -> int:
    ids = exercises.list_ids()
    if not ids:
        print("(no bundled exercises)")
        return 0
    width = max(len(i) for i in ids)
    for ex_id in ids:
        spec = exercises.load_spec(ex_id)
        print(f"{ex_id.ljust(width)}  {spec.prompt}")
    return 0


def _handle_scaffold(exercise_id: str) -> int:
    try:
        spec = exercises.load_spec(exercise_id)
    except exercises.UnknownExerciseError as e:
        print(str(e), file=sys.stderr)
        return 1
    dest = Path.cwd() / f"{spec.name}.fs"
    if dest.exists():
        print(
            f"refusing to overwrite existing file: {dest.name} "
            "(remove it first if you want a fresh stub)",
            file=sys.stderr,
        )
        return 1
    dest.write_text(scaffold_text(spec), encoding="utf-8")
    print(f"wrote starter stub: {dest.name}")
    print(f"edit it, then run:  mforth check {dest.name}")
    return 0


def _handle_show_solution(exercise_id: str) -> int:
    try:
        text = exercises.load_solution_text(exercise_id)
    except exercises.UnknownExerciseError as e:
        print(str(e), file=sys.stderr)
        return 1
    # No trailing extra newline — the solution text already ends in one.
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _handle_check(args: argparse.Namespace) -> int:
    """Dispatch the ``check`` subcommand based on which mode was selected."""
    if args.list:
        return _handle_list()
    if args.scaffold is not None:
        return _handle_scaffold(args.scaffold)
    if args.solution is not None:
        return _handle_show_solution(args.solution)
    if args.source is None:
        # argparse can't express "one of {positional, --list, --scaffold,
        # --solution}" cleanly, so we enforce it here.
        print(
            "nothing to check — pass a <solution.fs>, or use --list / "
            "--scaffold <id> / --solution <id>",
            file=sys.stderr,
        )
        return 1
    return _handle_check_solution(Path(args.source))


# ---------------------------------------------------------------------------
# Argparse glue
# ---------------------------------------------------------------------------


def _configure_check_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help="path to a learner .fs solution carrying a '\\ @exercise <id>' marker",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list bundled exercise ids and their prompts, then exit",
    )
    parser.add_argument(
        "--scaffold",
        metavar="<id>",
        default=None,
        help="write a starter .fs stub for <id> into the current directory",
    )
    parser.add_argument(
        "--solution",
        metavar="<id>",
        default=None,
        help="print the reference solution for <id> to stdout",
    )


# ---------------------------------------------------------------------------
# Registry plumbing
# ---------------------------------------------------------------------------


def register() -> None:
    """Register the ``check`` subcommand on the shared CLI registry.

    Idempotent — a second call after a registry clear (the test-isolation
    pattern) re-registers without raising the duplicate-name
    :class:`ValueError`.
    """
    import mforth.cli as _cli_mod

    if "check" in _cli_mod._REGISTRY:
        return
    register_subcommand(
        "check",
        help="Check a tutorial exercise solution (and --list / --scaffold / --solution).",
        configure_parser=_configure_check_parser,
        handler=_handle_check,
    )
