"""Integration tests for the interactive REPL prompt (bead mforth-10t.13).

The interactive `mforth repl` subcommand is driven by stdin/stdout in
production, but tests exercise the *programmatic* surface — `Repl.run_line()` —
because pytest can't reasonably drive an interactive readline loop.

The contract under test (per bead description):
  1. Each line is lexed/parsed/stack-checked against the current dictionary,
     then executed.
  2. State (data stack, variables, user definitions) persists across lines.
  3. On error (lex / parse / stackcheck / runtime), state is unchanged and
     the prompt returns.
  4. Multi-line constructs (unterminated `:` definition, unterminated string,
     unclosed IF/BEGIN/DO) buffer across lines until syntactically complete.
  5. Special commands `.s` (show stack), `bye` (exit), `words` (list
     dictionary) are intercepted before the lex/parse pipeline.

Key load-bearing decision: the stack-checker is seeded with the *current*
data-stack depth (NOT zero) so a multi-line program like `5 6` then `+ .`
type-checks correctly. This is the central correctness property of the REPL:
without it, every line that consumes more than it produces would falsely
report stack underflow.
"""

from __future__ import annotations

import pytest

from mforth.backend.repl import Repl, ReplResult


# ---------------------------------------------------------------------------
# 1. Stack survives across lines  (THE LOAD-BEARING CONTRACT)
# ---------------------------------------------------------------------------


def test_data_stack_survives_across_lines():
    """`5 6` on one line, `+ .` on the next. The `+` must consume both
    values that the first line pushed; stackcheck must NOT report underflow.
    """
    repl = Repl()
    r1 = repl.run_line("5 6")
    assert r1.ok, f"unexpected error: {r1.output}"
    assert repl.executor.data_stack == [5, 6]

    r2 = repl.run_line("+ .")
    assert r2.ok, f"unexpected error: {r2.output}"
    # `.` consumed the value; world should have observed "11" via print.
    prints = [e for e in repl.executor.world.events if e.__class__.__name__ == "MessagePrintEvent"]
    assert any(getattr(e, "text", None) == "11" for e in prints)


def test_user_definitions_survive_across_lines():
    """Define a word on one line, call it on the next."""
    repl = Repl()
    r1 = repl.run_line(": SQUARE DUP * ;")
    assert r1.ok, f"unexpected error: {r1.output}"
    r2 = repl.run_line("7 SQUARE .")
    assert r2.ok, f"unexpected error: {r2.output}"
    prints = [e for e in repl.executor.world.events if e.__class__.__name__ == "MessagePrintEvent"]
    assert any(getattr(e, "text", None) == "49" for e in prints)


def test_variables_survive_across_lines():
    """Declare a VARIABLE on one line, store / fetch across subsequent lines."""
    repl = Repl()
    assert repl.run_line("VARIABLE COUNT").ok
    assert repl.run_line("0 COUNT !").ok
    assert repl.run_line("COUNT @ 1 + COUNT !").ok
    r = repl.run_line("COUNT @ .")
    assert r.ok, f"unexpected error: {r.output}"
    prints = [e for e in repl.executor.world.events if e.__class__.__name__ == "MessagePrintEvent"]
    assert any(getattr(e, "text", None) == "1" for e in prints)


# ---------------------------------------------------------------------------
# 2. Error isolation — bad line does NOT corrupt state
# ---------------------------------------------------------------------------


def test_lex_error_leaves_state_unchanged():
    repl = Repl()
    repl.run_line("1 2 3")
    before = list(repl.executor.data_stack)
    # `( unterminated comment` triggers a lex error (unbalanced paren).
    # Note: unterminated-comment is a `continuation` case in the REPL
    # (the user might be mid-comment); resolve this by sending a clearly-
    # malformed token. We use an unrecoverable parse error instead:
    r = repl.run_line("; ; ;")  # bare semicolons with no open `:`
    assert not r.ok
    assert r.output  # has SOME error message
    assert repl.executor.data_stack == before, "parse error must not mutate stack"


def test_stackcheck_error_leaves_state_unchanged():
    repl = Repl()
    repl.run_line("1 2")
    before = list(repl.executor.data_stack)
    # `+ + +` would consume 4 from a 2-deep stack — underflow.
    r = repl.run_line("+ + +")
    assert not r.ok
    assert repl.executor.data_stack == before, "stackcheck error must not mutate stack"


def test_unresolved_word_leaves_state_unchanged():
    repl = Repl()
    repl.run_line("1 2 3")
    before = list(repl.executor.data_stack)
    r = repl.run_line("FROBNICATE")
    assert not r.ok
    assert repl.executor.data_stack == before


def test_runtime_error_does_not_block_subsequent_lines():
    """Once an error occurs, the next line still runs cleanly."""
    repl = Repl()
    r1 = repl.run_line("UNDEFINED_WORD")
    assert not r1.ok
    r2 = repl.run_line("42 .")
    assert r2.ok, f"unexpected error after prior bad line: {r2.output}"


