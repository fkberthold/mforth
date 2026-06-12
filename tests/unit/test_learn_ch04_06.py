"""Verification gate for "Learn Forth with mforth" Part I, chapters 4-6
(bead mforth-roz.3): arithmetic & truth, branching, looping.

Two things are pinned here:

* **Every reference solution in the ``forth-102`` track passes its own
  checker.** This is the chapter-local mirror of the global
  self-validation meta-test in ``tests/integration/test_check_cli.py``;
  it auto-extends as new ``forth-102/*`` exercises are added, so a
  solution that silently goes red is caught in the unit suite too.

* **Every ``​```forth`` fence in the three chapter pages runs.** The
  tutorial promises that "every snippet has been compiled and run"; this
  test makes that promise enforceable. Fences build on one another — a
  fence that *defines* words is remembered and prepended to later usage
  fences — and exactly one fence is deliberately broken (the unbalanced
  ``IF`` demonstration in chapter 5), which must raise.

Both layers run through the same host ``Runner`` → ``MockWorld`` →
``EventStream`` path the ``mforth check`` engine uses, so what passes
here is what a learner sees.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mforth import exercises
from mforth.cli_check import run_check

# Repo root: tests/unit/<this file> -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHAPTER_DIR = _REPO_ROOT / "docs" / "tutorials" / "learn-forth"

TRACK = "forth-102"

# The nine exercises this batch ships, in chapter order. Kept as an
# explicit list so a dropped file is a hard failure here (not a silently
# shorter parametrization).
EXPECTED_IDS = [
    f"{TRACK}/01-even",
    f"{TRACK}/02-odd",
    f"{TRACK}/03-between",
    f"{TRACK}/04-abs",
    f"{TRACK}/05-max",
    f"{TRACK}/06-sign",
    f"{TRACK}/07-sum",
    f"{TRACK}/08-factorial",
    f"{TRACK}/09-countdown",
]

CHAPTERS = [
    _CHAPTER_DIR / "04-arithmetic.md",
    _CHAPTER_DIR / "05-branching.md",
    _CHAPTER_DIR / "06-looping.md",
]

# A fence containing this token is the deliberately-broken demo and must
# raise rather than run clean.
_KNOWN_BAD_TOKEN = "oops"


def _track_ids() -> list[str]:
    return sorted(i for i in exercises.list_ids() if i.startswith(f"{TRACK}/"))


# ---------------------------------------------------------------------------
# Spec / solution presence
# ---------------------------------------------------------------------------


def test_track_ships_expected_exercises():
    """The forth-102 track holds exactly the nine chapter-4..6 exercises."""
    assert _track_ids() == EXPECTED_IDS


@pytest.mark.parametrize("ex_id", EXPECTED_IDS)
def test_spec_loads_and_has_solution(ex_id):
    spec = exercises.load_spec(ex_id)
    assert spec.id == ex_id
    assert spec.prompt.strip()
    assert spec.hint.strip()
    assert spec.cases, f"{ex_id} declares no cases"
    assert exercises.has_solution(ex_id), f"{ex_id} ships no reference solution"


# ---------------------------------------------------------------------------
# Self-validation: every reference solution passes its own checker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ex_id", EXPECTED_IDS)
def test_reference_solution_passes(ex_id, tmp_path):
    sol = tmp_path / "ref.fs"
    sol.write_text(exercises.load_solution_text(ex_id))
    result = run_check(sol)
    assert result.passed, (
        f"reference solution for {ex_id} failed: "
        f"{result.num_passed}/{result.total} cases pass; "
        f"failures={result.failures}"
    )
    assert result.num_passed == result.total


# ---------------------------------------------------------------------------
# Behavioural spot-checks for the chapter's headline words
# ---------------------------------------------------------------------------


def _run_snippet(source: str) -> list[str]:
    """Run ``source`` and return the ordered printed strings, via the same
    engine the checker uses (so this mirrors what a learner sees)."""
    from mforth import exercises as _ex
    from mforth.cli_check import _run_case

    return _run_case(source, _ex.Case(driver="", expect=[]), None)


@pytest.mark.parametrize(
    "source, expect",
    [
        # ch4: MOD, comparisons, the 0/1 boolean encoding, AND/OR/NOT.
        ("17 5 MOD .", ["2"]),
        ("5 3 < . 5 3 > . 4 4 = .", ["0", "1", "1"]),
        ("1 1 AND . 1 0 OR . 0 0 OR .", ["1", "1", "0"]),
        ("0 NOT . 1 NOT . 7 NOT .", ["1", "0", "0"]),
        # ch5: IF / IF-ELSE / nested.
        (": pay ( n -- n ) DUP 100 > IF 10 - THEN ; 150 pay . 50 pay .", ["140", "50"]),
        # ch6: BEGIN/UNTIL, DO/LOOP with I, zero-trip.
        (": tick3 ( -- ) 0 BEGIN 1 + DUP . DUP 3 = UNTIL DROP ; tick3", ["1", "2", "3"]),
        (": squares ( -- ) 4 1 DO I I * . LOOP ; squares", ["1", "4", "9"]),
        (": z ( -- ) 5 5 DO I . LOOP 99 . ; z", ["99"]),
    ],
)
def test_headline_word_behaviour(source, expect):
    assert _run_snippet(source) == expect


def test_boolean_encoding_is_zero_one():
    """The chapter teaches `0` false / `1` true (mlog encoding, not -1)."""
    assert _run_snippet("3 5 < .") == ["1"]
    assert _run_snippet("5 3 < .") == ["0"]


# ---------------------------------------------------------------------------
# Prose snippet gate: every ```forth fence in the chapters runs
# ---------------------------------------------------------------------------


def _forth_fences(text: str) -> list[str]:
    return [b.rstrip("\n") for b in re.findall(r"```forth\n(.*?)```", text, re.DOTALL)]


def _chapter_fence_cases():
    cases = []
    for md in CHAPTERS:
        for idx, body in enumerate(_forth_fences(md.read_text(encoding="utf-8"))):
            cases.append(pytest.param(md.name, idx, body, id=f"{md.name}#{idx}"))
    return cases


def test_chapters_exist():
    for md in CHAPTERS:
        assert md.is_file(), f"missing chapter page: {md}"
    # Sanity: each chapter has at least a few runnable fences.
    for md in CHAPTERS:
        assert _forth_fences(md.read_text(encoding="utf-8")), f"{md.name} has no forth fences"


@pytest.mark.parametrize("fname, idx, body", _chapter_fence_cases())
def test_every_forth_fence_runs(fname, idx, body):
    """Each forth fence lexes, parses, stackchecks, and runs without error.

    Fences accumulate: a usage fence may call a word defined in an earlier
    fence of the same chapter, so we prepend the definitions seen so far in
    that file. The single deliberately-broken fence (tagged by ``oops``)
    must instead raise.
    """
    # Gather definition-bearing fences that precede this one in the file.
    md = _CHAPTER_DIR / fname
    all_fences = _forth_fences(md.read_text(encoding="utf-8"))
    prior_defs: list[str] = []
    for body_before in all_fences[:idx]:
        if body_before.lstrip().startswith(":") and _KNOWN_BAD_TOKEN not in body_before:
            prior_defs.extend(body_before.splitlines())

    full = ("\n".join(prior_defs) + "\n" + body) if prior_defs else body

    if _KNOWN_BAD_TOKEN in body:
        # The unbalanced-IF demo: must be rejected, not run clean.
        with pytest.raises(Exception):
            _run_snippet(full)
    else:
        # Must run without raising. (Return value/printed output is asserted
        # by the dedicated behaviour tests above; here we only gate runnable.)
        _run_snippet(full)
