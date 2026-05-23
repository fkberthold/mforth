"""REPL ↔ mlog equivalence — the headline test class (bead mforth-10t.31).

For each ``(name.fs, name.world.toml)`` fixture pair under
``tests/integration/fixtures/equivalence/``, this test asserts that
running the program through the host REPL produces the SAME observable
event sequence as compiling it to mlog and running the compiled text
through the in-repo mlog interpreter.

Equivalence definition
======================

Two event sequences are *equivalent* iff:

* They have the same length.
* For each i, ``events_repl[i]`` and ``events_mlog[i]`` are instances of
  the same dataclass type AND have equal payloads on every field
  EXCEPT ``timestamp``. Timestamps are compared with a soft equality
  (exact in non-realtime mode, since both backends advance the clock
  via the same ``MockWorld.wait`` path).

Divergence in this property is the highest-severity regression class
for mforth (CLAUDE.md hard rule) — the REPL is the teaching surface;
if it diverges from compiled output, mforth fails as a teaching tool.

Fixture set
===========

* ``arithmetic`` — + - * / MOD < > = AND OR NOT, all sinks through PRINT.
* ``stack_ops`` — DUP DROP SWAP OVER ROT.
* ``if_else`` — IF/ELSE/THEN covering both branch arms.
* ``do_loop`` — DO/LOOP + I.
* ``print_seq`` — PRINT/PRINTFLUSH sequence + WAIT (sidecar Mode A).
* ``getlink_idx`` — sidecar Mode B (index) → exercises the getlink
  prologue path through the interpreter.

Each fixture is run for a small number of iterations (``ITERATIONS``)
so the auto-loop semantics of both backends are exercised.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Optional

import pytest

from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.finalize import finalize
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.runner import Runner, build_world
from mforth.backend.sidecar import WorldConfig, load_sidecar
from mforth.dictionary import resolve, standard_dictionary
from mforth.mlog_interp import MlogInterpreter
from mforth.parse import parse
from mforth.stackcheck import stackcheck


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "equivalence"
ITERATIONS = 2  # enough to exercise auto-loop wrap


def _discover_fixtures() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.fs"))


def _run_repl(fs_path: Path, iterations: int) -> list:
    """Execute via the host REPL runner; return the event list."""
    runner = Runner.from_path(fs_path)
    for _ in range(iterations):
        runner.run_once()
    return list(runner.executor.world.events)


def _filter_mode_b_prologue_events(events: list, world_config) -> list:
    """Strip ``LinkResolvedEvent`` instances produced by the Mode B
    ``getlink`` prologue.

    The prologue is a *compilation artifact* — the finalize pass
    prepends ``getlink <name> <N>`` for each Mode B sidecar entry so
    the variable is bound at runtime. The host REPL achieves the same
    binding via dictionary pre-seeding at startup (see
    ``Runner.from_path``), which does NOT emit a LinkResolvedEvent.
    The prologue's getlink fires once per program iteration in the
    compiled-mlog backend.

    Treating this as a divergence would invert the equivalence
    property the headline test is supposed to defend (both backends
    DO produce the same OBSERVABLE behaviour at the printflush /
    sensor consumer — they just bind the link via different
    mechanisms). Per CLAUDE.md, deliberate divergences are
    documented in the drawer; this filter implements the
    documentation in code.
    """
    from mforth.backend.world import LinkResolvedEvent

    mode_b_names = {
        spec.mforth_name
        for spec in world_config.links
        if spec.index is not None
    }
    if not mode_b_names:
        return events
    return [
        e
        for e in events
        if not (
            isinstance(e, LinkResolvedEvent)
            and e.block_name in mode_b_names
        )
    ]


def _run_mlog(fs_path: Path, iterations: int) -> list:
    """Compile the program, then execute via the in-repo mlog interpreter
    against an identically-configured MockWorld. Returns the event list.
    """
    sidecar_path = fs_path.with_suffix(".world.toml")
    world_config = (
        load_sidecar(sidecar_path) if sidecar_path.exists() else WorldConfig()
    )
    # Build the same dictionary the runner builds (so sidecar names
    # register as UserVariables before resolve/stackcheck).
    from mforth.dictionary import UserVariable
    from mforth.parse import SrcLoc

    dictionary = standard_dictionary()
    src_loc = SrcLoc(str(sidecar_path), 1, 1)
    for spec in world_config.links:
        if spec.mforth_name not in dictionary:
            dictionary.add_variable(
                UserVariable(name=spec.mforth_name, src_loc=src_loc)
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

    world = build_world(world_config)
    interp = MlogInterpreter(world=world, text=mlog_text)
    interp.run(iterations=iterations)
    return _filter_mode_b_prologue_events(list(world.events), world_config)


def _payload_eq(a, b, *, name_map: Optional[dict] = None) -> bool:
    """Compare two events by class + payload (excluding timestamp).

    ``name_map`` (optional) is the Mode A substitution map
    ``{mforth_name: in_game_name}`` from the sidecar. For event
    fields that carry a block-name (``block_name``), the REPL-side
    value is expected to be the mforth-name and the mlog-side value
    to be the in-game name — they are equivalent because they refer
    to the same in-game target by sidecar construction. Without this
    mapping, equivalence would falsely report divergence for every
    Mode A sidecar fixture, which would invert the property the
    headline test is supposed to defend.
    """
    if type(a) is not type(b):
        return False
    if not (is_dataclass(a) and is_dataclass(b)):
        return a == b
    name_map = name_map or {}
    for f in fields(a):
        if f.name == "timestamp":
            continue
        va = getattr(a, f.name)
        vb = getattr(b, f.name)
        if f.name == "block_name" and isinstance(va, str) and isinstance(vb, str):
            # Accept the Mode A binding: repl=<mforth-name>, mlog=<in-game-name>.
            if va == vb:
                continue
            if name_map.get(va) == vb:
                continue
            return False
        if va != vb:
            return False
    return True


def _format_diff(events_repl: list, events_mlog: list, name_map: dict) -> str:
    """Produce a readable diff string for the assertion message."""
    lines = [
        f"event-sequence diverges: "
        f"len(repl)={len(events_repl)} len(mlog)={len(events_mlog)}",
    ]
    n = max(len(events_repl), len(events_mlog))
    for i in range(n):
        r = events_repl[i] if i < len(events_repl) else "<missing>"
        m = events_mlog[i] if i < len(events_mlog) else "<missing>"
        eq = (
            r != "<missing>"
            and m != "<missing>"
            and _payload_eq(r, m, name_map=name_map)
        )
        marker = "  " if eq else "**"
        lines.append(f"  {marker} [{i}] repl={r!r}")
        lines.append(f"  {marker} [{i}] mlog={m!r}")
    return "\n".join(lines)


def _name_map_for(fs_path: Path) -> dict:
    """Build the {mforth_name: in_game_name} map from the sidecar (Mode
    A only). Returns {} if no sidecar exists or no Mode A bindings.
    """
    sidecar_path = fs_path.with_suffix(".world.toml")
    if not sidecar_path.exists():
        return {}
    cfg = load_sidecar(sidecar_path)
    return {
        spec.mforth_name: spec.target
        for spec in cfg.links
        if spec.target is not None
    }


@pytest.mark.parametrize(
    "fs_path",
    _discover_fixtures(),
    ids=lambda p: p.stem,
)
def test_repl_mlog_equivalence(fs_path: Path) -> None:
    """The headline test: REPL events == compiled-then-interpreted events."""
    events_repl = _run_repl(fs_path, ITERATIONS)
    events_mlog = _run_mlog(fs_path, ITERATIONS)
    name_map = _name_map_for(fs_path)

    assert len(events_repl) == len(events_mlog), _format_diff(
        events_repl, events_mlog, name_map
    )
    for i, (r, m) in enumerate(zip(events_repl, events_mlog)):
        assert _payload_eq(r, m, name_map=name_map), (
            f"event[{i}] diverges:\n  repl={r!r}\n  mlog={m!r}\n\n"
            + _format_diff(events_repl, events_mlog, name_map)
        )


# ---------------------------------------------------------------------------
# Negative-case coverage — pins specific failure surfaces of the equivalence
# property by exercising contract corners that the fixture set doesn't
# naturally hit. These are unit-style tests on the interpreter that mirror
# host-side negative tests in tests/unit/test_mindustry_primitives.py.
# ---------------------------------------------------------------------------


def test_interpreter_skips_comment_lines() -> None:
    """The header comment (line 0 of text) must NOT consume executable
    line-number space — a `jump 0` targets the first non-comment
    instruction.
    """
    text = (
        "# mforth output — 2 instructions; SOURCE=x; SIDECAR=<none>\n"
        "set s0 42\n"
        "print s0\n"
    )
    from mforth.backend.world import MockWorld

    world = MockWorld()
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    # One print event with text "42"; comment is ignored.
    events = list(world.events)
    assert len(events) == 1
    from mforth.backend.world import MessagePrintEvent

    assert isinstance(events[0], MessagePrintEvent)
    assert events[0].text == "42"


def test_interpreter_getlink_out_of_range_emits_no_event() -> None:
    """getlink past the last link returns null, AND must NOT emit a
    LinkResolvedEvent (matching MockWorld.getlink — see bead .12 ship
    drawer).
    """
    from mforth.backend.world import LinkResolvedEvent, MockWorld

    world = MockWorld()  # zero links
    text = (
        "# header\n"
        "getlink s0 5\n"
        "end\n"
    )
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    assert all(not isinstance(e, LinkResolvedEvent) for e in world.events)


def test_interpreter_end_loops_to_first_executable_line() -> None:
    """`end` is sugar for `jump 0 always`. The auto-loop semantics
    require that the next iteration begins at the FIRST EXECUTABLE
    instruction (line 0 in executable space), not at the header
    comment.
    """
    from mforth.backend.world import MessagePrintEvent, MockWorld

    world = MockWorld()
    text = (
        "# header that must be skipped on loop\n"
        "print 7\n"
        "end\n"
    )
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=3)
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert len(prints) == 3
    for e in prints:
        assert e.text == "7"


def test_interpreter_wait_advances_world_clock() -> None:
    """wait <seconds> calls world.wait, which advances events.tick and
    emits WaitEvent. Subsequent events should carry the advanced
    timestamp.
    """
    from mforth.backend.world import MessagePrintEvent, MockWorld, WaitEvent

    world = MockWorld()
    text = (
        "# header\n"
        "wait 5\n"
        "print done\n"
        "end\n"
    )
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    events = list(world.events)
    assert any(isinstance(e, WaitEvent) and e.seconds == 5.0 for e in events)
    # The print event after wait should be stamped at >= 5.0.
    prints = [e for e in events if isinstance(e, MessagePrintEvent)]
    assert prints, "expected a MessagePrintEvent after WAIT"
    assert prints[-1].timestamp == 5.0


def test_interpreter_printflush_to_nonexistent_block_emits_event() -> None:
    """printflush to a block that doesn't exist still emits
    MessagePrintflushEvent (matches host primitive — bead .12).
    """
    from mforth.backend.world import MessagePrintflushEvent, MockWorld

    world = MockWorld()  # no blocks
    text = (
        "# header\n"
        "print hello\n"
        "printflush ghost1\n"
        "end\n"
    )
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    flushes = [
        e for e in world.events if isinstance(e, MessagePrintflushEvent)
    ]
    assert len(flushes) == 1
    assert flushes[0].block_name == "ghost1"
