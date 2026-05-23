# Wire mforth into Helix

> **Goal:** get tree-sitter syntactic highlighting and `mforth lsp`
> diagnostics for `.fs` files in [Helix](https://helix-editor.com/).
>
> **Prerequisites:**
>
> - Helix with `hx --grammar` support (any recent release).
> - The tree-sitter CLI (`npm install -g tree-sitter-cli` *or*
>   `cargo install tree-sitter-cli`).
> - This repository checked out at a stable absolute path.
> - `mforth` on `$PATH` (e.g. `pip install -e .` from the repo root)
>   if you want LSP wiring; tree-sitter highlighting works without it.

## Steps

1. **Build the tree-sitter parser.** From this repo's root:

    ```bash
    cd tree-sitter-mforth
    tree-sitter generate    # emits src/parser.c from grammar.js
    tree-sitter test        # verify the corpus tests pass
    ```

    `src/parser.c` is a build artifact (not committed) — regenerate it
    on each pull whenever `grammar.js` changes.

2. **Append the Helix fragment.** Open `~/.config/helix/languages.toml`
   (create it if missing) and append the contents of
   `editor/helix/languages.toml` from this repo. Replace the
   placeholder source path with the absolute path to your local
   `tree-sitter-mforth` directory:

    ```toml
    [[language]]
    name = "mforth"
    scope = "source.mforth"
    file-types = ["fs"]
    roots = [".world.toml", "pyproject.toml"]
    comment-token = "\\"
    indent = { tab-width = 2, unit = "  " }
    language-servers = ["mforth-lsp"]

    [[grammar]]
    name = "mforth"
    source = { path = "/absolute/path/to/tree-sitter-mforth" }

    [language-server.mforth-lsp]
    command = "mforth"
    args = ["lsp"]
    ```

3. **Let Helix build the grammar.** From the shell:

    ```bash
    hx --grammar fetch
    hx --grammar build
    ```

    (Or from inside Helix: `:grammar fetch` then `:grammar build`.)

4. **Verify the wiring.** Open any `.fs` file (e.g.
   `examples/blink.fs`) and confirm:

    - Comments dim, keywords bold, numbers and strings distinct,
      definition names highlighted as functions.
    - Inside the buffer, `:lang` reports `mforth`.
    - If `mforth` is on `$PATH`, the LSP attaches on buffer open —
      parse / stack-balance / undefined-word diagnostics surface
      inline, hover shows stack effects, and completion offers
      built-ins and user-defined words in scope.

## What the LSP gives you

The `language-server.mforth-lsp` block above launches `mforth lsp` for
`.fs` buffers. The capabilities the server ships today are catalogued
in the [Reference](../reference/index.md); the surface is:

- parse / stack-balance / undefined-word / sidecar-link diagnostics,
- hover (stack effects for built-ins and inferred effects for user
  words),
- completion (built-ins and user-defined words in scope),
- semantic tokens that refine tree-sitter's static highlighting.

If `mforth` is not on `$PATH`, Helix logs a "command not found"
warning and falls back to tree-sitter highlighting only.

## Troubleshooting

- **Tree-sitter says "no language found".** Helix looks for grammars
  under its config directory's `runtime/grammars/`. Re-run
  `hx --grammar build` and read the output for compile errors. The
  `source = { path = ... }` must point at the directory containing
  `grammar.js`, not at the generated `src/parser.c`.
- **No highlighting at all.** Inside a `.fs` buffer, `:lang` should
  say `mforth`. If it says `text`, the `file-types = ["fs"]` line
  isn't being read — check that your `languages.toml` is well-formed.
- **LSP errors on every buffer.** Confirm `mforth lsp` runs from your
  shell (`mforth lsp --help` should print usage). If `mforth` is not
  installed, comment out the `language-servers = ["mforth-lsp"]` line
  until you install it — tree-sitter highlights work without the LSP.
- **Re-link the grammar after a pull.** Run `tree-sitter generate`
  and `hx --grammar build` again whenever `grammar.js` changes.

## What to read next

- [Tutorials](../tutorials/index.md) — a guided walkthrough now
  that your editor is wired up.
- [Use with Neovim](./use-with-nvim.md) — the same wiring for
  nvim-treesitter and nvim-lspconfig.
- [Reference](../reference/index.md) — the catalogue of every mforth
  surface, including the LSP capabilities listed above.
