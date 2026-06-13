"""Integration tests for `mforth run <path/to/example.fs>` (bead mforth-10t.14).

Contract pinned here (M3 RED contract for this bead):

* The runner loads a ``.fs`` source file plus its sibling ``<basename>.world.toml``
  sidecar (when present), runs lex/parse/resolve/stackcheck, builds a
  :class:`mforth.backend.world.MockWorld` configured from the sidecar, and
  executes ``program.main`` against an :class:`mforth.backend.host.Executor`.
* Top-level execution **auto-loops** — once ``main`` runs to completion the
  runner re-executes it from scratch, mirroring mlog's "fall off the end and
  restart" semantics on a real Mindustry logic processor. The loop is
  interrupted cleanly on ``KeyboardInterrupt`` (SIGINT); the runner reports
  iteration count + simulated tick on the way out.
* ``--no-loop`` bypasses the auto-loop and executes ``main`` exactly once.
  This is the test-friendly mode (auto-loop without ``--no-loop`` would
  hang forever in tests).
* The sidecar's ``[links]`` table seeds ``MockWorld.links`` with
  :class:`mforth.backend.world.Block` instances of the appropriate type.
  Sidecar absence is NOT an error — the runner falls back to an empty
  :class:`mforth.backend.sidecar.WorldConfig`. Sidecar parse errors and
  pipeline errors abort with a ``file:line:col`` message on stderr and
  exit code 1.
* ``--no-loop`` + ``WAIT`` in the program advances ``world.events.tick``,
  proving the runner shares the event-stream seam every other host-side
  subscriber speaks (the REPL ↔ mlog equivalence contract).

Negative cases (M5):

* Missing ``.fs`` file → exit 1, message names the missing path.
* Pipeline error (unresolved word, stack underflow, …) → exit 1 with the
  src_loc-prefixed message on stderr.
* Malformed sidecar → exit 1 with the ``SidecarError`` message.
* ``KeyboardInterrupt`` during the auto-loop → returns exit code 130 (the
  POSIX SIGINT convention) and prints a one-line summary to stderr.

The ``--serve`` flag (web viz) is OUT OF SCOPE for this bead — it lands in
mforth-10t.22, which extends this subcommand's ``configure_parser`` with one
additional flag and adds a viz-server hook in the handler. The shape of
``configure_parser`` is therefore part of the contract pinned here so .22
knows where to splice in.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def loop_once_fs(tmp_path: Path) -> Path:
    """A minimal program: print '1' once, then return (top of main exhausted)."""
    src = tmp_path / "loop_once.fs"
    src.write_text("1 .\n")
    return src


@pytest.fixture
def loop_with_wait_fs(tmp_path: Path) -> Path:
    """A program that calls WAIT once so we can assert tick advance."""
    src = tmp_path / "loop_wait.fs"
    src.write_text("1 . 2 WAIT\n")
    return src


@pytest.fixture
def loop_with_sidecar(tmp_path: Path) -> tuple[Path, Path]:
    """A program + sidecar that binds a `display` message block."""
    src = tmp_path / "sidecar_demo.fs"
    src.write_text(
        '." hi" display PRINTFLUSH\n'
    )
    sidecar = tmp_path / "sidecar_demo.world.toml"
    sidecar.write_text(
        "[links.display]\n"
        'type = "message"\n'
        'target = "message1"\n'
    )
    return src, sidecar


@pytest.fixture
def broken_fs(tmp_path: Path) -> Path:
    """A program with a stack underflow (gate-blocking error)."""
    src = tmp_path / "broken.fs"
    src.write_text("DUP\n")  # underflow: nothing on the stack to dup
    return src


@pytest.fixture
def unresolved_fs(tmp_path: Path) -> Path:
    src = tmp_path / "unresolved.fs"
    src.write_text("nosuchword\n")
    return src


# --------------------------------------------------------------------------
# Runner class — programmatic surface
# --------------------------------------------------------------------------


def test_runner_from_path_runs_pipeline_and_loads_sidecar(loop_with_sidecar):
    """Loading a path builds a Runner with the pipeline result + a MockWorld
    seeded from the sidecar's links table."""
    from mforth.backend.runner import Runner

    src, _ = loop_with_sidecar
    runner = Runner.from_path(src)

    # Pipeline ran — stackcheck result is cached on the runner.
    assert runner.result is not None
    assert runner.result.program is not None

    # Sidecar parsed — link is present on the executor's MockWorld.
    assert "display" in runner.executor.world.links
    assert runner.executor.world.links["display"].type == "message"