# ---------------------------------------------------------------------------
# 3. Multi-line buffering — incomplete syntactic constructs
# ---------------------------------------------------------------------------


def test_multiline_definition_buffers_across_lines():
    """A `:` opened without `;` returns continuation; `;` on next line completes."""
    repl = Repl()
    r1 = repl.run_line(": DOUBLE")
    assert r1.continuation, f"expected continuation, got {r1!r}"
    r2 = repl.run_line("2 *")
    assert r2.continuation
    r3 = repl.run_line(";")
    assert r3.ok, f"unexpected: {r3.output}"
    # Now invoke the definition.
    r4 = repl.run_line("5 DOUBLE .")
    assert r4.ok
    prints = [e for e in repl.executor.world.events if e.__class__.__name__ == "MessagePrintEvent"]
    assert any(getattr(e, "text", None) == "10" for e in prints)


def test_multiline_if_then_buffers_across_lines():
    repl = Repl()
    assert repl.run_line(": SGN DUP 0 > IF").continuation
    assert repl.run_line("DROP 1 ELSE").continuation
    assert repl.run_line("DROP -1 THEN ;").ok
    r = repl.run_line("7 SGN .")
    assert r.ok, f"unexpected: {r.output}"
    prints = [e for e in repl.executor.world.events if e.__class__.__name__ == "MessagePrintEvent"]
    assert any(getattr(e, "text", None) == "1" for e in prints)


# ---------------------------------------------------------------------------
# 4. Special commands
# ---------------------------------------------------------------------------


def test_dot_s_shows_stack_non_destructively():
    repl = Repl()
    repl.run_line("1 2 3")
    r = repl.run_line(".s")
    assert r.ok
    # Output contains the stack values.
    assert "3" in r.output
    assert "1" in r.output
    assert "2" in r.output
    # Stack itself unchanged.
    assert repl.executor.data_stack == [1, 2, 3]


def test_dot_s_on_empty_stack():
    repl = Repl()
    r = repl.run_line(".s")
    assert r.ok
    # Should print a length-0 indicator (e.g. "<0>" or "empty").
    assert "0" in r.output or "empty" in r.output.lower()


def test_bye_signals_exit():
    repl = Repl()
    r = repl.run_line("bye")
    assert r.exit_requested, "bye must request the interactive loop to exit"


def test_words_lists_dictionary():
    repl = Repl()
    r = repl.run_line("words")
    assert r.ok
    # Standard built-ins must appear.
    upper = r.output.upper()
    for builtin in ("DUP", "SWAP", "+", "PRINT"):
        assert builtin in upper, f"expected {builtin} in words output, got {r.output!r}"


def test_words_includes_user_definitions():
    repl = Repl()
    repl.run_line(": MYTHING 1 + ;")
    r = repl.run_line("words")
    assert r.ok
    assert "MYTHING" in r.output.upper()


# ---------------------------------------------------------------------------
# 5. Success-path output convention
# ---------------------------------------------------------------------------


def test_successful_line_returns_ok_marker():
    repl = Repl()
    r = repl.run_line("1 2 +")
    assert r.ok
    # Standard Forth REPL convention: print "ok" after a successful line.
    assert "ok" in r.output.lower()


def test_empty_line_is_a_noop():
    repl = Repl()
    r = repl.run_line("")
    assert r.ok
    assert repl.executor.data_stack == []


def test_whitespace_only_line_is_a_noop():
    repl = Repl()
    r = repl.run_line("   \t  ")
    assert r.ok
    assert repl.executor.data_stack == []


# ---------------------------------------------------------------------------
# 6. Mindustry primitive integration — REPL works end-to-end against MockWorld
# ---------------------------------------------------------------------------


def test_repl_drives_mindustry_print_primitive():
    """End-to-end: REPL executes PRINT and the MockWorld captures it."""
    repl = Repl()
    r = repl.run_line('." hello" PRINT')
    assert r.ok, f"unexpected: {r.output}"
    prints = [e for e in repl.executor.world.events if e.__class__.__name__ == "MessagePrintEvent"]
    assert any(getattr(e, "text", None) == "hello" for e in prints)


# ---------------------------------------------------------------------------
# 7. CLI subcommand registration (it's wired up)
# ---------------------------------------------------------------------------


def _reset_cli_registry():
    """Clear `_REGISTRY` and force re-import of the repl subcommand module
    so its module-level `register_subcommand` call re-executes. Mirrors
    the helper in tests/unit/test_lsp_diagnostics.py."""
    import sys

    import mforth.cli as cli_mod

    cli_mod._REGISTRY.clear()
    for mod in list(sys.modules):
        if mod.startswith("mforth.repl"):
            del sys.modules[mod]
    return cli_mod


