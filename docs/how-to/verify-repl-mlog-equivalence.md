# Verify REPL ↔ mlog equivalence

> **Goal:** confirm a `.fs` snippet produces the same event sequence
> on the host REPL and via the in-repo mlog interpreter — the
> property that makes mforth a teaching compile target.
>
> **Prerequisites:**
>
> - mforth installed from a checkout (`pip install -e ".[dev]"` —
>   see [Install](install.md)).
> - A `.fs` snippet plus a matching `.world.toml` sidecar (an empty
>   sidecar is fine for sidecar-free programs; arithmetic-only
>   snippets don't need any links).
> - `pytest` on `$PATH` if you want to use the existing fixture
>   harness instead of driving the two backends by hand.

The rationale for *why* this property is non-negotiable (and the
three convergence decisions that pin it — `/` as float division,
variable-event instrumentation, integer-valued PRINT formatting) is
covered in the project's [`CLAUDE.md`](https://github.com/fkberthold/mforth/blob/main/CLAUDE.md)
under "REPL ↔ mlog convergence decisions". This page is the recipe;
read that section first if you want the *why*.

## Steps

1. **Pick or write your snippet.** Save the source and its sidecar
   side-by-side, sharing a stem:

    ```bash
    cat > /tmp/mysnippet.fs <<'EOF'
    VARIABLE counter
    3 counter !
    counter @ 1 + counter !
    counter @ PRINT
    display PRINTFLUSH
    EOF

    cat > /tmp/mysnippet.world.toml <<'EOF'
    [links.display]
    type   = "message"
    target = "message1"

    [clock]
    realtime = false
    EOF
    ```

    The sidecar's `[links.<name>]` entry binds the mforth-name your
    source references (`display`) to an in-game block. See
    [Sidecar schema](../reference/sidecar-schema.md) for the full
    grammar.

2. **Run the snippet through the host REPL with event capture.**

    ```python
    from pathlib import Path
    from mforth.backend.runner import Runner

    runner = Runner.from_path(Path("/tmp/mysnippet.fs"))
    runner.run_once()                       # one auto-loop iteration
    events_repl = list(runner.executor.world.events)
    ```

    `Runner.from_path` runs the full pipeline (lex → parse → resolve
    → stackcheck), seeds a `MockWorld` from the sidecar, registers
    the canonical primitive table, and returns a runner ready to
    execute. `run_once()` runs `program.main` exactly once;
    `runner.executor.world.events` is the captured event stream.

3. **Compile the snippet to mlog.**

    ```bash
    mforth compile /tmp/mysnippet.fs -o /tmp/mysnippet.mlog
    ```

    The emitted text is paste-ready mlog. Inspect it if you like —
    each line maps to one of the instructions catalogued in the
    [mlog lowering reference](../reference/mlog-lowering.md).

4. **Run the compiled mlog through the in-repo interpreter with
   event capture.** The interpreter takes the same `MockWorld` shape
   the REPL uses, plus the set of source-declared `VARIABLE` names
   so reads and writes of those names emit
   `VariableReadEvent` / `VariableWriteEvent` (sidecar-pre-seeded
   block handles like `display` are deliberately excluded — they're
   not user variables, and the REPL doesn't instrument them either):

    ```python
    from pathlib import Path
    from mforth.backend.mlog.emit import emit
    from mforth.backend.mlog.finalize import finalize
    from mforth.backend.mlog.slots import allocate_slots
    from mforth.backend.runner import build_world
    from mforth.backend.sidecar import WorldConfig, load_sidecar
    from mforth.dictionary import UserVariable, resolve, standard_dictionary
    from mforth.mlog_interp import MlogInterpreter
    from mforth.parse import SrcLoc, parse
    from mforth.stackcheck import stackcheck

    fs_path = Path("/tmp/mysnippet.fs")
    sidecar_path = fs_path.with_suffix(".world.toml")
    cfg = load_sidecar(sidecar_path) if sidecar_path.exists() else WorldConfig()

    # Pre-seed sidecar link names as UserVariable entries (matching
    # the runner's pipeline). Capture them BEFORE resolve, so the
    # interpreter can distinguish block handles from real VARIABLEs.
    dictionary = standard_dictionary()
    loc = SrcLoc(str(sidecar_path), 1, 1)
    for spec in cfg.links:
        if spec.mforth_name not in dictionary:
            dictionary.add_variable(UserVariable(name=spec.mforth_name, src_loc=loc))
    sidecar_link_names = {
        e.name for e in dictionary._entries.values()
        if isinstance(e, UserVariable)
    }

    program = parse(fs_path.read_text(), file=str(fs_path))
    dictionary = resolve(program, dictionary=dictionary)
    result = stackcheck(program, dictionary=dictionary)
    slots = allocate_slots(result)
    instrs = emit(result, slots)
    mlog_text = finalize(
        instrs, world_config=cfg, source_path=fs_path,
        sidecar_path=sidecar_path if sidecar_path.exists() else None,
    )

    user_vars = {
        e.name for e in dictionary._entries.values()
        if isinstance(e, UserVariable) and e.name not in sidecar_link_names
    }

    world = build_world(cfg)
    interp = MlogInterpreter(world=world, text=mlog_text, user_variables=user_vars)
    interp.run(iterations=1)
    events_mlog = list(world.events)
    ```

5. **Diff the two event sequences.** They should be identical
   modulo `timestamp` (clock advances are pinned by `MockWorld.wait`
   on both sides, so timestamps match exactly in non-realtime mode):

    ```python
    from dataclasses import fields, is_dataclass

    def payload_eq(a, b):
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

    assert len(events_repl) == len(events_mlog), (
        f"event count diverges: repl={len(events_repl)} mlog={len(events_mlog)}"
    )
    for i, (r, m) in enumerate(zip(events_repl, events_mlog)):
        assert payload_eq(r, m), f"event[{i}] diverges:\n  repl={r!r}\n  mlog={m!r}"
    ```

    The event dataclasses themselves are catalogued in
    [Reference → Events](../reference/events.md).

6. **(Recommended) Use the existing pytest harness instead.** The
   five-line manual diff above is exactly what
   `tests/integration/test_equivalence.py` already runs, with a
   richer payload comparator (Mode A `target` substitution, Mode B
   `getlink` prologue filtering). To enroll your snippet:

    ```bash
    cp /tmp/mysnippet.fs        tests/integration/fixtures/equivalence/
    cp /tmp/mysnippet.world.toml tests/integration/fixtures/equivalence/
    pytest tests/integration/test_equivalence.py -k mysnippet -v
    ```

    The test is parameterised by fixture discovery — dropping the
    pair into `fixtures/equivalence/` is the entire wiring. A
    passing run is the canonical proof of equivalence.

## Troubleshooting

- **`/` produces `.0`-suffixed text in the REPL but an integer in
  mlog (or vice versa).** This was the symptom that bead
  `mforth-dlr` fixed: `/` now emits `op div` (float division) on
  both surfaces. If you see a fresh case, file a regression — the
  convergence is pinned by
  `tests/integration/fixtures/equivalence/divide_float.fs`.

- **A numeric `PRINT` shows `1.0` on one side and `1` on the
  other.** Integer-valued floats render WITHOUT the trailing `.0`
  on both surfaces (bead `mforth-05h`, matching the in-game `print`
  instruction's stringification rule). If you see drift, the host
  primitive in `src/mforth/backend/primitives.py` (`_print`) and
  the interpreter's `_format_for_print` are the convergence point.

- **`VariableReadEvent` / `VariableWriteEvent` appear for a sidecar
  link name (e.g. `display`).** That's a bug in your harness:
  sidecar-pre-seeded link names are block handles, NOT user
  variables, and the REPL never instruments them. Make sure your
  `user_variables` set excludes anything that came from
  `cfg.links` (the `sidecar_link_names` snapshot in step 4 is
  exactly this filter — bead `mforth-0qi`).

- **Event counts differ but every shared prefix matches.** Look
  for a Mode B (`index = N`) link in your sidecar. The compiled
  backend prepends one `getlink <name> <N>` per Mode B link as a
  runtime prologue, which fires a `LinkResolvedEvent` the REPL
  doesn't emit (the REPL binds links at startup via dictionary
  pre-seeding). `test_equivalence.py::_filter_mode_b_prologue_events`
  documents the filter; mirror it in your own harness if you want
  the events to line up.

- **`event[i] diverges` on `block_name`.** Mode A sidecars bind
  `mforth-name → target = "<in-game-name>"`. The REPL events
  carry the mforth-name; the compiled mlog binds via `target`, so
  its events carry the in-game name. Both refer to the same block.
  `test_equivalence.py::_payload_eq` accepts either form via the
  sidecar's `{mforth_name: target}` map — copy that pattern.

## What to read next

- [Reference → Events](../reference/events.md) — every event
  dataclass, its fields, and when each backend emits it.
- [Reference → mlog lowering](../reference/mlog-lowering.md) — how
  each Forth construct compiles into mlog instructions, so you can
  read the `mforth compile` output.
- [Sidecar schema](../reference/sidecar-schema.md) — the full
  `.world.toml` grammar, including the Mode A / Mode B link
  binding tradeoff.
