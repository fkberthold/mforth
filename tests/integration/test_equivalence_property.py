"""Generative REPL <-> mlog equivalence harness (bead mforth-2p8).

This is the *generative* counterpart to the fixture-based headline test in
``tests/integration/test_equivalence.py``. Where that file pins the property
with a handful of hand-written ``(name.fs, name.world.toml)`` pairs, this
file randomly generates WELL-FORMED v1 mforth programs (via the Hypothesis
strategy in ``tests/integration/_gen.py``) and asserts that EACH one produces
an identical observable event sequence whether:

1. run through the host REPL (``Runner`` -> MockWorld -> EventStream), or
2. compiled to mlog (``backend.mlog`` pipeline -> ``finalize``) and executed
   through the in-repo mlog interpreter (``mforth.mlog_interp``).

A divergence is the highest-severity regression class for mforth (CLAUDE.md
hard rule): the REPL is the teaching surface, and if compiled output drifts
from it, mforth has failed as a teaching tool. This harness is the
load-bearing safety net for the v2 optimization tier (mforth-10t.33-.40) —
any pass that transforms emitted mlog must keep this property GREEN.

Reuse, not duplication
======================

The per-program work (run REPL, compile, interpret, compare event streams)
is exactly what the fixture test already does. We import its ``_run_repl``,
``_run_mlog``, ``_payload_eq``, ``_format_diff``, and ``_name_map_for``
helpers verbatim and drive them from generated temp-file programs rather than
checked-in fixtures.

Teeth
=====

A clearly-marked meta-test (``test_property_detects_injected_divergence``)
monkeypatches the mlog emitter so Forth ``/`` lowers to ``op idiv`` (integer
division) instead of the convergence-correct ``op div`` (float). It then runs
a program that divides and asserts the equivalence comparison DETECTS the
resulting drift. This proves the harness has teeth: if the comparison were a
no-op, the meta-test would (wrongly) see no divergence and fail. The suite
stays GREEN because the meta-test PASSES precisely when the divergence is
caught.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, seed, settings

from tests.integration._gen import SIDECAR_TOML, mforth_program


# ---------------------------------------------------------------------------
# Reuse the fixture-test's proven per-program machinery rather than
# re-implementing it. The module isn't a package-importable name (tests/ has
# no __init__), so load it by path and pull the helpers off it.
# ---------------------------------------------------------------------------
_EQ_PATH = Path(__file__).parent / "test_equivalence.py"
_spec = importlib.util.spec_from_file_location("_equivalence_helpers", _EQ_PATH)
assert _spec is not None and _spec.loader is not None
_eq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_eq)

_run_repl = _eq._run_repl
_run_mlog = _eq._run_mlog
_payload_eq = _eq._payload_eq
_format_diff = _eq._format_diff
_name_map_for = _eq._name_map_for


ITERATIONS = 2  # exercise the auto-loop wrap, matching the fixture test.


def _materialize(source: str, tmp_path: Path) -> Path:
    """Write a generated program + its Mode A sidecar to ``tmp_path`` and
    return the ``.fs`` path (the runner / compiler find the sibling sidecar
    by suffix).
    """
    fs = tmp_path / "generated.fs"
    fs.write_text(source)
    fs.with_suffix(".world.toml").write_text(SIDECAR_TOML)
    return fs


def _events_diverge(events_repl: list, events_mlog: list, name_map: dict) -> bool:
    """True iff the two event sequences are NOT equivalent (length or any
    payload differs, timestamp excluded). Mirrors the assertion logic in the
    fixture test's ``test_repl_mlog_equivalence``.
    """
    if len(events_repl) != len(events_mlog):
        return True
    return any(
        not _payload_eq(r, m, name_map=name_map)
        for r, m in zip(events_repl, events_mlog)
    )


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


@settings(
    max_examples=120,
    deadline=None,  # compile+interpret per example varies; don't flake on time.
    suppress_health_check=[HealthCheck.too_slow],
)
@given(source=mforth_program())
def test_generated_program_repl_mlog_equivalence(
    source: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A randomly-generated well-formed v1 program produces identical event
    streams via the host REPL and via compiled-then-interpreted mlog.

    On divergence, Hypothesis shrinks ``source`` toward a minimal failing
    program and the assertion message embeds the full event diff.
    """
    tmp_path = tmp_path_factory.mktemp("genprog")
    fs = _materialize(source, tmp_path)

    events_repl = _run_repl(fs, ITERATIONS)
    events_mlog = _run_mlog(fs, ITERATIONS)
    name_map = _name_map_for(fs)

    assert not _events_diverge(events_repl, events_mlog, name_map), (
        "REPL <-> mlog event-stream divergence on generated program:\n"
        + source
        + "\n"
        + _format_diff(events_repl, events_mlog, name_map)
    )


# ---------------------------------------------------------------------------
# Teeth — prove the harness actually catches a divergence.
# ---------------------------------------------------------------------------


