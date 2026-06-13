"""Run-file runner — load a ``.fs`` source + sibling ``.world.toml`` sidecar
and execute the program against a :class:`MockWorld`, with mlog's auto-loop
semantics (re-execute top-level until SIGINT).

Bead mforth-10t.14. The CLI subcommand ``mforth run <path>`` wraps this
class via :mod:`mforth.cli_run`; integration tests drive ``Runner``
programmatically so the auto-loop is testable without spawning a
subprocess.

Architecture
============

The runner is a thin orchestrator over the parts that already exist:

1. :func:`mforth.parse.parse` → :class:`Program` (lex + parse combined).
2. :func:`mforth.dictionary.resolve` → :class:`Dictionary` with user
   definitions + ``VARIABLE`` declarations registered.
3. :func:`mforth.stackcheck.stackcheck` → :class:`StackcheckResult`
   (annotated AST + per-word stack effects).
4. :func:`mforth.backend.sidecar.load_sidecar` (optional) →
   :class:`WorldConfig` with the link declarations the program references.
5. :func:`build_world` (this module) → :class:`MockWorld` seeded with
   the sidecar's links + clock config.
6. :class:`mforth.backend.host.Executor` + ``register_all`` (canonical
   primitive table from bead .11/.12).
7. ``Executor.execute(result)`` runs ``program.main`` once.

The auto-loop wraps step 7 in a ``while True`` that catches
:exc:`KeyboardInterrupt`. There is no thread, no signal handler, no
busy-wait. Python's SIGINT delivery between bytecode dispatches is the
only mechanism we rely on — when WAIT runs in ``realtime=true`` mode
it sleeps via :func:`time.sleep`, which is itself a SIGINT yield point;
in ``realtime=false`` (test default) tick advance is instantaneous and
the loop iterations are bounded only by CPU. Tests drive the loop via
an ``on_iteration`` callback that raises ``KeyboardInterrupt`` after a
fixed iteration count, so they never depend on a real signal.

Sidecar resolution
==================

For a given ``<dir>/<name>.fs`` the runner looks for
``<dir>/<name>.world.toml``. If the sibling exists it MUST parse cleanly
— a malformed sidecar aborts (the CLI surface translates the
:class:`SidecarError` into ``exit 1`` + stderr). If the sibling is
absent the runner falls back to an empty :class:`WorldConfig` (no
links, default clock). This matches the contract that several bundled
examples are link-free (a program that only prints + waits doesn't
need a sidecar at all).

``[clock].realtime``
====================

When the sidecar requests ``realtime=true``, ``Executor.world.wait``'s
default behaviour (instantaneous tick advance) is wrapped so that
``WAIT`` also sleeps for the requested real-world seconds via
:func:`time.sleep`. Tests + the headline integration check default to
``realtime=false`` so they don't depend on wall-clock time. Sleep is
done OUTSIDE the executor (the executor's job is to advance the
simulated tick; this module's job is to honour the realtime knob), so
mocking sleep in tests doesn't require monkeypatching the executor.

The ``[clock].ipt`` value is stored on ``world.config["ipt"]`` for
future readers — it doesn't affect host-REPL execution speed (the host
is not instruction-budget-bounded), but it WILL inform the future mlog
backend (bead .15+) when it decides whether inlining a hot word
exceeds the per-processor instruction budget.

Block type → factory
====================

Each :class:`LinkSpec` in the sidecar produces a :class:`Block` via the
matching factory:

* ``message`` → :meth:`Block.message`
* ``memory-cell`` → :meth:`Block.memory_cell` (honours ``size`` from
  the sidecar, default 512)
* ``switch`` → :meth:`Block.switch` (honours ``enabled``, default False)
* ``core`` → :meth:`Block.generic` (no dedicated core factory in v1; the
  core block has no host-side behaviour that differs from generic)
* ``generic`` → :meth:`Block.generic`

The block is added to the world under its ``mforth_name`` (left side of
``[links.<name>]``). The ``target``/``index`` mode distinction is a
sidecar-layer concern (the in-game binding) and is not surfaced to the
host runtime — the runtime only sees the mforth-name and the block
type, which is what every primitive consumes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

from mforth.backend.host import Executor
from mforth.backend.primitives import register_all
from mforth.backend.sidecar import (
    LinkSpec,
    SidecarError,
    WorldConfig,
    load_sidecar,
)
from mforth.backend.world import Block, MockWorld
from mforth.dictionary import UserVariable, resolve, standard_dictionary
from mforth.lex import LexError
from mforth.parse import ParseError, SrcLoc, parse
from mforth.stackcheck import StackcheckResult, stackcheck


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RunnerError(Exception):
    """Raised when the runner cannot start (missing file, bad sidecar, etc.).

    Distinct from :class:`mforth.backend.host.ExecutionError` (runtime
    misbehaviour) and from the pipeline errors (:class:`LexError`,
    :class:`ParseError`, :class:`UnresolvedWordError`, :class:`StackError`),
    which the runner does NOT catch — they propagate to the CLI handler
    which formats them as ``file:line:col: <message>`` + ``exit 1``.
    """


# ---------------------------------------------------------------------------
# Sidecar → MockWorld
# ---------------------------------------------------------------------------


_TYPE_TO_FACTORY: dict[str, Callable[[LinkSpec], Block]] = {
    "message": lambda spec: Block.message(spec.mforth_name),
    "memory-cell": lambda spec: Block.memory_cell(
        spec.mforth_name, size=spec.size if spec.size is not None else 512
    ),
    "switch": lambda spec: Block.switch(
        spec.mforth_name, on=bool(spec.enabled) if spec.enabled is not None else False
    ),
    "core": lambda spec: Block.generic(spec.mforth_name),
    "generic": lambda spec: Block.generic(spec.mforth_name),
}


def build_world(config: WorldConfig) -> MockWorld:
    """Build a fresh :class:`MockWorld` from a :class:`WorldConfig`.

    Each link in ``config.links`` becomes a :class:`Block` of the
    corresponding type and is registered with the world under its
    ``mforth_name``. The clock config (``ipt``, ``realtime``) is stored
    on ``world.config`` so downstream code (e.g. the realtime wait
    wrapper) can consult it without re-reading the sidecar.
    """
    world = MockWorld()
    for spec in config.links:
        factory = _TYPE_TO_FACTORY.get(spec.type)
        if factory is None:
            # _parse_one_link in sidecar.py already validates type — this
            # branch should be unreachable. Defensive raise so future type
            # additions don't silently no-op.
            raise RunnerError(
                f"unknown link type {spec.type!r} for link {spec.mforth_name!r}"
            )
        block = factory(spec)
        # bead mforth-0pg: seed declared sensor/property readings into the
        # block's state. `world.sensor` reads `block.state[<@prop>]`, and
        # the in-repo mlog interpreter's `sensor` handler forwards the
        # same bare `@prop` token to `world.sensor` — so seeding here is
        # the SINGLE point that makes the host `SENSOR` primitive AND the
        # compiled `sensor` instruction return the same value. This is the
        # load-bearing equivalence point: `build_world` is the one world
        # factory both the host runner and the equivalence harness call,
        # so neither backend can drift. Sidecar validation already
        # guarantees keys are SENSOR-readable `@`-names and values are
        # floats, so this is a straight copy.
        for prop, value in spec.sensors.items():
            block.state[prop] = value
        world.add_link(block)
    world.config["ipt"] = config.clock.ipt
    world.config["realtime"] = config.clock.realtime
    return world


def _sidecar_for(source_path: Path) -> Optional[Path]:
    """Return the ``.world.toml`` sibling for ``source_path``, or None if
    no sibling exists. Sidecar absence is not an error."""
    sibling = source_path.with_suffix(".world.toml")
    return sibling if sibling.exists() else None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class Runner:
    """Loaded + ready-to-execute mforth program against a MockWorld.

    Construct via :meth:`from_path` — the dataclass fields are public
    for inspection by tests and downstream tooling (.22 ``--serve``
    will read ``executor.world.events`` to forward to the viz socket).
    """

    source_path: Path
    world_config: WorldConfig
    executor: Executor
    result: StackcheckResult
    iterations: int = 0
    _realtime_orig_wait: Optional[Callable] = field(default=None, repr=False)

    # ---- construction --------------------------------------------------

    @classmethod
    def from_path(cls, source_path: Union[str, Path]) -> "Runner":
        """Load + pipeline a ``.fs`` source file.

        Reads the file, finds its sibling sidecar, runs lex/parse/resolve/
        stackcheck, builds the MockWorld, wires the canonical primitive
        table onto an :class:`Executor`, and returns a ready Runner.

        Raises
        ------
        RunnerError
            If ``source_path`` does not exist.
        SidecarError
            If the sibling ``.world.toml`` exists but is malformed.
        LexError / ParseError / UnresolvedWordError / StackError
            Propagated unchanged — the CLI surface formats them.
        """
        path = Path(source_path)
        if not path.exists():
            raise RunnerError(f"source file not found: {path}")

        text = path.read_text()

        # Sidecar (optional) — load first so its link names can be
        # registered as dictionary entries before resolve/stackcheck run.
        sidecar_path = _sidecar_for(path)
        world_config = (
            load_sidecar(sidecar_path) if sidecar_path is not None else WorldConfig()
        )

        # Pre-seed the dictionary with sidecar-declared link names so the
        # program can refer to them as bare words (e.g. `display
        # PRINTFLUSH`). We model a link name as a `UserVariable` entry:
        # stackcheck treats it as (0, 1) — push address — and the
        # executor's `UserVariable` dispatch pushes the entry's name
        # string, which IS the block handle convention (bare mforth-name;
        # see backend.primitives docstring).
        #
        # This must happen BEFORE `resolve(program, ...)` runs the
        # unresolved-word check; passing a pre-populated dictionary into
        # `resolve` is the documented extension point for exactly this
        # scenario.
        dictionary = standard_dictionary()
        sidecar_src = SrcLoc(
            str(sidecar_path) if sidecar_path is not None else str(path), 1, 1
        )
        for spec in world_config.links:
            # Don't shadow an existing entry (defensive — the only
            # standard-dictionary names that could collide are reserved
            # like PRINT, which a sidecar shouldn't name; we still guard
            # against the future surprise).
            if spec.mforth_name not in dictionary:
                dictionary.add_variable(
                    UserVariable(name=spec.mforth_name, src_loc=sidecar_src)
                )

        # Pipeline (lex + parse combined inside `parse`).
        program = parse(text, file=str(path))
        dictionary = resolve(program, dictionary=dictionary)
        result = stackcheck(program, dictionary=dictionary)

        # MockWorld + executor + canonical primitive table.
        world = build_world(world_config)
        executor = Executor(world=world, dictionary=dictionary)
        register_all(executor)

        runner = cls(
            source_path=path,
            world_config=world_config,
            executor=executor,
            result=result,
        )
        runner._install_realtime_wait_if_requested()
        return runner

    # ---- realtime wrapper ---------------------------------------------

    def _install_realtime_wait_if_requested(self) -> None:
        """If the sidecar requested ``realtime=true``, wrap the world's
        ``wait`` method so it also sleeps for the requested seconds.
        Cooperative scheduling — :func:`time.sleep` is a SIGINT yield
        point, so the auto-loop stays interruptible.
        """
        if not self.world_config.clock.realtime:
            return
        world = self.executor.world
        original = world.wait
        self._realtime_orig_wait = original

        def wait_with_sleep(seconds: float) -> None:
            original(float(seconds))
            try:
                time.sleep(max(0.0, float(seconds)))
            except KeyboardInterrupt:
                # Propagate so the auto-loop can exit cleanly.
                raise

        world.wait = wait_with_sleep  # type: ignore[method-assign]

    # ---- execution ----------------------------------------------------

    def run_once(self) -> None:
        """Execute ``program.main`` exactly once against the persistent
        executor state. Definitions stay in the dictionary across calls
        (since :meth:`Executor.execute` is idempotent for the dictionary
        adoption step), which matches mlog's auto-loop behaviour: the
        word definitions are static; only the execution counter loops.
        """
        self.executor.execute(self.result)
        self.iterations += 1

    def run_forever(
        self,
        *,
        on_iteration: Optional[Callable[[int], None]] = None,
    ) -> int:
        """Execute ``program.main`` repeatedly until interrupted.

        Mirrors mlog's "fall off the end and restart" semantics: when
        the top-level term sequence is exhausted the program restarts
        from the top. On a real Mindustry processor this is the
        engine's auto-loop; in the host REPL it's an explicit
        :meth:`run_once` loop.

        Parameters
        ----------
        on_iteration
            Optional callback fired after every successful iteration,
            receiving the 1-based iteration count. Callbacks may raise
            :exc:`KeyboardInterrupt` to stop the loop early — this is
            the test seam for driving the auto-loop without a real
            signal.

        Returns
        -------
        int
            The total number of completed iterations. Always returned
            (the :exc:`KeyboardInterrupt` is caught here so the caller
            sees a clean return).
        """
        try:
            while True:
                self.run_once()
                if on_iteration is not None:
                    on_iteration(self.iterations)
        except KeyboardInterrupt:
            return self.iterations


__all__ = [
    "Runner",
    "RunnerError",
    "build_world",
]
