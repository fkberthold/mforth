"""Self-validation gate for "Learn Forth with mforth" Part I, chapters 1-3
(bead mforth-roz.2).

Two correctness gates keep these tutorial chapters from rotting (the
design's §3 "TWO automated correctness gates"; drawer
``drawer_mforth_decisions_00f669348c36c8702bb88dcc``):

1. **Every bundled reference solution passes its own checker.** The
   exercise specs are self-validating: running the bundled
   ``*.solution.fs`` through the live ``mforth check`` engine
   (:func:`mforth.cli_check.run_check`) must report all cases passing.
   This test enumerates the whole ``forth-101`` track via
   :func:`mforth.exercises.list_ids`, so it **auto-extends** — any new
   exercise pair dropped into the track is validated here with no edit
   to this file. (The 01-double / 02-nip exemplars from bead roz.1 are
   covered too.)

2. **Every ``forth`` code fence in the chapter prose compiles and runs.**
   A learner copies snippets straight out of the markdown; an
   un-runnable snippet is a teaching bug. This test extracts each
   ```` ```forth ```` block from chapters 1-3 and runs it once through
   the host :class:`~mforth.backend.runner.Runner`, asserting a clean
   lex→parse→resolve→stackcheck→execute with no exception. Illustrative
   stack-effect / infix-vs-postfix listings live in ``text`` fences (not
   ``forth``) precisely so they are NOT executed here.

Mirrors the prose-vs-code self-consistency pattern of
``tests/integration/test_tutorial_equivalence.py`` (bead mforth-mrl),
scoped to the from-zero tutorial's first batch.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from mforth import exercises
from mforth.backend.runner import Runner
from mforth.cli_check import CheckResult, run_check

REPO_ROOT = Path(__file__).resolve().parents[2]
LEARN_DIR = REPO_ROOT / "docs" / "tutorials" / "learn-forth"
TRACK = "forth-101"

# The chapter pages this batch owns. Kept explicit (not a glob) so a
# sibling worker adding chapters 4+ does not silently pull their fences
# into this batch's gate.
CHAPTER_FILES = [
    LEARN_DIR / "01-stack.md",
    LEARN_DIR / "02-juggling.md",
    LEARN_DIR / "03-defining.md",
]

# A ```forth ... ``` fenced block. Captures the body between the fences.
_FORTH_FENCE_RE = re.compile(r"^```forth[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


# ---------------------------------------------------------------------------
# Gate 1 — every bundled reference solution passes its own checker
# ---------------------------------------------------------------------------


def _track_ids() -> list[str]:
    return [i for i in exercises.list_ids() if i.startswith(f"{TRACK}/")]


def test_track_has_expected_exercises() -> None:
    """Sanity floor: the track carries the exemplars plus this batch's
    nine new exercises. Guards against an exercise file being dropped
    from the package data (which would silently shrink gate 1)."""
    ids = set(_track_ids())
    # roz.1 exemplars
    assert f"{TRACK}/01-double" in ids
    assert f"{TRACK}/02-nip" in ids
    # roz.2 chapter 1-3 exercises
    for name in (
        "03-rpn-add-mul",
        "04-rpn-two-groups",
        "05-triplicate",
        "06-peek-under",
        "07-back-rot",
        "08-square",
        "09-cube",
        "10-average",
        "11-quadruple",
    ):
        assert f"{TRACK}/{name}" in ids, f"missing exercise {name}"


@pytest.mark.parametrize("ex_id", _track_ids())
def test_reference_solution_passes_checker(ex_id: str, tmp_path: Path) -> None:
    """Gate 1: the bundled reference solution for every ``forth-101``
    exercise passes ``mforth check`` with all cases green.

    Auto-extends over :func:`exercises.list_ids` — a new exercise pair in
    the track is validated here automatically."""
    assert exercises.has_solution(ex_id), f"{ex_id} has no bundled solution"
    sol = tmp_path / "solution.fs"
    sol.write_text(exercises.load_solution_text(ex_id), encoding="utf-8")

    result = run_check(sol)
    assert isinstance(result, CheckResult)
    assert result.exercise_id == ex_id
    assert result.passed, (
        f"reference solution for {ex_id} failed its own checker: "
        f"{result.num_passed}/{result.total} cases pass; "
        f"failures={result.failures}"
    )


# ---------------------------------------------------------------------------
# Gate 2 — every forth code fence in the prose compiles + runs
# ---------------------------------------------------------------------------


def _forth_fences(md_path: Path) -> list[str]:
    text = md_path.read_text(encoding="utf-8")
    return [m.group(1) for m in _FORTH_FENCE_RE.finditer(text)]


def _all_fence_cases() -> list[tuple[str, int, str]]:
    """Return ``(chapter_filename, fence_index, snippet)`` for every
    ``forth`` fence across the batch's chapters."""
    cases: list[tuple[str, int, str]] = []
    for md in CHAPTER_FILES:
        for i, snippet in enumerate(_forth_fences(md)):
            cases.append((md.name, i, snippet))
    return cases


def test_each_chapter_has_runnable_fences() -> None:
    """Each chapter page exists and carries at least one ``forth`` fence —
    a tripwire against a renamed/empty page or a fence-tag typo."""
    for md in CHAPTER_FILES:
        assert md.exists(), f"missing chapter page: {md}"
        assert _forth_fences(md), f"{md.name}: no ```forth fences found"


@pytest.mark.parametrize(
    "chapter,index,snippet",
    _all_fence_cases(),
    ids=[f"{c}#{i}" for c, i, _ in _all_fence_cases()],
)
def test_prose_forth_snippet_runs(chapter: str, index: int, snippet: str) -> None:
    """Gate 2: every ``forth`` fence runs once through the host Runner
    with no pipeline exception. Bare definitions (no invocation) are
    allowed to emit zero events; the assertion is a clean run, not a
    particular output."""
    with tempfile.TemporaryDirectory() as tmp:
        fs_path = Path(tmp) / "snippet.fs"
        fs_path.write_text(snippet, encoding="utf-8")
        runner = Runner.from_path(fs_path)
        # A clean lex→parse→resolve→stackcheck→execute. Any failure raises
        # and fails the test, naming the chapter + fence index via the id.
        runner.run_once()