def test_zero_trip_do_loop_equivalence(tmp_path: Path) -> None:
    """REGRESSION (found by the generative harness, shrunk by Hypothesis to
    ``0 0 DO 0 PRINT I PRINT LOOP``): a ``limit start DO`` loop with
    ``start >= limit`` must run its body ZERO times on BOTH backends.
    This is the deterministic pin for the ``_emit_do_loop`` test-at-top fix.

    The host REPL (``backend/host.py``) tests the bound at the TOP of the
    loop (``while: if idx >= limit: break``), so ``0 0 DO 7 PRINT LOOP``
    prints nothing. The mlog ``_emit_do_loop`` previously emitted a
    test-at-BOTTOM (do-while) loop that ran the body once regardless,
    diverging from the REPL — the highest-severity regression class
    (CLAUDE.md headline property). This pins the fix: a zero-trip loop must
    produce identical (body-skipped) event streams on both backends.
    """
    source = "0 0 DO 7 PRINT LOOP\n9 PRINT\ndisplay PRINTFLUSH\n"
    fs = _materialize(source, tmp_path)
    name_map = _name_map_for(fs)

    events_repl = _run_repl(fs, 1)
    events_mlog = _run_mlog(fs, 1)

    assert not _events_diverge(events_repl, events_mlog, name_map), (
        "zero-trip DO/LOOP diverges (mlog ran the body; host did not):\n"
        + _format_diff(events_repl, events_mlog, name_map)
    )


def test_property_detects_injected_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """META-TEST: inject a known divergence into the mlog backend and assert
    the equivalence comparison flags it.

    We monkeypatch the emitter's binary-op map so Forth ``/`` lowers to mlog
    ``op idiv`` (integer division) instead of the convergence-correct
    ``op div`` (float division — CLAUDE.md / bead mforth-dlr). A program that
    computes ``5 2 /`` then prints: the REPL yields ``2.5`` (text "2.5") and
    the broken mlog yields integer ``2`` (text "2"). The event streams MUST
    diverge — and this test asserts the harness sees that.

    This is the load-bearing proof that a passing property run means
    something. If the comparison had no teeth, ``_events_diverge`` would
    return False here and this test would fail. The suite stays GREEN because
    the test PASSES exactly when the injected divergence is detected.
    """
    import importlib

    # NB: ``from mforth.backend.mlog import emit`` binds the *function*
    # ``emit`` (re-exported on the package), not the submodule. Reach the
    # module object explicitly so we can patch its op map.
    emit_mod = importlib.import_module("mforth.backend.mlog.emit")

    # Sanity: confirm the convergence-correct mapping is what we're breaking.
    assert emit_mod._BINARY_OP_MAP["/"] == "div"

    # First establish the program is equivalent UNDER the correct mapping,
    # so the meta-test can't pass for a spurious reason.
    source = '5 2 / PRINT\ndisplay PRINTFLUSH\n'
    fs = _materialize(source, tmp_path)
    name_map = _name_map_for(fs)

    events_repl = _run_repl(fs, 1)
    events_mlog_ok = _run_mlog(fs, 1)
    assert not _events_diverge(events_repl, events_mlog_ok, name_map), (
        "baseline program should be equivalent before injecting the bug"
    )

    # Now inject the divergence: `/` -> idiv. monkeypatch auto-reverts.
    patched = dict(emit_mod._BINARY_OP_MAP)
    patched["/"] = "idiv"
    monkeypatch.setattr(emit_mod, "_BINARY_OP_MAP", patched)

    events_mlog_broken = _run_mlog(fs, 1)

    assert _events_diverge(events_repl, events_mlog_broken, name_map), (
        "the harness FAILED TO DETECT an injected `/`->idiv divergence — "
        "the equivalence comparison has no teeth.\n"
        + _format_diff(events_repl, events_mlog_broken, name_map)
    )


_REQUIRED_CONSTRUCTS = [
    "DUP", "DROP", "SWAP", "OVER", "ROT",   # stack ops
    "+", "-", "*", "/",                       # arithmetic
    "<", ">", "=",                            # comparisons
    "IF", "ELSE", "THEN",                     # conditional
    "BEGIN", "UNTIL",                          # converging loop
    "DO", "LOOP", "I",                        # counted loop
    "VARIABLE", "@", "!",                     # variables
    "PRINT", ".", 'S"',                        # IO sinks (`.` landed by va2)
]


def test_generator_emits_full_primitive_subset() -> None:
    """Coverage guard: across a Hypothesis sweep the generator must exercise
    every primitive/construct in the mforth-2p8 spec subset at least once,
    INCLUDING the ``.`` pop-print word (its mlog emit landed in bead va2).

    Without this, a future edit could silently narrow the generator (e.g.
    stop emitting BEGIN/UNTIL, or drop ``.``) and the property would still
    pass while covering less. We accumulate the token set seen across a
    ``@given`` sweep (the Hypothesis-idiomatic way — no ``.example()``),
    then assert coverage once the sweep completes.
    """
    seen: set[str] = set()

    # Pinned seed + no example DB => the coverage sweep is DETERMINISTIC.
    # Without this the random sweep intermittently missed a rare construct
    # (e.g. '<') and false-failed this guard — a flaky merge/CI gate.
    @seed(20260611)
    @settings(max_examples=600, deadline=None, database=None)
    @given(source=mforth_program())
    def _collect(source: str) -> None:
        for line in source.splitlines():
            for tok in line.split():
                seen.add(tok)

    _collect()

    missing = [tok for tok in _REQUIRED_CONSTRUCTS if tok not in seen]
    assert not missing, (
        f"generator never emitted these required constructs across the "
        f"sweep: {missing}"
    )
