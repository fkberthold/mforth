"""Chapter 15-17 self-validation — bead mforth-roz.6 (Part II control loop,
capstone, where-next; renumbered from 13-15 by mforth-7h1.5 when the
meta-layer chapters joined the end of Part I).

Two correctness gates, mirroring the tutorial's no-rot policy
(design drawer ``drawer_mforth_decisions_00f669348c36c8702bb88dcc`` §3):

1. **Every bundled sim-102 reference solution passes its own checker.**
   The shared self-validation meta-test in
   ``tests/integration/test_check_cli.py`` already covers this for *all*
   tracks; this module pins the sim-102 track explicitly so a regression
   in this batch is diagnosable here, next to the chapters it backs.

2. **Every ``forth`` fence in chapters 15-17 compiles + runs.** The prose
   is the human source of truth; this test is the machine mirror that
   stops a snippet from drifting into something that no longer runs. Each
   fence is executed through the real host
   :class:`~mforth.backend.runner.Runner` against a MockWorld, with a
   permissive superset sidecar declaring every block name the chapters
   reference (``pump``, ``display``, ``unloader``, ``vault1``). Extra
   links are harmless; a fence that names none of them ignores the
   sidecar entirely.

A fence that is *only* a definition (``: word ... ;`` with no main call)
runs cleanly and emits no events — so we assert a clean run, not a
nonzero event count. Fences that DO drive output are additionally exercised
by the run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mforth import exercises
from mforth.backend.runner import Runner

REPO_ROOT = Path(__file__).resolve().parents[2]
LEARN_DIR = REPO_ROOT / "docs" / "tutorials" / "learn-forth"

CHAPTER_FILES = [
    LEARN_DIR / "15-control-loop.md",
    LEARN_DIR / "16-capstone.md",
    LEARN_DIR / "17-where-next.md",
]

# Every block name any chapter-15/16/17 forth fence references. Declared
# all-at-once so a single sidecar resolves every fence; a fence that uses
# none of these just ignores the extra links.
_SUPERSET_SIDECAR = """\
[links.pump]
type   = "switch"
target = "switch1"
enabled = false

[links.display]
type   = "message"
target = "message1"

[links.unloader]
type   = "generic"
target = "unloader1"

[links.vault1]
type   = "generic"
target = "vault1"

[clock]
ipt      = 8
realtime = false
"""

# Bare ``forth`` fences (``` ```forth ``` … ``` ``` ```) — not ``toml``.
_FENCE_RE = re.compile(r"```forth\n(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# Gate 1 — sim-102 reference solutions pass their checkers
# ---------------------------------------------------------------------------

SIM_102_IDS = sorted(i for i in exercises.list_ids() if i.startswith("sim-102/"))


def test_sim_102_track_is_populated():
    """Sanity: this batch shipped its exercises. If the track is empty the
    parametrized check below would silently collect zero cases."""
    assert SIM_102_IDS, "no sim-102 exercises found — did the specs ship?"
    # The five milestones this batch is built around.
    assert "sim-102/01-thermostat" in SIM_102_IDS
    assert "sim-102/05-sorter-step" in SIM_102_IDS


@pytest.mark.parametrize("ex_id", SIM_102_IDS)
def test_sim_102_reference_solution_passes(ex_id, tmp_path):
    """Each sim-102 reference solution passes its own spec's cases.

    Runs the real checker engine (the same ``run_check`` the CLI calls),
    so a passing assertion means the host REPL reproduces the spec's
    expected printed output exactly."""
    from mforth.cli_check import run_check

    sol = tmp_path / "ref.fs"
    sol.write_text(exercises.load_solution_text(ex_id), encoding="utf-8")
    result = run_check(sol)
    assert result.passed, (
        f"reference solution for {ex_id} failed its own checker: "
        f"{result.num_passed}/{result.total} cases, failures={result.failures}"
    )


# ---------------------------------------------------------------------------
# Gate 2 — every forth fence in the prose compiles + runs
# ---------------------------------------------------------------------------


def _iter_fences():
    """Yield ``(chapter_name, fence_index, fence_source)`` for every
    ``forth`` fence across chapters 15-17."""
    for md in CHAPTER_FILES:
        assert md.exists(), f"missing chapter file: {md}"
        text = md.read_text(encoding="utf-8")
        for i, m in enumerate(_FENCE_RE.finditer(text)):
            yield (md.name, i, m.group(1))


_FENCES = list(_iter_fences())


def test_chapters_have_forth_fences():
    """Each of the three chapters ships at least one runnable forth fence
    (guards against an empty-extraction false-green)."""
    names = {name for name, _, _ in _FENCES}
    assert "15-control-loop.md" in names
    assert "16-capstone.md" in names
    assert "17-where-next.md" in names


@pytest.mark.parametrize(
    "chapter,idx,source",
    _FENCES,
    ids=[f"{name}#{idx}" for name, idx, _ in _FENCES],
)
def test_forth_fence_runs(chapter, idx, source, tmp_path):
    """Every ``forth`` fence lexes, parses, stack-checks, and executes once
    through the host Runner against a MockWorld without raising.

    The permissive superset sidecar resolves any block names the fence
    references; fences that reference none ignore it."""
    fs_path = tmp_path / "snippet.fs"
    fs_path.write_text(source, encoding="utf-8")
    (tmp_path / "snippet.world.toml").write_text(_SUPERSET_SIDECAR, encoding="utf-8")

    runner = Runner.from_path(fs_path)
    # A single pass is enough to prove the snippet is real, executable
    # mforth — not just parseable text. No exception == pass.
    runner.run_once()
