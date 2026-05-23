# Wire mforth into Neovim

> **Goal:** get tree-sitter syntactic highlighting and `mforth lsp`
> diagnostics for `.fs` files in [Neovim](https://neovim.io/).
>
> **Prerequisites:**
>
> - Neovim 0.9+ (semantic tokens + built-in LSP client).
> - [`nvim-treesitter`](https://github.com/nvim-treesitter/nvim-treesitter)
>   installed via your plugin manager of choice (lazy.nvim, packer,
>   kickstart, etc.).
> - [`nvim-lspconfig`](https://github.com/neovim/nvim-lspconfig)
>   installed if you want LSP wiring; tree-sitter highlighting works
>   without it.
> - The tree-sitter CLI on `$PATH`
>   (`npm install -g tree-sitter-cli` *or*
>   `cargo install tree-sitter-cli`) — `nvim-treesitter` shells out to
>   it when installing the parser.
> - A C compiler (`gcc` or `clang`) — `nvim-treesitter` compiles the
>   generated parser into a shared library.
> - This repository checked out at a stable absolute path.
> - `mforth` on `$PATH` (e.g. `pip install -e .` from the repo root)
>   if you want LSP wiring.

> **Verification status:** the configuration snippets below match the
> documented shape of `nvim-treesitter`'s
> [add-parsers recipe](https://github.com/nvim-treesitter/nvim-treesitter/blob/master/CONTRIBUTING.md#parser-configurations)
> and `nvim-lspconfig`'s
> [custom-server recipe](https://github.com/neovim/nvim-lspconfig/blob/master/doc/lspconfig.txt).
> Exact key names and module paths may vary across plugin-manager
> distributions (lazy.nvim, packer, kickstart, etc.) — adapt to your
> setup rather than copying verbatim.

## Steps

1. **Generate the tree-sitter parser.** From this repo's root:

    ```bash
    cd tree-sitter-mforth
    tree-sitter generate    # emits src/parser.c from grammar.js
    tree-sitter test        # verify the corpus tests pass
    ```

    `src/parser.c` is a build artifact (not committed) — regenerate it
    on each pull whenever `grammar.js` changes.

2. **Register the parser with `nvim-treesitter`.** In your Neovim
   config (e.g. `~/.config/nvim/init.lua` or a dedicated
   `lua/plugins/mforth.lua`), add the parser to
   `parser_config`, register the filetype, and attach a `FileType`
   autocmd that turns highlighting on:

    ```lua
    local parser_config = require("nvim-treesitter.parsers").get_parser_configs()
    parser_config.mforth = {
      install_info = {
        url = "/absolute/path/to/tree-sitter-mforth", -- or a git URL
        files = { "src/parser.c" },
        branch = "main",
        generate_requires_npm = false,
        requires_generate_from_grammar = false,
      },
      filetype = "mforth",
    }

    vim.filetype.add({ extension = { fs = "mforth" } })

    vim.api.nvim_create_autocmd("FileType", {
      pattern = "mforth",
      callback = function()
        vim.treesitter.start()
      end,
    })
    ```

    Then install the parser from inside Neovim:

    ```vim
    :TSInstall mforth
    ```

3. **Copy the highlight queries.** `nvim-treesitter` reads queries
   from `queries/mforth/highlights.scm` under its runtime path.
   Either symlink or copy the file from this repo:

    ```bash
    mkdir -p ~/.local/share/nvim/site/queries/mforth
    cp tree-sitter-mforth/queries/highlights.scm \
       ~/.local/share/nvim/site/queries/mforth/highlights.scm
    ```

    Re-run this step whenever `queries/highlights.scm` changes
    upstream.

4. **Wire `mforth lsp` via `nvim-lspconfig`.** `nvim-lspconfig` has
   no built-in entry for mforth, so register it as a custom server.
   In the same Lua config:

    ```lua
    local lspconfig = require("lspconfig")
    local configs = require("lspconfig.configs")

    if not configs.mforth then
      configs.mforth = {
        default_config = {
          cmd = { "mforth", "lsp" },
          filetypes = { "mforth" },
          root_dir = lspconfig.util.root_pattern(".world.toml", "pyproject.toml", ".git"),
          settings = {},
        },
      }
    end

    lspconfig.mforth.setup({})
    ```

5. **Verify the wiring.** Open any `.fs` file (e.g.
   `examples/blink.fs`) and confirm:

    - `:set ft?` reports `filetype=mforth`.
    - `:TSModuleInfo` lists `mforth` under the `highlight` column,
      with a checkmark.
    - Colors apply — comments dim, keywords bold, numbers and strings
      distinct, definition names highlighted as functions.
    - If `mforth` is on `$PATH`, `:LspInfo` shows the `mforth` client
      attached and parse / stack-balance / undefined-word diagnostics
      surface inline.

## What the LSP gives you

The custom-server block above launches `mforth lsp` for `.fs`
buffers. The capabilities the server ships today are catalogued in
the [Reference](../reference/index.md); the surface is:

- parse / stack-balance / undefined-word / sidecar-link diagnostics,
- hover (stack effects for built-ins and inferred effects for user
  words),
- completion (built-ins and user-defined words in scope),
- semantic tokens that refine tree-sitter's static highlighting.

If `mforth` is not on `$PATH`, Neovim logs the spawn failure under
`:LspLog` and falls back to tree-sitter highlighting only.

## Troubleshooting

- **`:TSInstall mforth` fails.** Read the error: missing C compiler
  or missing tree-sitter CLI are the common causes. Re-run with
  `:TSInstall! mforth` to force a rebuild. The `install_info.url`
  must point at the directory containing `grammar.js`, not at the
  generated `src/parser.c`.
- **No highlighting in a `.fs` buffer.** Check `:set ft?` — if it
  says `text` or empty, the `vim.filetype.add` call above didn't
  run. If filetype is right but colors are missing, check
  `:TSModuleInfo highlight` — an unchecked row means the parser
  installed but the highlight module isn't enabled for `mforth`.
- **`highlights.scm` not found.** Confirm the file lives at
  `~/.local/share/nvim/site/queries/mforth/highlights.scm` (or
  wherever your `runtimepath` puts user queries — check `:set rtp?`).
- **LSP fails to attach.** Run `mforth lsp --help` from your shell.
  If the command isn't found, install mforth (`pip install -e .`
  from the repo root) and restart Neovim. `:LspLog` shows the spawn
  error verbatim.
- **Re-link the grammar after a pull.** Re-run `tree-sitter generate`
  and `:TSUpdate mforth` whenever `grammar.js` changes; re-copy
  `highlights.scm` whenever the query file changes.

## What to read next

- [Tutorials](../tutorials/index.md) — a guided walkthrough now
  that your editor is wired up.
- [Use with Helix](./use-with-helix.md) — the same wiring for
  Helix's native tree-sitter + LSP support.
- [Reference](../reference/index.md) — the catalogue of every mforth
  surface, including the LSP capabilities listed above.
