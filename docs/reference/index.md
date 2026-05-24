# Reference

The austere catalogue of what the project ships. Pages list exact
paths, signatures, flags, and behaviour. They are consulted, not read
sequentially.

> **What reference is not.** It does not teach, instruct, or argue.
> Recipes belong in [How-to](../how-to/index.md). Rationale belongs
> in [Explanation](../explanation/index.md). Step-by-step belongs in
> [Tutorials](../tutorials/index.md).

## Static reference pages

mforth has no loom primitives (`skills/`/`commands/`/`agents/`/`hooks/`),
so the auto-discovered catalogues those generate are not part of this
surface. Reference here is static — CLI subcommands, dictionary words,
sidecar schema, event types, etc.

Pages to add as content lands:

- **CLI subcommands** (`repl`, `run`, `compile`, `lsp`, `version`) —
  exact flags, behaviour, exit codes.
- **Dictionary words** — every built-in word with its stack effect +
  one-line doc, plus Mindustry @-identifiers (magic vars + content
  names + sensor properties).
- **Sidecar schema** — `.world.toml` `[links.X]` (target/index modes)
  and (v2) `[cells.X]`.
- **Event types** — the frozen dataclasses `MockWorld` emits, what
  fields each carries.
- **mlog instruction set** — the subset mforth emits, with each
  source-Forth-word's lowering.

Each gets its own page in this directory and an entry in
`mkdocs.yml`'s `nav` block.
