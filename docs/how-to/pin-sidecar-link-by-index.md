# Pin a sidecar link by index

> **Goal:** bind an mforth link name to a processor's link-slot
> number `N` (the order `getlink` returns it in) instead of the
> in-game block label. You accept fragility to in-game re-link order
> in exchange for stability across rebuilds when that order is
> preserved.
>
> **Prerequisites:**
>
> - mforth installed and on `$PATH` (see [Install](./install.md)).
> - A `.fs` source you want to bind to one or more linked blocks.
> - Knowledge of the in-game link order for your processor — open
>   the processor in Mindustry; the link list is numbered top-to-
>   bottom starting at `0`, matching what the `getlink` instruction
>   returns.

> **When NOT to use this.** Tutorials and most real programs should
> use `target = "<in-game-name>"` instead. `index = N` is fragile:
> unlink + relink the blocks in a different order and your `display`
> handle now points at the switch you used to call `gate`. Reach for
> `index` only when the in-game name is *not* reliably preserved
> across rebuilds — for example, a display block that gets destroyed
> and replaced during play, where Mindustry assigns a fresh
> `message1` / `message2` / … label each time. Re-read the sidecar
> [schema reference](../reference/sidecar-schema.md) for the full
> shape of `[links.X]` before continuing.

## Steps

1. **Pick the slot.** In Mindustry, click the processor and read the
   numbered link list. The slot you want is whatever `getlink N`
   would return — `0` for the first link, `1` for the second, and so
   on. Out-of-range slots resolve to `null` in mlog and `None` in the
   host REPL.

2. **Write the sidecar entry.** In `<name>.world.toml`, declare the
   link with `index = N` instead of `target = "..."`. The left side
   of `=` is still the stable mforth-name your `.fs` source
   references; the `index` only binds it to a concrete slot.

    ```toml
    # display.world.toml — display is processor link slot 0.
    # WARNING: index-mode. If you re-link the processor and slot 0
    # ends up bound to something other than the message block, this
    # script will print to the wrong block. Re-pin (or switch to
    # `target = "..."`) when that happens.
    [links.display]
    type  = "message"
    index = 0
    ```

    `target` and `index` are mutually exclusive — declaring both, or
    neither, is a parser error. See the
    [Troubleshooting](#troubleshooting) section below for the exact
    messages.

3. **Compile and inspect the prologue.** Run `mforth compile` and
   confirm a `getlink <mforth-name> <N>` instruction sits at the top
   of the emitted mlog, ahead of the first user-visible instruction:

    ```bash
    mforth compile display.fs -o display.mlog
    head -3 display.mlog
    ```

    For the sidecar above plus a one-liner `display PRINTFLUSH`,
    you should see:

    ```
    # mforth output — 2 instructions; SOURCE=display.fs; SIDECAR=display.world.toml
    getlink display 0
    printflush display
    ```

    The `getlink display 0` line is the **Mode B prologue**: it
    resolves slot `0` into the `display` variable once, so every
    later use of `display` reads the same in-game block handle. One
    `getlink` per index-bound link, emitted in sidecar-declaration
    order.

4. **Paste and run in Mindustry.** Copy `display.mlog` into the
   processor as usual. The prologue executes once per auto-loop
   iteration; if slot `0` is bound to the block you expected, the
   program behaves exactly as the host REPL did against the
   `index = 0` sidecar.

5. **Re-pin when the in-game link order changes.** If you unlink and
   re-link the blocks, the slot numbers shift. Open the processor,
   note the new order, update `index = N` in the sidecar to match,
   and recompile. There is no in-game indicator that an index has
   drifted — the script will simply talk to the wrong block until
   you re-pin or migrate to `target = "..."`.

## Troubleshooting

- **My script bound to the wrong block.** Re-linking in a different
  order is the usual cause. Open the processor in-game, count down
  the link list to slot `N`, and confirm it points at the block you
  meant. If the in-game block has a stable label, consider switching
  the sidecar to `target = "<in-game-name>"` instead — that mode
  survives re-link order changes.

- **Parser error: `cannot specify both 'target' and 'index' —
  exactly one is required`.** Your `[links.X]` entry has both keys.
  Drop one. If you were migrating from one mode to the other and
  forgot to delete the old line, that is almost certainly what
  happened.

- **Parser error: `requires exactly one of 'target' or 'index'
  (neither was given)`.** Your `[links.X]` entry has `type = "..."`
  but no binding. Add either `target = "..."` (recommended) or
  `index = N` (this how-to).

- **Parser error: `[links.X].index must be an integer`.** TOML
  quoted the value — `index = "0"` is a string, not an int. Drop
  the quotes: `index = 0`.

- **No `getlink` prologue appears in the mlog output.** Confirm the
  compile picked up the sidecar — `mforth compile` looks for
  `<name>.world.toml` next to `<name>.fs` automatically. The mlog
  output header lists `SIDECAR=<path>` when one was loaded; if the
  header says `SIDECAR=<none>`, the file is not where the compiler
  expected it or has a different basename.

## See also

- [Sidecar schema (`.world.toml`)](../reference/sidecar-schema.md)
  — the full reference for `[links.X]`, including every error
  condition the loader raises.
- [Why mforth](../explanation/why-mforth.md) — the REPL ↔ mlog
  equivalence rule the sidecar exists to serve.
- [Use with Helix](./use-with-helix.md) /
  [Use with Neovim](./use-with-nvim.md) — once an index-mode link
  is wired, the LSP resolves `display` (and friends) the same way
  for hover and completion as it does for `target`-mode links.
