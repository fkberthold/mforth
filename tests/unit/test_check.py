"""Unit tests for the exercise spec loader + ``mforth check`` engine
(bead mforth-roz.1).

Covers the two non-CLI layers:

* :mod:`mforth.exercises` — the package-data spec loader. It must
  resolve the bundled ``*.spec.toml`` + ``*.solution.fs`` from any cwd
  (importlib.resources), enumerate ids, and parse the schema
  (``id`` / ``prompt`` / ``hint`` / optional ``sidecar`` / ``[[case]]``).
* :mod:`mforth.cli_check` engine helpers — extract the ``\\ @exercise
  <id>`` marker from a learner ``.fs``; run each case (learner code +
  driver) through the host Runner against a MockWorld; compare the
  printed strings to ``expect``.

The CLI-surface (exit codes, ``--list`` / ``--scaffold`` / ``--solution``
output) lives in ``tests/integration/test_check_cli.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mforth import exercises
from mforth.cli_check import (
    CheckResult,
    ExerciseMarkerError,
    extract_exercise_id,
    run_check,
)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_list_ids_includes_bundled_exemplars():
    ids = exercises.list_ids()
    assert "forth-101/01-double" in ids
    assert "forth-101/02-nip" in ids


def test_load_spec_parses_schema():
    spec = exercises.load_spec("forth-101/02-nip")
    assert spec.id == "forth-101/02-nip"
    assert "nip" in spec.prompt.lower()
    assert spec.hint  # non-empty
    assert spec.sidecar is None  # this exemplar is abstract (no simulator)
    assert len(spec.cases) == 2
    assert spec.cases[0].driver == "1 2 nip ."
    assert spec.cases[0].expect == ["2"]
    assert spec.cases[1].expect == ["99"]


def test_load_spec_unknown_id_raises():
    with pytest.raises(exercises.UnknownExerciseError):
        exercises.load_spec("forth-101/does-not-exist")


def test_loader_resolves_from_any_cwd(tmp_path, monkeypatch):
    """The loader uses importlib.resources, so chdir'ing somewhere with no
    project files on disk must NOT break spec resolution."""
    monkeypatch.chdir(tmp_path)
    spec = exercises.load_spec("forth-101/01-double")
    assert spec.id == "forth-101/01-double"
    assert spec.cases  # cases survived the cwd change


def test_load_solution_text_has_marker():
    text = exercises.load_solution_text("forth-101/01-double")
    assert "@exercise forth-101/01-double" in text
    assert ": double" in text


def test_each_listed_id_has_loadable_spec():
    for ex_id in exercises.list_ids():
        spec = exercises.load_spec(ex_id)
        assert spec.id == ex_id
        assert spec.cases, f"{ex_id} has no cases"


# ---------------------------------------------------------------------------
# Marker extraction
# ---------------------------------------------------------------------------


def test_extract_exercise_id_from_marker():
    src = "\\ @exercise forth-101/02-nip\n: nip SWAP DROP ;\n"
    assert extract_exercise_id(src) == "forth-101/02-nip"


def test_extract_exercise_id_tolerates_leading_lines_and_spacing():
    src = "\n\n\\   @exercise   forth-101/01-double  \n: double DUP + ;\n"
    assert extract_exercise_id(src) == "forth-101/01-double"


def test_extract_exercise_id_missing_marker_raises():
    with pytest.raises(ExerciseMarkerError):
        extract_exercise_id(": double DUP + ;\n")


# ---------------------------------------------------------------------------
# Checker engine
# ---------------------------------------------------------------------------


def _solution_path(ex_id: str) -> Path:
    """Materialize the bundled reference solution to a path the checker can
    read. We read the text via the loader (cwd-independent) and write it to a
    real file so run_check exercises the same file-reading path the CLI uses."""
    text = exercises.load_solution_text(ex_id)
    return text


def test_correct_solution_passes(tmp_path):
    sol = tmp_path / "double.fs"
    sol.write_text(exercises.load_solution_text("forth-101/01-double"))
    result = run_check(sol)
    assert isinstance(result, CheckResult)
    assert result.exercise_id == "forth-101/01-double"
    assert result.passed is True
    assert result.total == 3
    assert result.num_passed == 3
    assert result.failures == []


def test_incorrect_solution_fails_with_diagnostic(tmp_path):
    # Wrong: triples instead of doubles → first case prints "15" not "10".
    # Stack-valid (1 -- 1) so it passes stackcheck and reaches the value
    # comparison; only the VALUE is wrong.
    sol = tmp_path / "wrong.fs"
    sol.write_text(
        "\\ @exercise forth-101/01-double\n: double ( n -- n ) DUP DUP + + ;\n"
    )
    result = run_check(sol)
    assert result.passed is False
    assert result.num_passed < result.total
    assert result.failures, "expected at least one failure record"
    first = result.failures[0]
    # The failure record names the driver + what was printed vs expected.
    assert first.driver == "5 double ."
    assert first.printed == ["15"]
    assert first.expect == ["10"]


def test_check_unknown_marker_id_raises(tmp_path):
    sol = tmp_path / "bad.fs"
    sol.write_text("\\ @exercise forth-101/nope\n: x ;\n")
    with pytest.raises(exercises.UnknownExerciseError):
        run_check(sol)


def test_check_missing_marker_raises(tmp_path):
    sol = tmp_path / "nomark.fs"
    sol.write_text(": double DUP + ;\n")
    with pytest.raises(ExerciseMarkerError):
        run_check(sol)
