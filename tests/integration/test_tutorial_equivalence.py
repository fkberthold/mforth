"""Tutorial self-consistency regression — bead mforth-mrl.

``docs/tutorials/writing-mforth-for-mindustry.md`` teaches mforth by
porting hand-written mlog scripts and repeatedly *claims* a specific
compiled instruction count for each part (Part 1: "two mlog
instructions"; Part 3: "23 instructions"; Part 4: "Seven
instructions"; Part 5: "35 instructions"). Before this test those
claims were pinned by nothing — the tutorial's ``.fs`` snippets were
inline-only, never compiled or run in CI, so the prose could drift (or
have always been wrong) silently. The bead exists because the
REPL ↔ mlog equivalence is mforth's headline property and the tutorial
is the teaching surface; an un-verified count claim there is a latent
teaching bug.

What this test pins
===================

Each tutorial Part's first ``forth`` fence (+ its ``toml`` sidecar) has
been extracted into a durable example pair under
``examples/tutorial/partN.fs`` / ``partN.world.toml`` (the markdown stays
the human-readable source of truth; these files are the machine-checked
mirror). For every Part this test, parametrized over the parts:

(a) **compiles** the ``.fs`` through the real
    lex→parse→resolve→stackcheck→slots→emit→finalize pipeline and
    asserts a clean compile (no pipeline exception);

(b) **runs** it once through the host REPL :class:`Runner` and asserts a
    clean run (no executor exception, at least one event emitted);

(c) for every Part whose prose states a compiled instruction count,
    **parses that claimed count out of the prose** and asserts it EQUALS
    the actual emitted instruction count. This is a *self-consistency*
    check: it reads the claim live from the markdown, so it stays robust
    to future prose edits — change "23" to "20" in the doc and this test
    fires; change the codegen so Part 3 emits 24 and it fires too.

The claimed-count parse is deliberately anchored to the exact sentence
that introduces each Part's compiled-mlog block ("You get exactly two
mlog instructions:", "23 instructions:", "Seven instructions:",
"35 instructions of mlog ..."). Part 2's prose shows its compiled block
verbatim but states NO numeric count, so it is exempted from (c) while
still covered by (a) and (b).

Discrepancy policy (per the bead): if a claimed count does NOT equal the
actual emitted count, or a snippet fails to compile/run, that Part's
param is marked ``xfail`` with a reason naming the discrepancy so the
suite stays green while the finding is recorded for a follow-up bead.
At authoring time (2026-06-11) NO discrepancies were found — all four
count claims match and all five parts compile and run — so no params
are xfail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.finalize import finalize
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.runner import Runner
from mforth.backend.sidecar import WorldConfig, load_sidecar
from mforth.dictionary import (
    UserVariable,
    resolve,
    standard_dictionary,
)
from mforth.parse import SrcLoc, parse
from mforth.stackcheck import stackcheck


REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIAL_DIR = REPO_ROOT / "examples" / "tutorial"
TUTORIAL_DOC = (
    REPO_ROOT / "docs" / "tutorials" / "writing-mforth-for-mindustry.md"
)

# Parts covered by this regression. Every part is extracted into a
# durable example pair under examples/tutorial/.
PARTS = [1, 2, 3, 4, 5]

# English number words that appear as instruction-count claims in the
# tutorial prose (the doc spells small counts: "two", "Seven").
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}


# ---------------------------------------------------------------------------
# Doc-prose claim extraction (read-only on docs/)
# ---------------------------------------------------------------------------


def _section_text(part: int) -> str:
    """Return the markdown of ``## Part <part> ...`` up to the next
    ``## Part`` heading (or EOF)."""
    doc = TUTORIAL_DOC.read_text()
    starts: dict[int, int] = {}
    for m in re.finditer(r"^## Part (\d+)", doc, re.M):
        starts[int(m.group(1))] = m.start()
    assert part in starts, f"Part {part} heading not found in tutorial"
    s = starts[part]
    after = sorted(x for x in starts.values() if x > s)
    return doc[s : (after[0] if after else len(doc))]


