"""pytest configuration for the golden mlog harness.

Adds the ``--update-golden`` flag.  When set, the harness writes the
actual compiler output to each ``<name>.expected.mlog`` file in place of
the comparison.  Off by default — `pytest tests/golden -q` is a pure
assertion run that fails on any drift.

Workflow for an intentional codegen change:

1. Make the codegen change.
2. Run ``pytest tests/golden`` — the affected fixtures fail with a
   unified diff.
3. Eyeball the diff to confirm the change is intentional.
4. Re-run with ``pytest tests/golden --update-golden`` — the
   ``.expected.mlog`` files are rewritten with the new output.
5. ``git diff tests/golden/*.expected.mlog`` — review the regen, commit.

The flag deliberately does NOT silently skip the assertion when a
golden file is missing; that case is handled separately in
``test_golden.py`` (xfail for fixtures known to depend on unshipped
beads, hard failure with a "run pytest --update-golden" hint otherwise).
"""

from __future__ import annotations

# Enable the built-in `pytester` fixture so tests/golden/test_harness.py
# can drive the harness through its failure modes (drift, missing
# golden, --update-golden write) in an isolated subprocess pytest run.
# Only loaded for the tests/golden subtree — production harness tests
# do not need it.
pytest_plugins = ["pytester"]


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help=(
            "Regenerate golden .mlog files from the compiler's actual "
            "output instead of asserting equality. Use after an "
            "intentional codegen change."
        ),
    )
