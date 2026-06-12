"""Behavior-equivalence across optimization levels (bead mforth-10t.40).

This is the validation gate for the OPTIMIZED levels. The STRICT teaching
equivalence harness (``test_equivalence.py`` + ``test_equivalence_property.py``)
compiles at O0 so the compiled output stays byte/event-identical to the host
REPL — that property is what makes the REPL a faithful teaching surface.

But the optimized levels (``-Ofast`` and up) deliberately ELIDE redundant
``@`` fetches via CSE / LICM, so an optimized program emits FEWER
``VariableReadEvent`` / ``VariableWriteEvent`` instrumentation events than the
REPL. That is a *correct* divergence on the instrumentation channel (project
decision: bead ``mforth-ump``, Option A).

So how do we validate an optimized level didn't break behavior? We compile the
SAME program at O0 and at the optimized level, run BOTH through the in-repo
mlog interpreter, and assert the **SINK** event sequence is identical — where
a *sink* is an observable effect on the world (print, printflush, wait,
control, sensor-read), as opposed to the instrumentation events
(VariableRead / VariableWrite), whose counts are ALLOWED to differ.

If the sink stream is identical, the optimized program does the same
observable thing as the unoptimized one — which is exactly the guarantee an
optimizer must provide.
"""

from __future__ import annotations

import pytest

from mforth.backend.sidecar import WorldConfig, load_sidecar
from mforth.backend.runner import build_world
from mforth.backend.world import (
    LinkResolvedEvent,
    VariableReadEvent,
    VariableWriteEvent,
)
from mforth.dictionary import UserVariable, resolve, standard_dictionary
from mforth.mlog_interp import MlogInterpreter
from mforth.optimize import OptLevel, compile_text
from mforth.parse import SrcLoc, parse


ITERATIONS = 2  # exercise the auto-loop wrap, matching the fixture test.

# Events whose counts are allowed to differ between O0 and an optimized
# level: the instrumentation channel + the Mode B compilation artifact.
_INSTRUMENTATION = (VariableReadEvent, VariableWriteEvent, LinkResolvedEvent)


def _sink_events(events: list) -> list:
    """Keep only observable SINK events — drop instrumentation (variable
    read/write) and the Mode B getlink prologue's LinkResolvedEvent."""
    return [e for e in events if not isinstance(e, _INSTRUMENTATION)]


# ---------------------------------------------------------------------------
# Representative programs. Each pairs source with a Mode A sidecar TOML.
# Chosen to exercise the passes that elide instrumentation:
#   * arith    — pure const arithmetic (fold) + print sink.
#   * var_reuse— a VARIABLE read twice (CSE elides the second fetch -> fewer
#                VariableReadEvents at Ofast, but the print sink is identical).
#   * loop     — a counted loop with a loop-invariant fetch (LICM hoists it).
#   * worddef  — a user word called twice (subroutine candidate for Osize).
# ---------------------------------------------------------------------------

_SIDECAR = '[links.display]\ntype = "message"\ntarget = "message1"\n'

_PROGRAMS = {
    "arith": "2 3 + 4 * PRINT\ndisplay PRINTFLUSH\n",
    "var_reuse": (
        "VARIABLE x\n"
        "7 x !\n"
        "x @ x @ + PRINT\n"
        "display PRINTFLUSH\n"
    ),
    "loop": (
        "VARIABLE k\n"
        "5 k !\n"
        "0 3 DO k @ PRINT LOOP\n"
        "display PRINTFLUSH\n"
    ),
    "worddef": (
        ": double DUP + ;\n"
        "4 double PRINT\n"
        "9 double PRINT\n"
        "display PRINTFLUSH\n"
    ),
}


def _seeded_dictionary():
    d = standard_dictionary()
    d.add_variable(UserVariable(name="display", src_loc=SrcLoc("<test>", 1, 1)))
    return d


def _run_compiled(src: str, level: int, world_config: WorldConfig) -> list:
    """Compile ``src`` at ``level`` and run the result through the in-repo
    mlog interpreter; return the (post-filter) event list.

    The interpreter is fed the source-declared VARIABLE names (excluding the
    sidecar ``display`` link handle) so its VariableRead/Write instrumentation
    matches the host REPL — exactly the machinery test_equivalence._run_mlog
    uses.
    """
    dictionary = _seeded_dictionary()
    # Snapshot sidecar link names BEFORE resolve adds source VARIABLEs.
    sidecar_link_names = {
        e.name
        for e in dictionary._entries.values()  # noqa: SLF001
        if isinstance(e, UserVariable)
    }

    program = parse(src, file="<bench>")
    resolved = resolve(program, dictionary=_seeded_dictionary())
    user_vars = {
        e.name
        for e in resolved._entries.values()  # noqa: SLF001
        if isinstance(e, UserVariable) and e.name not in sidecar_link_names
    }

    mlog_text = compile_text(
        src,
        opt_level=level,
        world_config=world_config,
        dictionary=_seeded_dictionary(),
        source_path="<bench>",
    )

    world = build_world(world_config)
    interp = MlogInterpreter(
        world=world, text=mlog_text, user_variables=user_vars
    )
    interp.run(iterations=ITERATIONS)
    return list(world.events)


