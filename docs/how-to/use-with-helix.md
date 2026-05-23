# How to wire mforth into Helix

This guide gets you syntactic highlighting for `.fs` files in
[Helix](https://helix-editor.com/) via the in-repo `tree-sitter-mforth`
grammar, plus LSP wiring that activates once `mforth lsp` ships
(bead `mforth-10t.20`).

> **Status:** this is a minimal sketch. A fuller pass lands when the
> `/docs-scaffold` Diataxis substrate (bead `mforth-10t.3`) reshapes
> `docs/` into tutorials / how-to / reference / explanation. Until then
> this file lives standalone.

## Prerequisites

* Helix (any recent version with `hx --grammar` support).
* The tree-sitter CLI:
  ```bash
  npm install -g tree-sitter-cli   # OR: cargo install tree-sitter-cli
  ```
* This repository checked out somewhere stable on disk. You'll need its
  absolute path below.

## Step 1 — build the grammar

From this repo's root:

```bash
cd tree-sitter-mforth
tree-sitter generate    # emits src/parser.c from grammar.js
tree-sitter test        # verify the corpus tests pass
```

`src/parser.c` is a build artifact (not committed) — regenerate it on
each pull.

## Step 2 — append the Helix fragment

Open `~/.config/helix/languages.toml` (create it if missing). Append
the contents of `editor/helix/languages.toml` from this repo, replacing
the placeholder path with the absolute path to your local
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
source = { path = "/home/you/repos/mforth/tree-sitter-mforth" }

[language-server.mforth-lsp]
command = "mforth"
args = ["lsp"]
```

## Step 3 — let Helix build the grammar

From the shell:

```bash
hx --grammar fetch
hx --grammar build
```

(Or from inside Helix: `:grammar fetch` then `:grammar build`.)

Open any `.fs` file and you should see colors applied — comments dim,
keywords bold, numbers and strings distinct, definition names
highlighted as functions.

## About the LSP wiring

The `language-server.mforth-lsp` block above tells Helix to launch
`mforth lsp` for `.fs` buffers. **That subcommand does not exist yet** —
it lands with bead `mforth-10t.20` (LSP server). Until then:

* Helix will quietly fail to start the LSP and log a "command not found"
  warning. Tree-sitter highlighting still works without it.
* Once the LSP ships and `mforth` is on your PATH (e.g., via
  `pip install -e .` from the repo root), no further config change is
  needed — Helix will pick it up on the next buffer open.

When the LSP is running, you'll also get:

* parse / stack-balance / undefined-word / sidecar-link diagnostics,
* hover (stack effects for built-ins and inferred effects for user words),
* completion (built-ins and user-defined words in scope),
* go-to-definition for user words,
* semantic tokens that refine tree-sitter's static highlighting.

## Troubleshooting

* **Tree-sitter says "no language found".** Helix is looking in the
  config directory's `runtime/grammars/`. Run `hx --grammar build`
  again; check the output for compile errors. The `source = { path = ... }`
  must point at the directory containing `grammar.js`, not at the
  generated `src/parser.c`.

* **No highlighting at all.** Confirm Helix picks up the language with
  `:lang` inside a `.fs` buffer — it should say `mforth`. If it says
  `text`, the `file-types = ["fs"]` line isn't being read; check that
  your `languages.toml` is well-formed TOML.

* **LSP errors on every buffer.** Comment out the
  `language-servers = ["mforth-lsp"]` line until `mforth-10t.20` ships
  and `mforth lsp` is on PATH. Tree-sitter highlights work without it.

* **Re-link the grammar after a pull.** Run `tree-sitter generate` and
  `hx --grammar build` again whenever `grammar.js` changes.