def test_runner_from_path_handles_missing_sidecar(loop_once_fs):
    """A .fs without a sibling .world.toml runs with an empty WorldConfig
    (no links). Sidecar absence is not an error."""
    from mforth.backend.runner import Runner

    runner = Runner.from_path(loop_once_fs)
    assert runner.executor.world.links == {}


def test_runner_run_once_executes_main_one_time(loop_once_fs):
    """`run_once()` executes `program.main` exactly one time and returns
    None. Observable: one MessagePrintEvent ('1') on the world stream."""
    from mforth.backend.runner import Runner
    from mforth.backend.world import MessagePrintEvent

    runner = Runner.from_path(loop_once_fs)
    runner.run_once()

    prints = [e for e in runner.executor.world.events if isinstance(e, MessagePrintEvent)]
    assert len(prints) == 1
    assert prints[0].text == "1"


def test_runner_run_once_advances_world_tick_on_wait(loop_with_wait_fs):
    """WAIT inside the program advances the world's simulated tick — proves
    the runner shares the executor → MockWorld → EventStream seam."""
    from mforth.backend.runner import Runner

    runner = Runner.from_path(loop_with_wait_fs)
    assert runner.executor.world.events.tick == 0.0
    runner.run_once()
    assert runner.executor.world.events.tick == pytest.approx(2.0)


def test_runner_run_forever_loops_until_keyboard_interrupt(loop_once_fs):
    """`run_forever()` re-executes `main` repeatedly. We simulate SIGINT by
    raising KeyboardInterrupt from an `on_iteration` callback after the
    third loop. Asserts the loop returns gracefully + reports the iteration
    count it managed to complete."""
    from mforth.backend.runner import Runner
    from mforth.backend.world import MessagePrintEvent

    runner = Runner.from_path(loop_once_fs)

    seen = {"iters": 0}

    def stop_after_three(it: int) -> None:
        seen["iters"] = it
        if it >= 3:
            raise KeyboardInterrupt

    iters = runner.run_forever(on_iteration=stop_after_three)
    assert iters == 3
    # Each of three iterations should have emitted one MessagePrintEvent.
    prints = [e for e in runner.executor.world.events if isinstance(e, MessagePrintEvent)]
    assert len(prints) == 3


