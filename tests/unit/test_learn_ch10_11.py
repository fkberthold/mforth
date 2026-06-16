"""Self-validation gate for the "Learn Forth with mforth" Part I
meta-layer chapters 10-11 (bead mforth-7h1.5): defining words
(``CREATE`` / ``,`` / ``DOES>`` / source-defined ``CONSTANT``) and user
macros (``MACRO: name … ;``).

Two correctness gates, mirroring the per-chapter pattern of the earlier
batches (``test_learn_ch07_09.py``, ``test_learn_ch12_14.py``):

1. **Every bundled ``forth-104`` + ``forth-105`` reference solution
   passes its own checker.** Discovers exercises via
   :func:`mforth.exercises.list_ids` and runs the real
   :func:`mforth.cli_check.run_check` over each bundled ``*.solution.fs``,
   so the gate AUTO-EXTENDS to any future meta-layer exercise. A red
   solution (declared-vs-inferred mismatch, wrong expected output) fails
   here rather than shipping broken to a learner. The shared meta-test in
   ``tests/integration/test_check_cli.py`` covers all tracks too; this
   module pins the two meta-layer tracks next to the chapters they back.

2. **Every ```forth fence in ``10-defining-words.md`` + ``11-macros.md``
   compiles + runs.** A small extractor pulls each fenced ``forth`` block
   and runs it once through the real host
   :class:`~mforth.backend.runner.Runner` (lex → parse → resolve →
   expand → stackcheck → execute). A snippet that no longer compiles — a
   meta-word that stopped folding, a renamed primitive — fails here, so
   the prose can't claim something the language doesn't do.

The two *rejection* examples in the prose (``CellBoundaryError`` for a
runtime-input ``DOES>`` body; ``PurityError`` for a world-sink in a
macro) are deliberately written in ``text`` fences, NOT ``forth`` fences,
because they are meant to fail to compile — so the ``forth``-only
extractor correctly skips them. These chapters are abstract Part-I
material: their fences reference no sidecar-bound blocks, so no
synthesized ``.world.toml`` is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mforth import exercises
from mforth.backend.runner import Runner
from mforth.cli_check import run_check

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHAPTER_DIR = _REPO_ROOT / "docs" / "tutorials" / "learn-forth"
_CHAPTERS = [
    _CHAPTER_DIR / "10-defining-words.md",
    _CHAPTER_DIR / "11-macros.md",
]

_TRACKS = ("forth-104", "forth-105")

# The six exercises this batch ships (ch10 defining words, ch11 macros).
_CH10_11_IDS = [
    "forth-104/01-constant",
    "forth-104/02-doubled",
    "forth-104/03-family",
    "forth-105/01-macro",
    "forth-105/02-compose",
    "forth-105/03-width",
]


def _track_ids() -> list[str]:
    """Every bundled exercise id under the two meta-layer tracks."""
    return [
        i
        for i in exercises.list_ids()
        if i.split("/", 1)[0] in _TRACKS
    ]


# ---------------------------------------------------------------------------
# Gate 1 — auto-extending: every forth-104/forth-105 solution passes
# ---------------------------------------------------------------------------


def test_meta_tracks_are_populated():
    """Sanity: both tracks exist and ship the six exercises this batch
    introduced (a guard so a packaging regression that drops the specs
    turns red here instead of silently emptying gate 1)."""
    found = set(_track_ids())
    assert found, "no forth-104/forth-105 exercises bundled"
    for ex_id in _CH10_11_IDS:
        assert ex_id in found, f"missing bundled exercise: {ex_id}"


@pytest.mark.parametrize("ex_id", _track_ids())
def test_reference_solution_passes_its_own_checker(ex_id: str, tmp_path: Path):
    """Each bundled meta-layer reference solution prints ``✓`` under the
    checker — ``run_check`` reports all cases pass. Auto-extends to any
    future forth-104/forth-105 exercise."""
    assert exercises.has_solution(ex_id), f"{ex_id} has no reference solution"
    sol = tmp_path / "solution.fs"
    sol.write_text(exercises.load_solution_text(ex_id), encoding="utf-8")

    result = run_check(sol)

    assert result.exercise_id == ex_id
    assert result.passed, (
        f"reference solution for {ex_id} is RED: "
        f"{result.num_passed}/{result.total} cases pass; "
        f"failures={result.failures}"
    )
    assert result.total >= 1


@pytest.mark.parametrize("ex_id", _track_ids())
def test_solution_carries_marker_and_spec_metadata(ex_id: str):
    """Each solution carries its ``\\ @exercise <id>`` marker, and each
    spec has a non-empty prompt + hint. Meta-layer exercises are abstract
    (no simulator sidecar)."""
    text = exercises.load_solution_text(ex_id)
    assert f"@exercise {ex_id}" in text

    spec = exercises.load_spec(ex_id)
    assert spec.id == ex_id
    assert spec.prompt.strip()
    assert spec.hint.strip()
    assert spec.sidecar is None


# ---------------------------------------------------------------------------
# Gate 1b — per-exercise case pins (catches a silent expectation edit)
# ---------------------------------------------------------------------------


def test_ch10_constant_is_defined_in_source():
    """ch10's headline: CONSTANT is not built in — the reference defines
    it from CREATE/,/DOES>, then stamps a constant that reads back."""
    spec = exercises.load_spec("forth-104/01-constant")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["answer ."] == ["42"]
    sol = exercises.load_solution_text("forth-104/01-constant")
    assert ": CONSTANT" in sol
    assert "CREATE" in sol and "DOES>" in sol


def test_ch10_doubled_folds_field_and_literal():
    spec = exercises.load_spec("forth-104/02-doubled")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["x ."] == ["42"]
    sol = exercises.load_solution_text("forth-104/02-doubled")
    assert "DOES> @ 2 *" in sol


def test_ch10_family_stamps_two_children():
    """One defining word mints a whole family — two independent children,
    each folding to its own literal."""
    spec = exercises.load_spec("forth-104/03-family")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["six ."] == ["36"]
    assert drivers["nine ."] == ["81"]
    sol = exercises.load_solution_text("forth-104/03-family")
    assert "SQUARED-C six" in sol
    assert "SQUARED-C nine" in sol


def test_ch11_macro_substitutes_its_body():
    spec = exercises.load_spec("forth-105/01-macro")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["5 bump ."] == ["6"]
    sol = exercises.load_solution_text("forth-105/01-macro")
    assert "MACRO: bump 1 + ;" in sol


def test_ch11_macros_compose():
    """hundredx is built ON tenx — the composition lesson."""
    spec = exercises.load_spec("forth-105/02-compose")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["5 hundredx ."] == ["500"]
    sol = exercises.load_solution_text("forth-105/02-compose")
    assert "MACRO: hundredx tenx tenx ;" in sol


def test_ch11_width_is_a_named_literal():
    spec = exercises.load_spec("forth-105/03-width")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["WIDTH ."] == ["40"]
    assert drivers["WIDTH 2 / ."] == ["20"]


# ---------------------------------------------------------------------------
# Gate 2 — every ```forth fence in chapters 10-11 compiles + runs
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```forth\n(.*?)```", re.DOTALL)


def _iter_forth_snippets():
    for chapter in _CHAPTERS:
        assert chapter.exists(), f"chapter page missing: {chapter}"
        text = chapter.read_text(encoding="utf-8")
        for i, m in enumerate(_FENCE_RE.finditer(text)):
            yield chapter.name, i, m.group(1)


_SNIPPETS = list(_iter_forth_snippets())


def test_chapter_pages_exist():
    for chapter in _CHAPTERS:
        assert chapter.exists(), f"expected chapter page at {chapter}"


def test_snippets_were_found():
    """Guard against a regex / rename silently extracting zero snippets.
    ch10 has 6 runnable forth fences, ch11 has 6."""
    names = {name for name, _, _ in _SNIPPETS}
    assert "10-defining-words.md" in names
    assert "11-macros.md" in names
    assert len(_SNIPPETS) >= 10, (
        f"expected >= 10 forth snippets across ch10-11, found {len(_SNIPPETS)}"
    )


@pytest.mark.parametrize(
    "chapter,index,snippet",
    _SNIPPETS,
    ids=[f"{name}#{i}" for name, i, _ in _SNIPPETS],
)
def test_forth_snippet_compiles_and_runs(
    chapter: str, index: int, snippet: str, tmp_path: Path
):
    """Every ```forth fence runs cleanly through the host Runner.

    These meta-layer chapters reference no sidecar-bound blocks, so no
    ``.world.toml`` is synthesized — a defining word / macro definition
    (with or without a use site) lexes, parses, expands, stack-checks,
    and executes once without raising. A definition-only fence emits no
    events; that is a clean run, not a failure."""
    fs_path = tmp_path / "snippet.fs"
    fs_path.write_text(snippet, encoding="utf-8")

    runner = Runner.from_path(fs_path)
    runner.run_once()  # raises on any pipeline/runtime error → test fails
