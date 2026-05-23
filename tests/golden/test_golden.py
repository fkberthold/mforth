"""Golden mlog harness — bead mforth-10t.29.

For each ``tests/golden/<name>.fs`` fixture (with sibling
``<name>.world.toml`` and ``<name>.expected.mlog``), this harness runs
the compile pipeline:

    lex → parse → resolve → stackcheck → allocate_slots → emit
            → serialize to mlog text

and compares the result against ``<name>.expected.mlog`` byte-for-byte.

Serialization format (the contract every backend bead must produce)
-------------------------------------------------------------------

The emitter yields ``MlogInstr`` 3-tuples
``(label: str | None, opcode: str, operands: tuple[str, ...])`` per the
bead mforth-10t.16 ship contract.  This harness renders each tuple as a
single line:

* If ``label`` is not None, emit ``"<label>:"`` on its own line first
  (no indentation, no trailing whitespace) — anticipating bead .19's
  label-resolution pass.  ``.16`` always emits ``label=None``.
* Emit ``opcode`` followed by each operand, separated by single spaces,
  no trailing whitespace.
* Lines are joined with ``"\n"``.
* The file ends with exactly one trailing newline.
* No blank lines, no comments (until a future bead introduces them).

This format MUST match what an in-game paste expects (mlog itself is
one-instruction-per-line, no leading whitespace, no blank-line
separators).  The trailing newline matches POSIX text-file convention
and is what an editor will produce on save.

Missing / xfail goldens
-----------------------

* If a fixture has no ``.expected.mlog`` AND the fixture name is listed
  in ``XFAIL_FIXTURES``, the test is marked xfail with the reason
  naming the blocker bead.  When that bead ships, the fixture
  maintainer flips the entry off and runs
  ``pytest tests/golden --update-golden`` to populate the golden.
* If a fixture has no ``.expected.mlog`` and is NOT in
  ``XFAIL_FIXTURES``, the test FAILS with a clear hint to run
  ``pytest --update-golden``.  Silent skip would let drift accumulate.

``--update-golden`` regen
-------------------------

When the ``--update-golden`` CLI flag is set (see ``conftest.py``), the
harness writes the actual output to ``<name>.expected.mlog`` instead of
asserting equality.  Use this after an intentional codegen change; eyeball
``git diff`` to confirm the regen looks right; commit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from mforth.backend.mlog import MlogInstr, allocate_slots, emit
from mforth.backend.sidecar import load_sidecar
from mforth.parse import parse
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

GOLDEN_DIR = Path(__file__).parent

# Map fixture stem → xfail reason (blocker bead ID).  Fixtures listed
# here ship .fs + .world.toml but no .expected.mlog; the test is marked
# xfail until the blocker ships and the fixture gets regenerated.
#
# Keep this list short and specific — every entry is a known-incomplete
# coverage gap, and the xfail message is the bead that closes the gap.
XFAIL_FIXTURES: Mapping[str, str] = {
    "blink": "blocks on mforth-10t.18 (mlog Mindustry primitives) + "
             "mforth-10t.19 (final pass: label resolution + sidecar "
             "substitution)",
    "counter": "blocks on mforth-10t.18 + mforth-10t.19",
    "if_then": "blocks on mforth-10t.17 (mlog control flow codegen)",
    "begin_until": "blocks on mforth-10t.17",
    "getlink_index_mode": "blocks on mforth-10t.18 + mforth-10t.19 "
                          "(needs Mindustry primitive emit + sidecar "
                          "index-mode getlink prologue)",
}


def _discover_fixtures() -> list[Path]:
    """Return every ``*.fs`` under tests/golden/, sorted for stable IDs."""
    return sorted(GOLDEN_DIR.glob("*.fs"))


# ---------------------------------------------------------------------------
# Pipeline + serialization
# ---------------------------------------------------------------------------


def _compile_to_tuples(src: str, file: str) -> list[MlogInstr]:
    """Run lex → parse → resolve → stackcheck → allocate_slots → emit."""
    program = parse(src, file=file)
    result = stackcheck(program)
    slots = allocate_slots(result)
    return emit(result, slots)


def _serialize(instrs: list[MlogInstr]) -> str:
    """Render an instruction list as canonical mlog text.

    See module docstring for the format contract.  One label-line + one
    instruction-line per tuple as applicable; single-space-joined
    operands; trailing newline at end of file.
    """
    lines: list[str] = []
    for label, opcode, operands in instrs:
        if label is not None:
            lines.append(f"{label}:")
        lines.append(" ".join((opcode, *operands)))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fs_path",
    _discover_fixtures(),
    ids=lambda p: p.stem,
)
def test_golden(fs_path: Path, request: pytest.FixtureRequest) -> None:
    """Compile <stem>.fs and compare against <stem>.expected.mlog."""
    stem = fs_path.stem
    sidecar_path = fs_path.with_suffix(".world.toml")
    expected_path = fs_path.with_suffix(".expected.mlog")

    # Sidecar must exist for every fixture (golden discipline: a .fs
    # without its sidecar is half a fixture).  Load it eagerly so the
    # harness fails on sidecar parse errors with a clear pointer.
    assert sidecar_path.exists(), (
        f"missing sidecar: {sidecar_path.name} "
        f"(every <name>.fs needs a sibling <name>.world.toml)"
    )
    load_sidecar(sidecar_path)  # raises SidecarError on malformed input

    # xfail FIRST, before compiling.  Fixtures listed in XFAIL_FIXTURES
    # are known to exercise codegen paths that don't exist yet
    # (.17 control flow, .18 Mindustry primitives, .19 final pass), so
    # the compile call would raise NotImplementedError before we reach
    # the comparison.  Mark xfail with strict=False so a fixture that
    # accidentally starts passing (e.g. blocker shipped) becomes
    # visible as an unexpected XPASS, prompting a manual regen.
    if stem in XFAIL_FIXTURES:
        pytest.xfail(reason=XFAIL_FIXTURES[stem])

    src = fs_path.read_text()
    actual = _serialize(_compile_to_tuples(src, file=fs_path.name))

    update_golden = request.config.getoption("--update-golden")

    if update_golden:
        expected_path.write_text(actual)
        return

    if not expected_path.exists():
        pytest.fail(
            f"missing golden: {expected_path.name}.  "
            f"If this fixture exercises a new codegen path that ships "
            f"now, run `pytest tests/golden --update-golden` to write "
            f"the file, eyeball `git diff` to confirm the output is "
            f"right, and commit.  If the path is not yet shipped, add "
            f"the fixture stem to XFAIL_FIXTURES in test_golden.py "
            f"with a reason naming the blocker bead."
        )

    expected = expected_path.read_text()
    if actual != expected:
        # Build a unified diff so the failure message points at the
        # exact mismatch.  Use difflib to keep deps zero.
        import difflib

        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"{expected_path.name} (expected)",
                tofile=f"{stem}.actual.mlog",
            )
        )
        pytest.fail(
            f"golden mismatch for {stem}\n"
            f"(regenerate with `pytest tests/golden --update-golden` "
            f"if the change is intentional)\n\n{diff}",
            pytrace=False,
        )
