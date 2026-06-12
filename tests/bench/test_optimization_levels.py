"""Optimization-level benchmark harness (bead mforth-10t.40).

Compiles each fixture in ``bench_programs/`` at every optimization level
(``-O0`` / ``-O1`` / ``-Ofast`` / ``-Osize``) and reports three metrics per
``(fixture, level)``:

* **static** — instruction lines emitted (the program's footprint in a
  Mindustry processor; smaller fits more logic per processor).
* **dynamic** — instruction *dispatches per tick* under the in-repo mlog
  interpreter (``MlogInterpreter.executed_steps`` over one pass). This is the
  ``fast > small`` metric: fewer dispatches per tick = more headroom under
  ``@ipt``.
* **slots** — distinct ``s<N>`` stack-slot variables referenced (the
  register pressure the slot allocator + dead-copy reuse produce).

The numbers are printed as a Markdown table (visible with ``pytest -s``) and
the same renderer produces ``docs/reference/optimization-levels.md`` via the
module-level :func:`render_table` so the committed doc and the live harness
never drift.

Assertions (kept to invariants that hold for THIS dialect — see the docstring
on :func:`test_ofast_never_worse_than_o0`):

* ``-Ofast`` static instruction count is ``<=`` ``-O0`` for every fixture.
* ``-Ofast`` dynamic instructions/tick is ``<=`` ``-O0`` for every fixture.
* The SINK event stream is identical across all levels (behavior preserved) —
  the optimizer may shrink, never change observable output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from mforth.backend.runner import build_world
from mforth.backend.sidecar import WorldConfig, load_sidecar
from mforth.backend.world import (
    LinkResolvedEvent,
    VariableReadEvent,
    VariableWriteEvent,
)
from mforth.dictionary import UserVariable, resolve, standard_dictionary
from mforth.mlog_interp import MlogInterpreter
from mforth.optimize import OptLevel, compile_text, level_name
from mforth.parse import parse


BENCH_DIR = Path(__file__).parent / "bench_programs"
LEVELS = [OptLevel.O0, OptLevel.O1, OptLevel.OFAST, OptLevel.OSIZE]
ITERATIONS = 1  # one pass = one tick for the dynamic metric.

# Instrumentation + Mode-B artifact events are NOT part of observable
# behavior — see test_optimized_equivalence.py and bead mforth-ump.
_NON_SINK = (VariableReadEvent, VariableWriteEvent, LinkResolvedEvent)


def _discover_fixtures() -> list[Path]:
    return sorted(BENCH_DIR.glob("*.fs"))


def _seeded_dictionary(world_config: WorldConfig):
    """Standard dictionary pre-seeded with the sidecar link names, exactly as
    the runner / cli_compile do."""
    from mforth.parse import SrcLoc

    d = standard_dictionary()
    loc = SrcLoc("<bench>", 1, 1)
    for spec in world_config.links:
        if spec.mforth_name not in d:
            d.add_variable(UserVariable(name=spec.mforth_name, src_loc=loc))
    return d


def _user_vars(src: str, world_config: WorldConfig) -> set:
    """Source-declared VARIABLE names (excluding sidecar link handles) — the
    set the interpreter instruments for VariableRead/Write events."""
    d = _seeded_dictionary(world_config)
    sidecar_names = {
        e.name for e in d._entries.values()  # noqa: SLF001
        if isinstance(e, UserVariable)
    }
    program = parse(src, file="<bench>")
    resolved = resolve(program, dictionary=d)
    return {
        e.name for e in resolved._entries.values()  # noqa: SLF001
        if isinstance(e, UserVariable) and e.name not in sidecar_names
    }


@dataclass(frozen=True)
class BenchRow:
    fixture: str
    level: int
    static: int
    dynamic: int
    slots: int
    sink_events: tuple


def _static_count(mlog_text: str) -> int:
    return sum(
        1
        for line in mlog_text.splitlines()
        if line and not line.lstrip().startswith("#")
    )


def _slot_count(mlog_text: str) -> int:
    """Distinct ``s<N>`` stack-slot variables referenced in the output."""
    slots: set[str] = set()
    for line in mlog_text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        for tok in line.split():
            if len(tok) >= 2 and tok[0] == "s" and tok[1:].isdigit():
                slots.add(tok)
    return len(slots)


def _measure(fs_path: Path, level: int) -> BenchRow:
    src = fs_path.read_text()
    sidecar = fs_path.with_suffix(".world.toml")
    world_config = load_sidecar(sidecar) if sidecar.exists() else WorldConfig()

    mlog_text = compile_text(
        src,
        opt_level=level,
        world_config=world_config,
        dictionary=_seeded_dictionary(world_config),
        source_path=fs_path,
        sidecar_path=sidecar if sidecar.exists() else None,
    )

    world = build_world(world_config)
    interp = MlogInterpreter(
        world=world,
        text=mlog_text,
        user_variables=_user_vars(src, world_config),
    )
    interp.run(iterations=ITERATIONS)

    sinks = tuple(
        _event_key(e) for e in world.events if not isinstance(e, _NON_SINK)
    )
    return BenchRow(
        fixture=fs_path.stem,
        level=level,
        static=_static_count(mlog_text),
        dynamic=interp.executed_steps,
        slots=_slot_count(mlog_text),
        sink_events=sinks,
    )


def _event_key(e) -> tuple:
    """A hashable, timestamp-free identity for a sink event so sink streams
    can be compared across levels."""
    from dataclasses import fields, is_dataclass

    if not is_dataclass(e):
        return (type(e).__name__, repr(e))
    payload = tuple(
        (f.name, getattr(e, f.name))
        for f in fields(e)
        if f.name != "timestamp"
    )
    return (type(e).__name__, payload)


# ---------------------------------------------------------------------------
# Table rendering (shared by the -s console dump and the doc generator).
# ---------------------------------------------------------------------------


def collect_rows() -> list[BenchRow]:
    rows: list[BenchRow] = []
    for fs_path in _discover_fixtures():
        for level in LEVELS:
            rows.append(_measure(fs_path, level))
    return rows


def render_table(rows: list[BenchRow]) -> str:
    """Render the benchmark rows as a GitHub-flavored Markdown table."""
    header = (
        "| Fixture | Level | Static instrs | Dynamic instrs/tick | Slots |\n"
        "| --- | --- | ---: | ---: | ---: |"
    )
    lines = [header]
    for r in rows:
        lines.append(
            f"| {r.fixture} | {level_name(r.level)} | {r.static} "
            f"| {r.dynamic} | {r.slots} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The benchmark test.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bench_rows() -> list[BenchRow]:
    return collect_rows()


def test_benchmark_table_prints(bench_rows, capsys):
    """Emit the benchmark table to the captured stdout (visible with
    ``pytest -s``). This is the human-readable ground-truth artifact the
    bead asks for; the same renderer feeds docs/reference."""
    table = render_table(bench_rows)
    with capsys.disabled():
        print("\n" + table + "\n")
    assert table.count("\n") >= len(bench_rows)  # header + one row each


def _by_fixture(rows: list[BenchRow]) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r.fixture, {})[r.level] = r
    return out


def test_ofast_static_not_worse_than_o0(bench_rows):
    """``-Ofast`` must never emit MORE static instructions than ``-O0`` for
    any fixture. (It folds / elides; it never bloats.)"""
    for fixture, by_level in _by_fixture(bench_rows).items():
        o0 = by_level[OptLevel.O0].static
        ofast = by_level[OptLevel.OFAST].static
        assert ofast <= o0, (
            f"{fixture}: -Ofast static ({ofast}) > -O0 ({o0})"
        )


def test_ofast_dynamic_not_worse_than_o0(bench_rows):
    """``-Ofast`` must never execute MORE instructions per tick than ``-O0``.
    This is the ``fast > small`` headline metric — the optimizer's whole
    reason for existing. (The bead's aspirational target is a >=40% win on
    arithmetic-heavy code; we assert the SAFE invariant `<=` here and report
    the actual per-fixture delta in the printed table, so a regression that
    makes optimized code SLOWER fails the gate without making the suite
    flaky on fixtures where the dialect lowering leaves little to remove.)"""
    for fixture, by_level in _by_fixture(bench_rows).items():
        o0 = by_level[OptLevel.O0].dynamic
        ofast = by_level[OptLevel.OFAST].dynamic
        assert ofast <= o0, (
            f"{fixture}: -Ofast dynamic/tick ({ofast}) > -O0 ({o0})"
        )


def test_arith_heavy_ofast_beats_o0_by_40pct(bench_rows):
    """The arithmetic-heavy fixture is the case the bead's >=40% target was
    written for: const-folding collapses a long constant chain, so -Ofast's
    dynamic instructions/tick should be DRAMATICALLY lower than -O0's. We
    pin the bead's >=40% acceptance bar on this fixture specifically (the one
    where it is achievable), rather than asserting it universally."""
    by_level = _by_fixture(bench_rows)["arith_heavy"]
    o0 = by_level[OptLevel.O0].dynamic
    ofast = by_level[OptLevel.OFAST].dynamic
    assert o0 > 0
    reduction = (o0 - ofast) / o0
    assert reduction >= 0.40, (
        f"arith_heavy: -Ofast only reduced dynamic instrs/tick by "
        f"{reduction:.0%} (o0={o0}, ofast={ofast}); expected >=40%"
    )


def test_sinks_identical_across_all_levels(bench_rows):
    """Behavior preservation: the SINK event stream is identical at every
    level for every fixture. Optimization changes the instruction stream,
    never the observable output."""
    for fixture, by_level in _by_fixture(bench_rows).items():
        baseline = by_level[OptLevel.O0].sink_events
        for level, row in by_level.items():
            assert row.sink_events == baseline, (
                f"{fixture}: sink events at {level_name(level)} diverge "
                f"from -O0\n  O0={baseline!r}\n  {level_name(level)}="
                f"{row.sink_events!r}"
            )
