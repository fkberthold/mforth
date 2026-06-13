"""Tutorial-level gate for the "Learn Forth with mforth" series (bead
mforth-roz.7).

This does NOT re-run every exercise checker — that is owned by the
self-validation meta-test in ``tests/integration/test_check_cli.py``
(``test_every_reference_solution_passes_its_own_checker``, parametrized over
``exercises.list_ids()``) and by the per-chapter tests under ``tests/unit``.
Its job is to prove the SERIES IS WHOLE: every chapter page exists, every one
is wired into the published nav, and every exercise track the tutorial relies
on is actually bundled. A chapter that is written but not navigable, or a
track that silently failed to ship, is what this catches.
"""

from __future__ import annotations

from pathlib import Path

from mforth import exercises

_REPO = Path(__file__).resolve().parents[2]
_LEARN = _REPO / "docs" / "tutorials" / "learn-forth"
_MKDOCS = _REPO / "mkdocs.yml"

# The 15 chapters, in order, plus the landing page.
_CHAPTERS = [
    "01-stack.md", "02-juggling.md", "03-defining.md",
    "04-arithmetic.md", "05-branching.md", "06-looping.md",
    "07-state.md", "08-output.md", "09-factoring.md",
    "10-simulator.md", "11-sensing.md", "12-controlling.md",
    "13-control-loop.md", "14-capstone.md", "15-where-next.md",
]
_PAGES = ["index.md", *_CHAPTERS]

# Exercise tracks the tutorial is built on.
_TRACKS = ["forth-101", "forth-102", "forth-103", "sim-101", "sim-102"]


def test_all_chapter_pages_exist() -> None:
    missing = [p for p in _PAGES if not (_LEARN / p).is_file()]
    assert not missing, f"Learn-Forth pages missing on disk: {missing}"


def test_every_page_is_wired_into_nav() -> None:
    nav = _MKDOCS.read_text(encoding="utf-8")
    missing = [
        p for p in _PAGES
        if f"tutorials/learn-forth/{p}" not in nav
    ]
    assert not missing, (
        f"Learn-Forth pages not referenced in mkdocs.yml nav (orphans): {missing}"
    )


def test_every_exercise_track_is_bundled() -> None:
    ids = exercises.list_ids()
    assert ids, "no exercises bundled at all"
    tracks_seen = {eid.split("/", 1)[0] for eid in ids}
    missing = [t for t in _TRACKS if t not in tracks_seen]
    assert not missing, f"Learn-Forth exercise tracks not bundled: {missing}"


def test_every_bundled_exercise_has_a_solution() -> None:
    # Structural completeness: every discoverable spec ships a reference
    # solution (the meta-test then proves each one actually passes).
    no_solution = [eid for eid in exercises.list_ids() if not exercises.has_solution(eid)]
    assert not no_solution, f"exercises missing a reference solution: {no_solution}"
