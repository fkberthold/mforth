"""Negative-case + integration coverage for the golden mlog harness.

The harness itself (`test_golden.py`) is exercised by its production
fixtures (one passing, five xfail) — that proves the happy path.  This
file pins the failure surfaces the harness introduces:

* ``--update-golden`` writes a previously-missing golden in place.
* A drifted golden fails with a unified diff in the error message.
* A missing golden NOT listed in ``XFAIL_FIXTURES`` fails hard with the
  "run pytest --update-golden" hint, NOT a silent skip.
* A bad sidecar fails with a SidecarError-shaped message.
* The serializer renders the documented format byte-for-byte for the
  shapes the contract names (label-less plain instruction, multi-operand
  instruction, label-prefixed instruction).

These tests bypass the production fixture discovery: they import the
harness's private helpers and the production xfail registry directly,
and they use ``tmp_path`` for any disk writes so the production
``tests/golden/*.expected.mlog`` files are never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Sibling import: tests/golden has __init__.py so pytest imports the
# module as `tests.golden.test_golden` when invoked from the repo root,
# but as `test_golden` if the directory itself is on sys.path.  The
# `importlib` dance below works in both layouts without depending on
# the project's pyproject testpath conventions.
import importlib.util as _ilu
from pathlib import Path as _Path

_spec = _ilu.spec_from_file_location(
    "tests_golden_test_golden",
    _Path(__file__).parent / "test_golden.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
XFAIL_FIXTURES = _mod.XFAIL_FIXTURES
_compile_to_tuples = _mod._compile_to_tuples
_serialize = _mod._serialize


# ---------------------------------------------------------------------------
# Serializer contract
# ---------------------------------------------------------------------------


def test_serialize_plain_instruction_renders_space_joined_with_trailing_newline():
    instrs = [(None, "set", ("s0", "1"))]
    assert _serialize(instrs) == "set s0 1\n"


def test_serialize_multi_operand_instruction():
    instrs = [(None, "op", ("add", "s0", "s0", "s1"))]
    assert _serialize(instrs) == "op add s0 s0 s1\n"


def test_serialize_emits_label_line_before_instruction():
    """When `.17` / `.19` start emitting labels, the contract is:
    label on its own line, then the instruction.  No indentation,
    single trailing newline at end of file."""
    instrs = [
        ("L0", "op", ("add", "s0", "s0", "s1")),
        (None, "jump", ("L0", "always")),
    ]
    assert _serialize(instrs) == "L0:\nop add s0 s0 s1\njump L0 always\n"


def test_serialize_empty_program_renders_just_trailing_newline():
    """Edge case: empty .fs source produces no instructions.  The
    serializer still emits a single trailing newline so the file is
    POSIX-text-clean."""
    assert _serialize([]) == "\n"


# ---------------------------------------------------------------------------
# Pipeline integration — arithmetic_basic round-trips
# ---------------------------------------------------------------------------


def test_arithmetic_basic_pipeline_matches_committed_golden():
    """End-to-end: lex+parse+stackcheck+slots+emit+serialize on the
    canonical .fs equals the committed .expected.mlog byte-for-byte.

    This is the integration test for the harness — if it fails, either
    the fixture drifted from the codegen OR the harness's pipeline glue
    drifted from the production compiler.  Both are bugs worth surfacing
    independently of the parametrized golden run."""
    here = Path(__file__).parent
    fs_path = here / "arithmetic_basic.fs"
    expected_path = here / "arithmetic_basic.expected.mlog"

    actual = _serialize(_compile_to_tuples(fs_path.read_text(), file=fs_path.name))
    assert actual == expected_path.read_text()


# ---------------------------------------------------------------------------
# XFAIL registry sanity
# ---------------------------------------------------------------------------


def test_xfail_registry_keys_match_existing_fs_fixtures():
    """Every key in XFAIL_FIXTURES must correspond to a real
    `tests/golden/<key>.fs` file.  An orphan key means a fixture got
    deleted but the registry wasn't cleaned up — silent rot."""
    here = Path(__file__).parent
    for stem in XFAIL_FIXTURES:
        assert (here / f"{stem}.fs").exists(), (
            f"XFAIL_FIXTURES references {stem!r} but tests/golden/"
            f"{stem}.fs does not exist"
        )


