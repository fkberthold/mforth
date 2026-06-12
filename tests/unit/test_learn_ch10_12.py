"""Self-validation gate for the "Learn Forth with mforth" Part II
chapters 10-12 (bead mforth-roz.5).

Two correctness gates keep these tutorial chapters from rotting — the
same pair the design drawer (``drawer_mforth_decisions_00f669348c36c8702bb88dcc``)
mandates for every chapter batch:

1. **Every bundled ``sim-101`` reference solution passes its own
   checker.** This iterates :func:`mforth.exercises.list_ids` and runs
   :func:`mforth.cli_check.run_check` on the bundled
   ``*.solution.fs`` for each ``sim-101`` id — so the test AUTO-EXTENDS:
   a future chapter worker that drops another ``sim-101`` exercise pair
   is covered the moment the spec lands, with no edit here. A red
   solution (declared-vs-inferred stack mismatch, wrong expected output,
   a typo'd driver) fails this test rather than shipping a broken
   exercise to a learner.

2. **Every ```forth fence in chapters 10-12 compiles + runs.** A small
   extractor pulls each fenced ``forth`` block out of the three chapter
   pages and runs it once through the real host
   :class:`~mforth.backend.runner.Runner` (lex → parse → resolve →
   stackcheck → execute). A snippet that no longer compiles — a renamed
   word, a stack-effect drift — fails here, so the prose can't claim
   something the language doesn't do.

Sim snippets reference sidecar-bound block names (``display``,
``vault1``, ``miner``, …). Rather than transcribe a per-snippet sidecar,
the extractor scans each snippet for bare lowercase identifiers that look
like link handles and synthesizes a permissive ``.world.toml`` binding
each as a ``generic`` block (a ``message`` block for ``display``, a
``switch`` for ``miner``). That keeps the gate focused on "does the Forth
compile and run", which is what the prose promises the reader.
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
    _CHAPTER_DIR / "10-simulator.md",
    _CHAPTER_DIR / "11-sensing.md",
    _CHAPTER_DIR / "12-controlling.md",
]

_TRACK = "sim-101"


# ---------------------------------------------------------------------------
# Gate 1 — every bundled sim-101 solution passes its checker
# ---------------------------------------------------------------------------


def _sim_ids() -> list[str]:
    return [i for i in exercises.list_ids() if i.startswith(f"{_TRACK}/")]


def test_sim_track_is_populated():
    """The sim-101 track exists and carries the chapter 10-12 exercises.

    A floor so a packaging regression that drops the bundled specs (or a
    bad ``id``) turns red here instead of silently shrinking gate 1's
    parametrization to nothing.
    """
    ids = _sim_ids()
    assert ids, "no sim-101 exercises are bundled — Part II checker gate is empty"
    assert len(ids) >= 6, f"expected >= 6 sim-101 exercises, found {len(ids)}: {ids}"


@pytest.mark.parametrize("ex_id", _sim_ids())
def test_sim_solution_passes_its_checker(ex_id: str, tmp_path: Path):
    """Each bundled ``sim-101`` reference solution prints ``✓`` under the
    checker — i.e. ``run_check`` reports all cases pass. Auto-extends to
    any future sim-101 exercise."""
    assert exercises.has_solution(ex_id), f"{ex_id} has no bundled solution"
    sol = tmp_path / "solution.fs"
    sol.write_text(exercises.load_solution_text(ex_id), encoding="utf-8")

    result = run_check(sol)

    assert result.exercise_id == ex_id
    assert result.passed, (
        f"{ex_id} solution did not pass: {result.num_passed}/{result.total} "
        f"cases; failures={result.failures}"
    )


def test_every_sim_spec_declares_a_sidecar():
    """Part II exercises sense/control a simulated world, so each spec must
    carry an inline ``sidecar`` — otherwise the block names the solution
    references would not resolve."""
    for ex_id in _sim_ids():
        spec = exercises.load_spec(ex_id)
        assert spec.sidecar is not None and spec.sidecar.strip(), (
            f"{ex_id} is a simulator exercise but declares no [sidecar]"
        )


# ---------------------------------------------------------------------------
# Gate 2 — every ```forth fence in chapters 10-12 compiles + runs
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```forth\n(.*?)```", re.DOTALL)

# Identifiers a snippet may reference as sidecar-bound block handles. Map
# each to the block `type` its prose uses so the synthesized sidecar is
# faithful enough to run.
_KNOWN_LINK_TYPES = {
    "display": "message",
    "miner": "switch",
    "generator1": "switch",
}
# Bare lowercase words that are NOT link handles (Forth words, variable
# names introduced in-snippet, etc.) — never bind these.
_NON_LINK_WORDS = {
    "if",
    "else",
    "then",
    "do",
    "loop",
    "begin",
    "until",
    "variable",
    "dup",
    "drop",
    "swap",
    "over",
    "rot",
    "nip",
    "tuck",
    "and",
    "or",
    "not",
    "mod",
    "restock",  # a definition name, not a link
    "items",  # an in-snippet VARIABLE
    "greet",
    "readout",
    "i",
    "j",
}


def _iter_forth_snippets():
    for chapter in _CHAPTERS:
        assert chapter.exists(), f"chapter page missing: {chapter}"
        text = chapter.read_text(encoding="utf-8")
        for i, m in enumerate(_FENCE_RE.finditer(text)):
            yield chapter.name, i, m.group(1)


_SNIPPETS = list(_iter_forth_snippets())


def _looks_like_link(word: str, snippet: str) -> bool:
    """Heuristic: a bare lowercase identifier that is not a known Forth
    word and not declared as a VARIABLE in this snippet is treated as a
    sidecar-bound block handle."""
    if word in _NON_LINK_WORDS:
        return False
    if re.search(rf"\bVARIABLE\s+{re.escape(word)}\b", snippet, re.IGNORECASE):
        return False
    if re.search(rf":\s+{re.escape(word)}\b", snippet):  # definition name
        return False
    return word.isalpha() or bool(re.fullmatch(r"[a-z][a-z0-9]*", word))


def _synthesize_sidecar(snippet: str) -> str:
    """Build a permissive ``.world.toml`` binding every block-handle-like
    identifier the snippet references."""
    candidates = set(re.findall(r"(?<![@:.])\b([a-z][a-z0-9]*)\b", snippet))
    links = []
    for word in sorted(candidates):
        if not _looks_like_link(word, snippet):
            continue
        block_type = _KNOWN_LINK_TYPES.get(word, "generic")
        block = f'[links.{word}]\ntype = "{block_type}"\ntarget = "{word}_blk"\n'
        if block_type == "switch":
            block += "enabled = false\n"
        links.append(block)
    return "\n".join(links)


def test_chapter_pages_exist():
    for chapter in _CHAPTERS:
        assert chapter.exists(), f"expected chapter page at {chapter}"


def test_snippets_were_found():
    """Guard against a regex / rename silently extracting zero snippets."""
    assert len(_SNIPPETS) >= 9, f"expected >= 9 forth snippets, found {len(_SNIPPETS)}"


@pytest.mark.parametrize(
    "chapter,index,snippet",
    _SNIPPETS,
    ids=[f"{name}#{i}" for name, i, _ in _SNIPPETS],
)
def test_forth_snippet_compiles_and_runs(
    chapter: str, index: int, snippet: str, tmp_path: Path
):
    """Every ```forth fence runs cleanly through the host Runner."""
    fs_path = tmp_path / "snippet.fs"
    fs_path.write_text(snippet, encoding="utf-8")
    sidecar = _synthesize_sidecar(snippet)
    if sidecar.strip():
        (tmp_path / "snippet.world.toml").write_text(sidecar, encoding="utf-8")

    runner = Runner.from_path(fs_path)
    runner.run_once()  # raises on any pipeline/runtime error → test fails
