"""mforth interactive REPL — programmatic surface (bead mforth-10t.13).

The `mforth repl` subcommand drops the user at a Forth prompt. This module
owns the *programmatic* surface (`Repl.run_line`) that the interactive
driver wraps with `input()` + `readline`. Splitting the two lets pytest
exercise the contract without having to drive an interactive readline
session.

Pipeline per line
=================

The Repl holds a long-lived `Executor` carrying the persistent state
quadruple `(data_stack, return_stack, variables, world)` plus the
accumulated `Dictionary` of user definitions and VARIABLE declarations.

Each call to :meth:`Repl.run_line` does:

1. **Special-command intercept** — `.s`, `bye`, `words` are handled
   *before* the lex/parse pipeline sees the line. This keeps them out of
   the Forth grammar (so a user can't accidentally redefine `.s`).
2. **Buffer + tokenize** — the incoming line is appended to a pending
   buffer (empty on first call) and the combined buffer is lexed.
3. **Parse** — the tokens are parsed into a `Program`. If lex/parse
   raises with a message containing "unterminated", the partial input is
   kept in the buffer and the result reports `continuation=True` — the
   interactive driver should display a continuation prompt.
4. **Resolve + stack-check** — uses the executor's accumulated dictionary
   (so previously-defined words and variables remain visible) and the
   *seeded* stack-checker (`initial_depth=len(executor.data_stack)`) so
   the data stack survives across lines.
5. **Snapshot + execute** — snapshot stack/variables/dictionary state
   before running. On runtime error, restore the snapshot so a bad line
   leaves no trace.
6. **Output** — successful lines print `ok`. Errors print
   `<file>:<line>:<col>: <message>`.

Stack-checker seeding (the load-bearing decision)
=================================================

Without seeding, every line that consumed more than it produced would
falsely report stack underflow. Seeding with `len(executor.data_stack)`
makes the existing stack visible to the simulation. Underflow is still
measured against absolute zero (a line that tries to consume more than
the live stack contains is still an error) — see `stackcheck.py`'s
`initial_depth` parameter.

This means: stack-effect inference for *user definitions declared inside
a REPL line* still computes against a fresh `initial_depth=0` body
(matching Definition semantics), but the main-body simulation that
follows starts at the live depth.

Special commands
================

* ``.s``    — non-destructively print the data stack (top on the right).
* ``bye``   — request the interactive loop to exit.
* ``words`` — list the dictionary contents.

These three are intercepted whole-line (after `strip()` + lowercase).
A line like `1 2 .s` is not intercepted — only a bare `.s` is. This
matches gforth and most pragmatic Forth dialects.

State rollback on error
=======================

A snapshot of `(data_stack, variables)` is taken before execute. On
`ExecutionError`, the snapshot is restored. The dictionary is NOT
rolled back if the error fires mid-execution after some definitions
landed — that would require deep-copying the dictionary every line.
For a REPL that's acceptable: the worst-case user-visible effect is a
partially-installed definition that the user can re-`:` to overwrite.
Lex/parse/stackcheck errors fire *before* any execution and have no
state changes to roll back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mforth.backend.host import Executor, ExecutionError
from mforth.backend.primitives import register_all
from mforth.dictionary import UnresolvedWordError, resolve
from mforth.lex import LexError, tokenize
from mforth.parse import ParseError, _Parser
from mforth.stackcheck import StackError, stackcheck


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class ReplResult:
    """One round-trip through :meth:`Repl.run_line`.

    Attributes
    ----------
    ok
        True if the line was accepted and executed (or was a successful
        special-command). False if any error fired.
    output
        Text the interactive driver should print to the user. Includes
        the trailing ``ok`` for successful Forth lines, the stack contents
        for ``.s``, etc. Always a string (possibly empty).
    continuation
        True if the line was syntactically incomplete (open ``:`` def,
        unterminated string, unclosed ``IF``/``BEGIN``/``DO``). The driver
        should display a continuation prompt and feed the next line to
        :meth:`Repl.run_line` again — the Repl will combine them.
    exit_requested
        True if the user typed ``bye``. The interactive driver should
        break out of its loop.
    """

    ok: bool
    output: str
    continuation: bool = False
    exit_requested: bool = False


# ---------------------------------------------------------------------------
# Repl
# ---------------------------------------------------------------------------


SPECIAL_COMMANDS = (".s", "bye", "words")


@dataclass
class Repl:
    """The interactive REPL's programmatic interface.

    Long-lived instance per REPL session. Construct once; call
    :meth:`run_line` per user input line.

    Parameters
    ----------
    preload_source
        Optional source string executed once at construction time
        (typically the contents of a `--load <file.fs>` file). Definitions
        and variables it declares become available at the prompt.
    preload_file
        File label used for error messages from preload_source. Defaults
        to ``"<preload>"``.
    """

    preload_source: Optional[str] = None
    preload_file: str = "<preload>"
    executor: Executor = field(default_factory=Executor)
    _pending: str = ""  # multi-line buffer
    _line_counter: int = 0

    def __post_init__(self) -> None:
        # Wire every host primitive onto the executor so PRINT / DUP / +
        # / etc. all work from the first user line.
        register_all(self.executor)
        if self.preload_source:
            res = self._run_source(self.preload_source, self.preload_file)
            if not res.ok:
                # Preload errors propagate as exceptions so the CLI can
                # report them before dropping to the prompt.
                raise RuntimeError(f"preload failed: {res.output}")

    # -- public ----------------------------------------------------------

    def run_line(self, line: str) -> ReplResult:
        """Process one input line. See module docstring for the pipeline."""
        # 1. Special-command intercept (only on a clean buffer — a special
        #    command interrupts a continuation, which matches user
        #    expectation: a stuck `:` def can be escaped by typing `bye`).
        stripped = line.strip()
        lowered = stripped.lower()
        if not self._pending and lowered in SPECIAL_COMMANDS:
            return self._handle_special(lowered)

        # 2. Empty / whitespace-only line is a no-op (unless we're in a
        #    pending buffer, in which case we're still waiting).
        if not stripped and not self._pending:
            return ReplResult(ok=True, output="")

        # 3. Combine with pending buffer and run.
        self._line_counter += 1
        file_label = f"<repl:{self._line_counter}>"
        if self._pending:
            combined = self._pending + "\n" + line
        else:
            combined = line

        return self._run_source(combined, file_label, is_repl_input=True)

    # -- internal --------------------------------------------------------

    def _handle_special(self, cmd: str) -> ReplResult:
        if cmd == "bye":
            return ReplResult(
                ok=True, output="bye", exit_requested=True
            )
        if cmd == ".s":
            return ReplResult(ok=True, output=self._format_stack())
        if cmd == "words":
            return ReplResult(ok=True, output=self._format_words())
        # Defensive — SPECIAL_COMMANDS is the source of truth.
        return ReplResult(ok=False, output=f"unknown special command: {cmd!r}")

    def _format_stack(self) -> str:
        ds = self.executor.data_stack
        if not ds:
            return "<0>"
        rendered = " ".join(self._render_value(v) for v in ds)
        return f"<{len(ds)}> {rendered}"

    @staticmethod
    def _render_value(v) -> str:
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)

    def _format_words(self) -> str:
        """List every name in the active dictionary, one per line.

        Renders each entry's original-case `.name` (built-ins are
        canonical-cased; user words preserve the case the user typed),
        sorted case-insensitively.
        """
        d = self.executor.dictionary
        # Dictionary stores keys lowercase but each entry carries its
        # display name on `.name`. Pull display names, dedupe (defensive
        # — should be 1:1), and sort case-insensitively.
        names = sorted({e.name for e in d._entries.values()}, key=str.upper)
        return "\n".join(names)

    def _run_source(
        self, src: str, file_label: str, is_repl_input: bool = False
    ) -> ReplResult:
        """Lex / parse / resolve / stackcheck / execute `src`.

        On lex or parse error whose message indicates an unterminated
        construct, buffer the input and return continuation=True (only
        when `is_repl_input` — preload errors are always hard).
        """
        # --- Lex ---
        try:
            tokens = list(tokenize(src, file=file_label))
        except LexError as e:
            if is_repl_input and "unterminated" in e.message.lower():
                self._pending = src
                return ReplResult(ok=False, output="", continuation=True)
            self._pending = ""
            return ReplResult(ok=False, output=str(e))

        # --- Parse ---
        try:
            program = _Parser(tokens, file=file_label).parse_program()
        except ParseError as e:
            if is_repl_input and "unterminated" in e.message.lower():
                self._pending = src
                return ReplResult(ok=False, output="", continuation=True)
            self._pending = ""
            return ReplResult(ok=False, output=str(e))

        # Successful parse — clear the pending buffer.
        self._pending = ""

        # --- Resolve (against executor's accumulated dictionary) ---
        # Snapshot the dictionary's entry-names so we can detect & roll
        # back partial additions if a later stage errors. Definition
        # objects added by `resolve` are tracked here.
        d = self.executor.dictionary
        dict_snapshot = set(d._entries.keys())

        try:
            resolve(program, dictionary=d)
        except UnresolvedWordError as e:
            self._rollback_dictionary(dict_snapshot)
            return ReplResult(ok=False, output=str(e))

        # --- Stackcheck (seeded with the current data-stack depth) ---
        try:
            result = stackcheck(
                program,
                dictionary=d,
                initial_depth=len(self.executor.data_stack),
            )
        except StackError as e:
            self._rollback_dictionary(dict_snapshot)
            return ReplResult(ok=False, output=str(e))

        # --- Execute (snapshot state for runtime-error rollback) ---
        ds_snapshot = list(self.executor.data_stack)
        vars_snapshot = dict(self.executor.variables)
        try:
            self.executor.execute(result)
        except ExecutionError as e:
            self.executor.data_stack[:] = ds_snapshot
            self.executor.variables.clear()
            self.executor.variables.update(vars_snapshot)
            self._rollback_dictionary(dict_snapshot)
            return ReplResult(ok=False, output=str(e))
        except Exception as e:  # noqa: BLE001 — primitive blew up
            self.executor.data_stack[:] = ds_snapshot
            self.executor.variables.clear()
            self.executor.variables.update(vars_snapshot)
            self._rollback_dictionary(dict_snapshot)
            return ReplResult(
                ok=False, output=f"{type(e).__name__}: {e}"
            )

        return ReplResult(ok=True, output="ok")

    def _rollback_dictionary(self, snapshot: set) -> None:
        """Remove any dictionary entries added since the snapshot."""
        d = self.executor.dictionary
        added = set(d._entries.keys()) - snapshot
        for name in added:
            del d._entries[name]


__all__ = ["Repl", "ReplResult", "SPECIAL_COMMANDS"]
