"""Interactive stdin/stdout driver around :class:`mforth.backend.repl.Repl`.

The driver is a thin wrapper: pull a line, hand it to ``Repl.run_line``,
print the output, repeat. ``readline`` (stdlib) is imported for line
editing + history if available; on platforms without it (e.g. some
non-CPython Windows builds) the driver degrades to a vanilla ``input()``
loop with no history.

Tests do NOT drive this module — they exercise ``Repl.run_line`` directly.
That keeps the driver itself extremely small (a sequence of obvious
print/input calls) and pushes all the interesting contract testing into
:mod:`tests.integration.test_repl_prompt`.
"""

from __future__ import annotations

import sys
from typing import Optional

from mforth.backend.repl import Repl


# Best-effort readline import. On platforms without it we get a plain
# blocking input() with no history or line editing, which is fine for
# unattended use and tolerable for interactive use.
try:
    import readline  # noqa: F401 — side-effect: hooks into builtin input()
except ImportError:  # pragma: no cover — non-CPython / Windows
    pass


PROMPT = "mforth> "
CONTINUATION_PROMPT = "    ... "


def run_interactive(
    preload_source: Optional[str] = None,
    preload_file: str = "<preload>",
) -> int:
    """Run the interactive prompt until EOF or ``bye``. Returns exit code."""
    try:
        repl = Repl(preload_source=preload_source, preload_file=preload_file)
    except RuntimeError as e:
        print(f"mforth repl: {e}", file=sys.stderr)
        return 1

    print("mforth REPL — type 'bye' to exit, '.s' to show stack, 'words' to list dictionary.")
    in_continuation = False
    while True:
        try:
            line = input(CONTINUATION_PROMPT if in_continuation else PROMPT)
        except EOFError:
            print()  # newline after the prompt
            return 0
        except KeyboardInterrupt:
            # Ctrl-C aborts the current line; reset continuation buffer.
            print("^C")
            repl._pending = ""  # noqa: SLF001 — driver/REPL are tightly coupled
            in_continuation = False
            continue

        result = repl.run_line(line)
        if result.exit_requested:
            print(result.output)
            return 0
        if result.continuation:
            in_continuation = True
            continue
        in_continuation = False
        if result.output:
            print(result.output)


__all__ = ["run_interactive", "PROMPT", "CONTINUATION_PROMPT"]
