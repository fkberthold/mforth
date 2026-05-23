"""Integration tests for ``mforth compile`` (bead mforth-10t.19).

Drives the CLI subcommand end-to-end through ``mforth.cli.main([...])``
on the golden-fixture .fs files. Pins the user-facing surface:

* Successful compile writes a non-empty .mlog file with the header
  comment, the substituted in-game names (Mode A), and the prologue
  ``getlink`` (Mode B).
* Missing source → exit 1.
* Missing ``--output`` flag → argparse usage error.
* Malformed sidecar → exit 1 with the sidecar error message.
* Output round-trips through bead .29's serializer body shape — i.e.
  every non-header line is ``opcode op1 op2 ...`` space-joined with no
  trailing whitespace.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import mforth.cli
import mforth.cli_compile  # noqa: F401 — ensures the module is importable


GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"


def _reset_cli_registry() -> None:
    """Clear and rebuild the CLI registry so test isolation holds when
    earlier tests in the run have mutated ``mforth.cli._REGISTRY``.

    Mirrors the pattern from ``tests/unit/test_cli.py`` per bead .14's
    drawer note on test-isolation."""
    mforth.cli._REGISTRY.clear()
    # Reimport the compile module so its module-level state (none in our
    # case, but defensive) is fresh.
    importlib.reload(mforth.cli_compile)
    mforth.cli._load_subcommands()


@pytest.fixture(autouse=True)
def _cli_registry():
    _reset_cli_registry()
    yield
    mforth.cli._REGISTRY.clear()


def test_compile_counter_fixture_produces_substituted_output(tmp_path):
    output = tmp_path / "counter.mlog"
    rc = mforth.cli.main([
        "compile",
        str(GOLDEN_DIR / "counter.fs"),
        "-o",
        str(output),
    ])
    assert rc == 0
    text = output.read_text()
    assert text.startswith("#")
    # The Mode A in-game name must appear; the mforth-name must NOT in
    # any printflush operand (substituted away).
    assert "message1" in text
    for line in text.splitlines():
        if line.startswith("printflush"):
            assert "display" not in line, line


def test_compile_getlink_index_mode_emits_prologue(tmp_path):
    output = tmp_path / "out.mlog"
    rc = mforth.cli.main([
        "compile",
        str(GOLDEN_DIR / "getlink_index_mode.fs"),
        "-o",
        str(output),
    ])
    assert rc == 0
    body = [
        ln for ln in output.read_text().splitlines()
        if not ln.startswith("#") and ln.strip()
    ]
    assert body[0] == "getlink display 0", body


def test_compile_if_then_fixture_resolves_labels(tmp_path):
    output = tmp_path / "out.mlog"
    rc = mforth.cli.main([
        "compile",
        str(GOLDEN_DIR / "if_then.fs"),
        "-o",
        str(output),
    ])
    assert rc == 0
    text = output.read_text()
    for ln in text.splitlines():
        if ln.startswith("jump"):
            int(ln.split()[1])  # resolved to a numeric target


def test_compile_missing_source_exits_nonzero(tmp_path, capsys):
    output = tmp_path / "out.mlog"
    rc = mforth.cli.main([
        "compile",
        str(tmp_path / "does_not_exist.fs"),
        "-o",
        str(output),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "does_not_exist.fs" in err


def test_compile_missing_output_flag_is_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        mforth.cli.main([
            "compile",
            str(GOLDEN_DIR / "if_then.fs"),
        ])
    assert exc_info.value.code == 2  # argparse usage error


def test_compile_malformed_sidecar_exits_nonzero(tmp_path, capsys):
    src = tmp_path / "prog.fs"
    src.write_text("1 2 +\n")
    bad = tmp_path / "prog.world.toml"
    # Both target+index violate the sidecar's exactly-one rule.
    bad.write_text(
        "[links.foo]\ntype = \"message\"\ntarget = \"a\"\nindex = 0\n"
    )
    output = tmp_path / "out.mlog"
    rc = mforth.cli.main([
        "compile",
        str(src),
        "-o",
        str(output),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "target" in err or "index" in err


def test_compile_output_body_is_serializer_shape(tmp_path):
    """The non-header body must obey bead .29's serializer contract:
    space-joined ``opcode op1 op2 ...`` per line, no trailing whitespace,
    exactly one trailing newline at EOF."""
    output = tmp_path / "out.mlog"
    rc = mforth.cli.main([
        "compile",
        str(GOLDEN_DIR / "arithmetic_basic.fs"),
        "-o",
        str(output),
    ])
    assert rc == 0
    text = output.read_text()
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    for ln in text.splitlines():
        if ln.startswith("#"):
            continue
        if not ln:
            continue
        assert ln == ln.rstrip()  # no trailing whitespace
        # Body lines are opcode + operands joined by single spaces — no
        # double spaces, no tabs.
        assert "\t" not in ln
        assert "  " not in ln


def test_compile_emit_comments_flag_does_not_break_output(tmp_path):
    """``--emit-comments`` is wired through; currently a no-op for the
    body (no per-Term locations carried into tuples yet) but must not
    break compilation."""
    output = tmp_path / "out.mlog"
    rc = mforth.cli.main([
        "compile",
        str(GOLDEN_DIR / "arithmetic_basic.fs"),
        "-o",
        str(output),
        "--emit-comments",
    ])
    assert rc == 0
    text = output.read_text()
    assert text.startswith("#")  # header still present


# --------------------------------------------------------------------------
# Example fixtures (bead mforth-10t.32) — pinned for the v1 demo.
# --------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


def test_compile_blink_example_under_instruction_budget(tmp_path):
    """examples/blink.fs compiles cleanly and lands well under the
    community-lore 1000-instruction-per-processor cap (per CLAUDE.md
    `mforth-v1-demo` bd-memory).

    Pins: the v1 demo target is paste-ready into a real Mindustry
    logic processor without hitting the size ceiling.
    """
    output = tmp_path / "blink.mlog"
    rc = mforth.cli.main([
        "compile",
        str(EXAMPLES_DIR / "blink.fs"),
        "-o",
        str(output),
    ])
    assert rc == 0
    text = output.read_text()
    # Header carries the self-monitor instruction count.
    assert text.startswith("#")
    assert "instructions" in text.splitlines()[0]
    # Body line count is far under the 1000-instr lore cap.
    body = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert len(body) < 100, f"blink should be tiny, got {len(body)} instrs"
    # Sidecar substitution did its job — `display` should be replaced
    # with the in-game name from the sidecar (not appear in any printflush).
    for ln in body:
        if ln.startswith("printflush"):
            assert "display" not in ln, ln


def test_compile_counter_example_under_instruction_budget(tmp_path):
    """examples/counter.fs compiles cleanly and is even smaller than
    blink (no Mindustry primitives, just VARIABLE + arithmetic + `.`).
    Pins the second v1 demo's reachability via `mforth compile`."""
    output = tmp_path / "counter.mlog"
    rc = mforth.cli.main([
        "compile",
        str(EXAMPLES_DIR / "counter.fs"),
        "-o",
        str(output),
    ])
    assert rc == 0
    text = output.read_text()
    assert text.startswith("#")
    assert "instructions" in text.splitlines()[0]
    body = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert len(body) < 100, f"counter should be tiny, got {len(body)} instrs"
