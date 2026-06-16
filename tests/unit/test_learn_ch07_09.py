"""Self-validating tests for the "Learn Forth with mforth" Part I
chapters 7-9 (bead mforth-roz.4): state, output, factoring.

Two gates, mirroring the design drawer
(``drawer_mforth_decisions_00f669348c36c8702bb88dcc``, §3):

1. **Every bundled ``forth-103`` reference solution passes its own
   checker.** This is the auto-extending self-validation meta-test —
   it discovers exercises by walking ``exercises.list_ids()``, so any
   new ``forth-103/*`` exercise added later is covered automatically
   with no edit here. A spec whose reference answer does not satisfy
   its own ``expect`` cases is a red exercise and fails the suite.

2. **Per-exercise case pins.** Concrete assertions on the prompt,
   hint, and a representative driver/expect for each of the six ch7-9
   exercises, so a silent edit to a spec's expectations is caught.

The CLI surface (``mforth check`` exit codes) is covered by
``tests/integration/test_check_cli.py``; the loader + engine internals
by ``tests/unit/test_check.py``. This file is the *content* gate for
the ch7-9 batch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mforth import exercises
from mforth.cli_check import run_check

TRACK = "forth-103"

# The six exercises this batch ships, in chapter order
# (ch7 state, ch8 output, ch9 factoring).
CH07_09_IDS = [
    f"{TRACK}/01-bump",
    f"{TRACK}/02-total",
    f"{TRACK}/03-report",
    f"{TRACK}/04-show-count",
    f"{TRACK}/05-vocabulary",
    f"{TRACK}/06-refactor",
]


def _track_ids() -> list[str]:
    """Every bundled exercise id under the ``forth-103`` track."""
    return [i for i in exercises.list_ids() if i.startswith(f"{TRACK}/")]


# ---------------------------------------------------------------------------
# Gate 1 — auto-extending: every bundled forth-103 solution passes its checker
# ---------------------------------------------------------------------------


def test_track_is_discovered():
    """Sanity: the track exists and ships the six exercises this batch
    introduced (a guard so an accidental file deletion is caught)."""
    found = set(_track_ids())
    for ex_id in CH07_09_IDS:
        assert ex_id in found, f"missing bundled exercise: {ex_id}"


@pytest.mark.parametrize("ex_id", _track_ids())
def test_reference_solution_passes_its_own_checker(ex_id: str, tmp_path: Path):
    """Auto-extending meta-test: discover each ``forth-103`` exercise and
    run its bundled reference solution through the real checker. The
    reference must satisfy every ``expect`` case in its own spec."""
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
    spec has a non-empty prompt + hint (the hint is shown on failure, so
    an empty one is a learner-facing bug)."""
    text = exercises.load_solution_text(ex_id)
    assert f"@exercise {ex_id}" in text

    spec = exercises.load_spec(ex_id)
    assert spec.id == ex_id
    assert spec.prompt.strip()
    assert spec.hint.strip()
    # Part-I exercises are abstract: no simulator sidecar.
    assert spec.sidecar is None


# ---------------------------------------------------------------------------
# Gate 2 — per-exercise case pins (ch7 state)
# ---------------------------------------------------------------------------


def test_ch7_bump_counter():
    spec = exercises.load_spec(f"{TRACK}/01-bump")
    assert "bump" in spec.prompt.lower()
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["bump count @ ."] == ["1"]
    assert drivers["bump bump bump count @ ."] == ["3"]


def test_ch7_running_total():
    spec = exercises.load_spec(f"{TRACK}/02-total")
    assert "total" in spec.prompt.lower()
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["5 add  3 add  total @ ."] == ["8"]
    # Folding a negative number in reduces the total.
    assert drivers["10 add  -4 add  total @ ."] == ["6"]


# ---------------------------------------------------------------------------
# Gate 2 — per-exercise case pins (ch8 output)
# ---------------------------------------------------------------------------


def test_ch8_report_is_two_prints():
    """A labelled message is TWO printed events — label then value —
    NOT one joined string. This is the headline ch8 teaching point."""
    spec = exercises.load_spec(f"{TRACK}/03-report")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["42 report"] == ["score=", "42"]


def test_ch8_show_count_pairs_state_and_output():
    spec = exercises.load_spec(f"{TRACK}/04-show-count")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["3 count !  show-count"] == ["count=", "3"]
    # Two reports across changing state stay in order, two events each.
    assert drivers["3 count !  show-count  9 count !  show-count"] == [
        "count=",
        "3",
        "count=",
        "9",
    ]


# ---------------------------------------------------------------------------
# Gate 2 — per-exercise case pins (ch9 factoring)
# ---------------------------------------------------------------------------


def test_ch9_vocabulary_reuses_squared():
    spec = exercises.load_spec(f"{TRACK}/05-vocabulary")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["4 squared ."] == ["16"]
    assert drivers["2 cubed ."] == ["8"]
    # The reference `cubed` is built ON `squared` — the factoring lesson.
    sol = exercises.load_solution_text(f"{TRACK}/05-vocabulary")
    assert "squared" in sol
    assert ": cubed" in sol


def test_ch9_refactor_names_the_piece():
    spec = exercises.load_spec(f"{TRACK}/06-refactor")
    drivers = {c.driver: c.expect for c in spec.cases}
    assert drivers["5 announce"] == ["doubled=", "10"]
    # The reference factors the doubling into its own named word.
    sol = exercises.load_solution_text(f"{TRACK}/06-refactor")
    assert ": double" in sol
    assert ": announce" in sol


# ---------------------------------------------------------------------------
# Prose gate — the chapter pages exist and thread the series together
# ---------------------------------------------------------------------------

_DOCS = Path(__file__).resolve().parents[2] / "docs" / "tutorials" / "learn-forth"


@pytest.mark.parametrize(
    "page,must_link",
    [
        ("07-state.md", "08-output.md"),
        ("08-output.md", "09-factoring.md"),
        # ch9 now hands off to the meta layer (ch10 defining words), which
        # closes Part I before the simulator chapters (bead mforth-7h1.5).
        ("09-factoring.md", "10-defining-words.md"),
    ],
)
def test_chapter_page_exists_and_links_next(page: str, must_link: str):
    """Each ch7-9 page exists and forward-links the next chapter, so the
    Part I series threads together. (The nav/index wiring itself is owned
    by the roz.7 task; this only checks the in-prose cross-links.)"""
    path = _DOCS / page
    assert path.is_file(), f"missing chapter page: {page}"
    body = path.read_text(encoding="utf-8")
    assert must_link in body, f"{page} should forward-link {must_link}"