def test_repl_subcommand_is_registered():
    """`mforth repl --help` works — the subcommand is on the registry."""
    cli_mod = _reset_cli_registry()
    cli_mod._load_subcommands()
    assert "repl" in cli_mod._REGISTRY, (
        f"expected 'repl' in registry, got {list(cli_mod._REGISTRY)}"
    )


def test_repl_subcommand_has_help_text():
    cli_mod = _reset_cli_registry()
    cli_mod._load_subcommands()
    entry = cli_mod._REGISTRY["repl"]
    assert entry.help, "repl subcommand must have help text"
    # Should describe the interactive nature.
    h = entry.help.lower()
    assert "interactive" in h or "prompt" in h or "repl" in h


def test_repl_subcommand_accepts_load_option():
    """`mforth repl --load <file.fs>` preloads source before the prompt."""
    import argparse

    cli_mod = _reset_cli_registry()
    cli_mod._load_subcommands()
    entry = cli_mod._REGISTRY["repl"]
    parser = argparse.ArgumentParser()
    entry.configure_parser(parser)
    # Should not raise:
    args = parser.parse_args(["--load", "foo.fs"])
    assert args.load == "foo.fs"
    # And `--load` is optional:
    args2 = parser.parse_args([])
    assert args2.load is None


# ---------------------------------------------------------------------------
# 8. ReplResult shape
# ---------------------------------------------------------------------------


def test_repl_result_fields():
    """ReplResult exposes ok, output, continuation, exit_requested."""
    r = ReplResult(ok=True, output="ok", continuation=False, exit_requested=False)
    assert r.ok is True
    assert r.output == "ok"
    assert r.continuation is False
    assert r.exit_requested is False


# ---------------------------------------------------------------------------
# 9. --load preloads source
# ---------------------------------------------------------------------------


def test_repl_load_preloads_definitions(tmp_path):
    """Repl(preload_source=...) preloads the file's contents; the prompt
    sees the definitions as if they'd been typed."""
    src = tmp_path / "lib.fs"
    src.write_text(": TRIPLE 3 * ;\n")
    repl = Repl(preload_source=src.read_text(), preload_file=str(src))
    r = repl.run_line("4 TRIPLE .")
    assert r.ok, f"unexpected: {r.output}"
    prints = [e for e in repl.executor.world.events if e.__class__.__name__ == "MessagePrintEvent"]
    assert any(getattr(e, "text", None) == "12" for e in prints)


# ---------------------------------------------------------------------------
# 10. Print output is rendered to the user (mforth-os2)
#
# Regression for mforth-os2: `.` and PRINT emitted a MessagePrintEvent but
# the REPL returned only "ok", so the printed value never reached the user.
# The event stream is unchanged by the fix (equivalence intact) — the REPL
# now ALSO surfaces the printed text in ReplResult.output. Decision (Frank,
# 2026-06-13): echo immediately, Forth-like, on the same line as `ok`.
# ---------------------------------------------------------------------------


def test_repl_dot_renders_printed_value_in_output():
    """`3 4 + .` must surface the popped value '7' to the user, not just
    emit a MessagePrintEvent. This is the headline mforth-os2 symptom."""
    repl = Repl()
    r = repl.run_line("3 4 + .")
    assert r.ok, f"unexpected error: {r.output}"
    assert "7" in r.output, f"expected printed '7' in output, got {r.output!r}"


def test_repl_print_word_renders_in_output():
    """PRINT output is also surfaced to the user in the REPL (same sink as
    `.` — both funnel through world.print → MessagePrintEvent)."""
    repl = Repl()
    r = repl.run_line('." count=" PRINT')
    assert r.ok, f"unexpected error: {r.output}"
    assert "count=" in r.output, f"expected 'count=' in output, got {r.output!r}"


def test_repl_multiple_prints_concatenate_in_output():
    """Multiple prints on one line concatenate with no separator, matching
    the mlog print-buffer accumulation semantics (bug-class coverage)."""
    repl = Repl()
    r = repl.run_line('." count=" PRINT 5 .')
    assert r.ok, f"unexpected error: {r.output}"
    assert "count=5" in r.output, f"expected 'count=5' in output, got {r.output!r}"


def test_repl_printed_output_coexists_with_ok_marker():
    """Printed output is surfaced ALONGSIDE the 'ok' marker, not instead of
    it — so the user still sees the success signal."""
    repl = Repl()
    r = repl.run_line("42 .")
    assert "42" in r.output, f"got {r.output!r}"
    assert "ok" in r.output, f"got {r.output!r}"


def test_repl_non_printing_line_is_still_exactly_ok():
    """A line that prints nothing must still return exactly 'ok' — no stray
    whitespace, no regression for the common no-output case."""
    repl = Repl()
    r = repl.run_line("1 2 +")
    assert r.output == "ok", f"expected bare 'ok', got {r.output!r}"