def _payload_eq(a, b) -> bool:
    """Compare two events by class + payload, excluding ``timestamp``."""
    from dataclasses import fields, is_dataclass

    if type(a) is not type(b):
        return False
    if not (is_dataclass(a) and is_dataclass(b)):
        return a == b
    for f in fields(a):
        if f.name == "timestamp":
            continue
        if getattr(a, f.name) != getattr(b, f.name):
            return False
    return True


def _sink_streams_equal(events_a: list, events_b: list) -> bool:
    sinks_a = _sink_events(events_a)
    sinks_b = _sink_events(events_b)
    if len(sinks_a) != len(sinks_b):
        return False
    return all(_payload_eq(x, y) for x, y in zip(sinks_a, sinks_b))


@pytest.mark.parametrize("name", sorted(_PROGRAMS))
@pytest.mark.parametrize(
    "level",
    [OptLevel.O1, OptLevel.OFAST, OptLevel.OSIZE],
    ids=["O1", "Ofast", "Osize"],
)
def test_optimized_sink_events_match_O0(name, level):
    """For each representative program, the SINK event stream at an
    optimized level is IDENTICAL to O0 — instrumentation counts may differ,
    observable behavior may not."""
    src = _PROGRAMS[name]
    world_config = _load_sidecar_text(_SIDECAR)

    events_o0 = _run_compiled(src, OptLevel.O0, world_config)
    events_opt = _run_compiled(src, level, world_config)

    assert _sink_streams_equal(events_o0, events_opt), (
        f"sink-event divergence for {name!r} at level {level}:\n"
        f"  O0 sinks  = {_sink_events(events_o0)!r}\n"
        f"  opt sinks = {_sink_events(events_opt)!r}"
    )


def _load_sidecar_text(text: str) -> WorldConfig:
    """Load a WorldConfig from inline TOML text via a temp file (the public
    sidecar loader takes a path)."""
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(
        "w", suffix=".world.toml", delete=False
    ) as fh:
        fh.write(text)
        path = Path(fh.name)
    return load_sidecar(path)


# ---------------------------------------------------------------------------
# Teeth: prove the comparison actually distinguishes optimized output — i.e.
# the sink-equivalence assertion above is NOT vacuously comparing two
# byte-identical compiled programs.
# ---------------------------------------------------------------------------


def _static_instr_count(src: str, level: int) -> int:
    wc = _load_sidecar_text(_SIDECAR)
    text = compile_text(
        src,
        opt_level=level,
        world_config=wc,
        dictionary=_seeded_dictionary(),
        source_path="<bench>",
    )
    return sum(
        1
        for line in text.splitlines()
        if line and not line.lstrip().startswith("#")
    )


def test_ofast_compiles_to_fewer_static_instructions_than_O0():
    """The ``arith`` program const-folds under Ofast, so its compiled output
    has STRICTLY FEWER static instructions than O0. This proves the
    sink-equivalence tests above are comparing GENUINELY DIFFERENT compiled
    programs — if Ofast were a no-op, this would catch it and the
    sink-equivalence guarantee would be vacuous."""
    src = _PROGRAMS["arith"]
    o0 = _static_instr_count(src, OptLevel.O0)
    ofast = _static_instr_count(src, OptLevel.OFAST)
    assert ofast < o0, (
        f"expected Ofast to shrink {src!r} below O0 ({o0}); got {ofast}"
    )
    # ...and yet the observable sink behavior is identical — that is the
    # whole point of the optimizer.
    wc = _load_sidecar_text(_SIDECAR)
    assert _sink_streams_equal(
        _run_compiled(src, OptLevel.O0, wc),
        _run_compiled(src, OptLevel.OFAST, wc),
    )


def test_instrumentation_counts_may_differ_but_sinks_match():
    """Demonstrates the project decision (mforth-ump, Option A): the
    instrumentation channel (VariableRead/Write) is EXEMPT from cross-level
    equivalence — only the sink stream must match. We assert the sink streams
    match for the variable-touching ``var_reuse`` program at Ofast while NOT
    requiring the VariableRead counts to be equal (they may or may not differ
    depending on which redundant fetches a given dialect lowering exposes)."""
    src = _PROGRAMS["var_reuse"]
    wc = _load_sidecar_text(_SIDECAR)
    events_o0 = _run_compiled(src, OptLevel.O0, wc)
    events_ofast = _run_compiled(src, OptLevel.OFAST, wc)

    # Sinks identical — the load-bearing guarantee.
    assert _sink_streams_equal(events_o0, events_ofast)
    # Instrumentation is allowed to differ: Ofast must emit AT MOST as many
    # VariableReadEvents as O0 (an optimizer never ADDS reads).
    reads_o0 = sum(isinstance(e, VariableReadEvent) for e in events_o0)
    reads_ofast = sum(isinstance(e, VariableReadEvent) for e in events_ofast)
    assert reads_ofast <= reads_o0