# --------------------------------------------------------------------------
# CLI surface — `mforth run`
# --------------------------------------------------------------------------


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Drive `mforth.cli.main` with captured stdout/stderr."""
    from mforth import cli

    out = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(argv)
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0
    return rc, out.getvalue(), err.getvalue()


def test_cli_run_subcommand_is_registered():
    """After `_load_subcommands()` the `run` subcommand is in `_REGISTRY`."""
    from mforth import cli

    cli._load_subcommands()
    assert "run" in cli._REGISTRY


def test_cli_run_help_lists_no_loop_and_source():
    """`mforth run --help` should mention the `source` positional and the
    `--no-loop` flag — pins the parser surface so .22 can extend it."""
    rc, out, err = _run_cli(["run", "--help"])
    # argparse exits 0 for --help
    assert rc == 0
    assert "source" in out
    assert "--no-loop" in out


def test_cli_run_no_loop_executes_program_once(loop_once_fs):
    """`mforth run <file> --no-loop` runs once, returns 0."""
    rc, out, err = _run_cli(["run", str(loop_once_fs), "--no-loop"])
    assert rc == 0, f"stderr={err!r}"


def test_cli_run_missing_file_exits_nonzero(tmp_path):
    """A non-existent .fs path aborts with exit code 1 and a stderr message
    naming the missing path."""
    bogus = tmp_path / "does_not_exist.fs"
    rc, out, err = _run_cli(["run", str(bogus), "--no-loop"])
    assert rc == 1
    assert "does_not_exist.fs" in err


def test_cli_run_stackcheck_error_aborts_with_src_loc(broken_fs):
    """An unhandled stackcheck error aborts with `file:line:col` on stderr
    and a nonzero exit code."""
    rc, out, err = _run_cli(["run", str(broken_fs), "--no-loop"])
    assert rc == 1
    assert ":" in err
    assert str(broken_fs) in err or broken_fs.name in err


def test_cli_run_unresolved_word_aborts_with_src_loc(unresolved_fs):
    rc, out, err = _run_cli(["run", str(unresolved_fs), "--no-loop"])
    assert rc == 1
    assert "nosuchword" in err


def test_cli_run_malformed_sidecar_aborts(tmp_path):
    """A sidecar that violates the schema aborts with the SidecarError msg."""
    src = tmp_path / "bad_sidecar.fs"
    src.write_text("1 .\n")
    sidecar = tmp_path / "bad_sidecar.world.toml"
    # Both target and index specified — schema violation.
    sidecar.write_text(
        "[links.display]\n"
        'type = "message"\n'
        'target = "message1"\n'
        "index = 0\n"
    )
    rc, out, err = _run_cli(["run", str(src), "--no-loop"])
    assert rc == 1
    assert "target" in err or "index" in err


def test_cli_run_blink_example_advances_tick():
    """The bundled examples/blink.fs runs end-to-end under --no-loop. Pins
    that the v1 demo is reachable via `mforth run`."""
    repo_root = Path(__file__).resolve().parents[2]
    blink = repo_root / "examples" / "blink.fs"
    assert blink.exists(), "examples/blink.fs is part of the v1 ship"

    rc, out, err = _run_cli(["run", str(blink), "--no-loop"])
    assert rc == 0, f"stderr={err!r}"


def test_cli_run_counter_example_runs_clean():
    """The bundled examples/counter.fs runs end-to-end under --no-loop. Pins
    that the second v1 demo (pure VARIABLE/@/!/. with no Mindustry blocks)
    is reachable via `mforth run`.

    Per bead mforth-10t.32: examples/counter.fs is the minimal pedagogical
    artifact demonstrating user variables + fetch/store + arithmetic +
    `.` (print top of stack) — no display, no PRINTFLUSH, no WAIT.
    """
    repo_root = Path(__file__).resolve().parents[2]
    counter = repo_root / "examples" / "counter.fs"
    assert counter.exists(), "examples/counter.fs is part of the v1 ship"

    rc, out, err = _run_cli(["run", str(counter), "--no-loop"])
    assert rc == 0, f"stderr={err!r}"


def test_cli_run_counter_sidecar_exists_and_parses():
    """examples/counter.world.toml exists and is a valid sidecar (parses
    cleanly via `mforth.backend.sidecar.load_sidecar`). Pins the
    paired-sidecar convention for every example in v1."""
    from mforth.backend.sidecar import load_sidecar

    repo_root = Path(__file__).resolve().parents[2]
    sidecar = repo_root / "examples" / "counter.world.toml"
    assert sidecar.exists(), "examples/counter.world.toml is part of the v1 ship"

    cfg = load_sidecar(sidecar)
    assert cfg is not None


# --------------------------------------------------------------------------
# Print output reaches stdout (mforth-os2)
# --------------------------------------------------------------------------


def test_cli_run_no_loop_echoes_printed_value_to_stdout(loop_once_fs):
    """Regression for mforth-os2: headless `mforth run --no-loop` must echo
    printed output to stdout. Previously only `--serve` rendered the event
    stream; the headless human surface swallowed it (the existing
    `test_cli_run_no_loop_executes_program_once` only checked rc==0)."""
    rc, out, err = _run_cli(["run", str(loop_once_fs), "--no-loop"])
    assert rc == 0, f"stderr={err!r}"
    assert "1" in out, f"expected printed '1' in stdout, got {out!r}"


# --------------------------------------------------------------------------
# Auto-loop + interrupt — CLI level
# --------------------------------------------------------------------------


def test_cli_run_auto_loop_interruptible_via_runner(loop_once_fs, monkeypatch):
    """When invoked without --no-loop, the CLI handler delegates to
    Runner.run_forever. Simulate SIGINT mid-loop by monkeypatching
    `run_forever` to raise KeyboardInterrupt after a few iterations and
    assert the handler returns 130 (POSIX SIGINT convention)."""
    from mforth.backend import runner as runner_mod

    def fake_run_forever(self, *, on_iteration=None):
        # Simulate the inner loop running 2 iterations before SIGINT.
        for i in range(1, 3):
            self.run_once()
            if on_iteration is not None:
                on_iteration(i)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner_mod.Runner, "run_forever", fake_run_forever)

    rc, out, err = _run_cli(["run", str(loop_once_fs)])
    assert rc == 130