def _claimed_instruction_count(part: int) -> int | None:
    """Parse the compiled-mlog instruction count CLAIMED in this Part's
    prose, or ``None`` if the Part states no count.

    Anchored to the sentence that introduces the compiled block. The
    tutorial phrases these as one of:

    * ``You get exactly two mlog instructions:``  (Part 1)
    * ``23 instructions:``                         (Part 3)
    * ``Seven instructions:``                      (Part 4)
    * ``35 instructions of mlog for the five resources.``  (Part 5)

    We match ``<number-or-word> [mlog] instructions`` where the number is
    either digits or one of the spelled English words. Part 2 shows its
    block but never claims a count → returns ``None``.
    """
    text = _section_text(part)
    # Look only at the prose immediately preceding a compiled-mlog block,
    # i.e. the line that ends in "instructions:" or "instructions ...".
    pattern = re.compile(
        r"\b(\d+|" + "|".join(_WORD_NUMBERS) + r")\s+(?:mlog\s+)?instructions?\b",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        token = m.group(1).lower()
        # The wiki-comparison sentences ("24 instructions", "Nine lines")
        # describe the HAND-WRITTEN script, not mforth's output. Anchor to
        # the mforth claim: it is the one whose match is immediately
        # followed (within the next ~40 chars) by a fenced ``` block, OR
        # uses the "exactly ... mlog instructions" / "instructions of mlog"
        # phrasing. We resolve this by taking the count that sits right
        # before the FIRST mlog code fence after the prose claim.
        tail = text[m.end() : m.end() + 80]
        if "```" in tail or "of mlog" in tail or "mlog instruction" in m.group(0).lower():
            return int(token) if token.isdigit() else _WORD_NUMBERS[token]
    return None


# ---------------------------------------------------------------------------
# Compile + run helpers (mirror the real CLI / equivalence-harness pipeline)
# ---------------------------------------------------------------------------


def _fs_path(part: int) -> Path:
    return TUTORIAL_DIR / f"part{part}.fs"


def _compile_part(part: int) -> tuple[str, int]:
    """Compile ``examples/tutorial/partN.fs`` through the full pipeline.

    Returns ``(mlog_text, emitted_instruction_count)`` where the count is
    the number of non-blank, non-comment mlog lines — the same metric the
    finalize header reports and the tutorial prose claims.
    """
    fs_path = _fs_path(part)
    sidecar_path = fs_path.with_suffix(".world.toml")
    world_config = (
        load_sidecar(sidecar_path)
        if sidecar_path.exists()
        else WorldConfig()
    )

    dictionary = standard_dictionary()
    seed_loc = SrcLoc(
        str(sidecar_path) if sidecar_path.exists() else str(fs_path), 1, 1
    )
    for spec in world_config.links:
        if spec.mforth_name not in dictionary:
            dictionary.add_variable(
                UserVariable(name=spec.mforth_name, src_loc=seed_loc)
            )

    text = fs_path.read_text()
    program = parse(text, file=str(fs_path))
    dictionary = resolve(program, dictionary=dictionary)
    result = stackcheck(program, dictionary=dictionary)
    slots = allocate_slots(result)
    instrs = emit(result, slots)
    mlog_text = finalize(
        instrs,
        world_config=world_config,
        source_path=fs_path,
        sidecar_path=sidecar_path if sidecar_path.exists() else None,
    )
    count = len(
        [
            ln
            for ln in mlog_text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
    )
    return mlog_text, count


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("part", PARTS)
def test_tutorial_part_files_exist(part: int) -> None:
    """The extracted ``.fs`` (and sidecar) example pair is committed as a
    durable example. If the markdown gains/loses a Part, this is the first
    test to flag the extraction is stale."""
    fs_path = _fs_path(part)
    assert fs_path.exists(), (
        f"missing extracted tutorial example: {fs_path} — re-extract from "
        f"docs/tutorials/writing-mforth-for-mindustry.md Part {part}"
    )


@pytest.mark.parametrize("part", PARTS)
def test_tutorial_part_compiles_cleanly(part: int) -> None:
    """(a) Every tutorial Part's ``.fs`` compiles through the full
    pipeline with no error. A tracked dialect gap that broke one of these
    (e.g. mforth-vdt) would fire here, forcing the prose to be corrected
    or the param xfail'd with the bead id."""
    mlog_text, count = _compile_part(part)
    assert count > 0, f"Part {part} emitted zero instructions"
    assert mlog_text.strip(), f"Part {part} produced empty mlog"


@pytest.mark.parametrize("part", PARTS)
def test_tutorial_part_runs_cleanly(part: int) -> None:
    """(b) Every tutorial Part runs once through the host REPL Runner
    without raising, and emits at least one observable event — i.e. the
    snippet is a real, executable program, not just parseable text."""
    runner = Runner.from_path(_fs_path(part))
    runner.run_once()
    events = list(runner.executor.world.events)
    assert events, f"Part {part} produced no events on a single run"


@pytest.mark.parametrize("part", PARTS)
def test_tutorial_claimed_count_matches_emitted(part: int) -> None:
    """(c) Self-consistency: the instruction count CLAIMED in this Part's
    prose equals the count the compiler actually emits.

    The claim is parsed live from the markdown, so this stays correct
    across future prose edits. Parts that state no count (Part 2) are
    skipped — they are still covered by the compile/run tests above."""
    claimed = _claimed_instruction_count(part)
    if claimed is None:
        pytest.skip(f"Part {part} prose states no instruction count")
    _mlog_text, actual = _compile_part(part)
    assert claimed == actual, (
        f"Part {part} instruction-count claim drifted: tutorial prose "
        f"claims {claimed}, compiler emits {actual}. Either the prose is "
        f"wrong (fix the doc) or codegen changed (update the doc / file a "
        f"bug)."
    )