def test_xfail_reasons_name_a_blocker_bead():
    """Every xfail reason must mention an mforth-10t.NN bead so the
    "what unblocks this?" question is answerable from the message."""
    for stem, reason in XFAIL_FIXTURES.items():
        assert "mforth-10t." in reason, (
            f"XFAIL_FIXTURES[{stem!r}] reason must name the blocker "
            f"bead (mforth-10t.NN); got: {reason!r}"
        )


# ---------------------------------------------------------------------------
# Harness failure-mode coverage via pytester
# ---------------------------------------------------------------------------
#
# `pytester` is pytest's built-in meta-testing fixture: it runs a fresh
# pytest subprocess against a generated test layout.  We use it to
# drive the harness through its three failure modes (drift, missing
# golden hard-fail, --update-golden write) in isolation from the
# production fixtures.

pytestmark_pytester = pytest.mark.usefixtures("pytester")


@pytest.fixture
def isolated_harness(pytester: pytest.Pytester) -> pytest.Pytester:
    """Lay a minimal copy of the harness + a single trivial fixture
    into a fresh pytest working directory.  Tests drive the
    `--update-golden` flag, drift detection, and missing-golden paths
    against this isolated copy."""
    # Copy the harness verbatim — keep the serialization format, xfail
    # registry, and missing-golden message under a single source of
    # truth.  Re-import not used because pytester's subprocess pytest
    # picks up files by name, not by import.
    here = Path(__file__).parent
    pytester.copy_example  # noqa: B018 — ensures attribute exists
    pytester.makepyfile(
        conftest=(here / "conftest.py").read_text(),
        test_golden=(here / "test_golden.py").read_text(),
    )
    # Trivial fixture: empty .fs program (zero instructions).  The
    # serializer renders "\n" — that's our pinned baseline.
    pytester.makefile(".fs", trivial="")
    pytester.makefile(".world.toml", trivial="[clock]\nipt = 8\n")
    return pytester


def test_update_golden_writes_missing_expected(isolated_harness: pytest.Pytester):
    """With --update-golden and no .expected.mlog, the harness writes
    the actual output and the test passes."""
    expected = isolated_harness.path / "trivial.expected.mlog"
    assert not expected.exists()

    result = isolated_harness.runpytest("--update-golden", "-q")
    result.assert_outcomes(passed=1)

    assert expected.exists()
    assert expected.read_text() == "\n"  # empty program → just newline


def test_drifted_golden_fails_with_unified_diff(isolated_harness: pytest.Pytester):
    """A golden whose content drifts from the compiler's actual output
    fails with a unified-diff payload in the failure message."""
    expected = isolated_harness.path / "trivial.expected.mlog"
    expected.write_text("set s0 999\n")  # deliberately wrong

    result = isolated_harness.runpytest("-q")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*golden mismatch for trivial*"])
    # The unified-diff header is what gives the message its value;
    # pin its shape so future harness refactors can't silently drop it.
    result.stdout.fnmatch_lines(["*trivial.expected.mlog (expected)*"])
    result.stdout.fnmatch_lines(["*trivial.actual.mlog*"])


def test_missing_golden_without_xfail_fails_hard(isolated_harness: pytest.Pytester):
    """A fixture with no .expected.mlog and not listed in
    XFAIL_FIXTURES must FAIL (not skip) with the
    `pytest --update-golden` hint — silent skip would let drift
    accumulate unnoticed."""
    expected = isolated_harness.path / "trivial.expected.mlog"
    assert not expected.exists()

    result = isolated_harness.runpytest("-q")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*missing golden: trivial.expected.mlog*"])
    result.stdout.fnmatch_lines(["*pytest tests/golden --update-golden*"])


def test_missing_sidecar_fails_with_pointer(isolated_harness: pytest.Pytester):
    """A fixture missing its sibling .world.toml fails with a clear
    "missing sidecar" message, NOT a tomllib parse error."""
    (isolated_harness.path / "trivial.world.toml").unlink()

    result = isolated_harness.runpytest("-q")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*missing sidecar: trivial.world.toml*"])
