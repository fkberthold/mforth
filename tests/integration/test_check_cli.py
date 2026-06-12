"""CLI-surface tests for ``mforth check`` (bead mforth-roz.1).

Drives ``mforth.cli.main(["check", ...])`` end-to-end and pins:

* ``mforth check <correct.fs>``  → exit 0, ``✓`` line with N/N.
* ``mforth check <wrong.fs>``    → exit 1, ``✗`` line + the hint.
* ``mforth check --list``        → lists bundled ids + prompts, exit 0.
* ``mforth check --scaffold <id>`` → writes a starter stub to cwd, exit 0.
* ``mforth check --solution <id>`` → prints the reference solution, exit 0.

Plus the SELF-VALIDATION meta-test: every bundled reference solution
passes its own checker. This is the gate the tutorial chapters extend —
a spec that ships a solution it can't validate is a broken spec.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import mforth.cli
import mforth.cli_check  # noqa: F401 — ensures the module is importable
from mforth import exercises


def _reset_cli_registry() -> None:
    mforth.cli._REGISTRY.clear()
    importlib.reload(mforth.cli_check)
    mforth.cli._load_subcommands()


@pytest.fixture(autouse=True)
def _cli_registry():
    _reset_cli_registry()
    yield
    mforth.cli._REGISTRY.clear()


# ---------------------------------------------------------------------------
# check <solution.fs>
# ---------------------------------------------------------------------------


def test_check_correct_solution_exit_0(tmp_path, capsys):
    sol = tmp_path / "double.fs"
    sol.write_text(exercises.load_solution_text("forth-101/01-double"))
    rc = mforth.cli.main(["check", str(sol)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "✓" in out
    assert "forth-101/01-double" in out
    assert "3/3" in out


def test_check_wrong_solution_exit_1_with_hint(tmp_path, capsys):
    sol = tmp_path / "wrong.fs"
    sol.write_text(
        "\\ @exercise forth-101/01-double\n: double ( n -- n ) 3 * ;\n"
    )
    rc = mforth.cli.main(["check", str(sol)])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 1
    assert "✗" in combined
    # The mismatch is surfaced: printed "15", expected "10".
    assert "15" in combined and "10" in combined
    # The hint is shown.
    spec = exercises.load_spec("forth-101/01-double")
    assert spec.hint in combined


def test_check_missing_file_exit_1(tmp_path, capsys):
    rc = mforth.cli.main(["check", str(tmp_path / "nope.fs")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "nope.fs" in err


# ---------------------------------------------------------------------------
# --list
# ---------------------------------------------------------------------------


def test_check_list_enumerates_ids_and_prompts(capsys):
    rc = mforth.cli.main(["check", "--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "forth-101/01-double" in out
    assert "forth-101/02-nip" in out
    # prompt text appears alongside the id
    assert "double" in out.lower()


# ---------------------------------------------------------------------------
# --scaffold
# ---------------------------------------------------------------------------


def test_check_scaffold_writes_stub_to_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = mforth.cli.main(["check", "--scaffold", "forth-101/02-nip"])
    out = capsys.readouterr().out
    assert rc == 0
    # Stub written to cwd; name derived from the exercise basename.
    stub = tmp_path / "02-nip.fs"
    assert stub.exists()
    text = stub.read_text()
    assert "@exercise forth-101/02-nip" in text
    # Prompt is embedded as a comment + there's a TODO for the learner.
    assert "nip" in text.lower()
    assert "TODO" in text
    # The CLI tells the learner where the stub landed.
    assert "02-nip.fs" in out


def test_check_scaffold_does_not_clobber(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "02-nip.fs"
    existing.write_text("my work\n")
    rc = mforth.cli.main(["check", "--scaffold", "forth-101/02-nip"])
    capsys.readouterr()
    # Refuses to overwrite existing learner work.
    assert rc == 1
    assert existing.read_text() == "my work\n"


# ---------------------------------------------------------------------------
# --solution
# ---------------------------------------------------------------------------


def test_check_solution_prints_reference(capsys):
    rc = mforth.cli.main(["check", "--solution", "forth-101/01-double"])
    out = capsys.readouterr().out
    assert rc == 0
    assert ": double" in out
    assert "@exercise forth-101/01-double" in out


def test_check_unknown_id_exit_1(capsys):
    rc = mforth.cli.main(["check", "--solution", "forth-101/zzz"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "zzz" in err or "unknown" in err.lower()


# ---------------------------------------------------------------------------
# SELF-VALIDATION meta-test (the gate chapters extend)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ex_id", sorted(exercises.list_ids()))
def test_every_reference_solution_passes_its_own_checker(ex_id, tmp_path, capsys):
    """Every bundled spec ships a reference solution that passes the spec.
    A new chapter adding a spec whose solution doesn't validate fails here."""
    sol = tmp_path / "ref.fs"
    sol.write_text(exercises.load_solution_text(ex_id))
    rc = mforth.cli.main(["check", str(sol)])
    out = capsys.readouterr().out
    assert rc == 0, f"reference solution for {ex_id} failed its own checker:\n{out}"
    assert "✓" in out
