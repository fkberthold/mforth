# tree-sitter-mforth

A [tree-sitter](https://tree-sitter.github.io/) grammar for **mforth** — a
pragmatic Forth dialect that compiles to Mindustry's mlog bytecode.

The grammar is intentionally small (Forth syntax is tiny) and aligned
with `src/mforth/lex.py` so that the eventual mforth LSP's semantic
tokens and this grammar's static highlights agree on what each token is.

## What it covers

| mforth token             | tree-sitter node     | highlights.scm capture     |
|--------------------------|----------------------|----------------------------|
| `\ line comment`         | `line_comment`       | `@comment`                 |
| `( block comment )`      | `block_comment`      | `@comment`                 |
| `: name body ;`          | `definition`         | colon/`;` `@keyword`, name `@function` |
| `."` and `S"` strings    | `string_literal`     | `@string`                  |
| signed decimal integer   | `number`             | `@number`                  |
| `IF ELSE THEN BEGIN UNTIL WHILE REPEAT DO LOOP` | `word` | `@keyword.control` |
| `DUP DROP SWAP OVER ROT NIP TUCK` | `word` | `@function.builtin` |
| `+ - * / MOD < > = <> <= >= AND OR NOT` | `word` | `@operator` |
| `VARIABLE @ !`           | `word`               | `@keyword`                 |
| `PRINT PRINTFLUSH WAIT SENSOR GETLINK` | `word` | `@function.builtin` |
| `I J` (DO/LOOP counters) | `word`               | `@variable.builtin`        |
| anything else            | `word`               | `@variable`                |

Word classification happens in `queries/highlights.scm` via `#match?`
predicates over case-insensitive name patterns. When the mforth LSP is
running, its semantic tokens override the static highlights with
dictionary-aware classification (e.g., user-defined words get
`@function`, undefined words trigger diagnostics).

## Install + build

Requires the tree-sitter CLI:

```bash
npm install -g tree-sitter-cli         # Node-based install
# OR
cargo install tree-sitter-cli          # Rust-based install
```

Then from this directory:

```bash
tree-sitter generate                   # generate src/parser.c from grammar.js
tree-sitter test                       # run the corpus tests
tree-sitter parse ../examples/blink.fs # parse a real .fs file
```

`tree-sitter generate` emits the C parser into `src/`. That C source is
NOT committed (it's a build artifact) — regenerate it locally with
`tree-sitter generate`. The grammar is the source of truth.

## Use with Helix

See `docs/how-to/use-with-helix.md` for the full wiring. Short version:

1. Build the parser locally (`tree-sitter generate`).
2. Append `editor/helix/languages.toml` to your
   `~/.config/helix/languages.toml`, replacing the placeholder source
   path with this directory's absolute path.
3. Run `hx --grammar fetch && hx --grammar build` from Helix to compile
   the grammar.
4. Open a `.fs` file in Helix — you should see syntactic highlights.

## Use with neovim (nvim-treesitter)

Not yet packaged. Add a custom parser config pointing at this directory
and `:TSInstall mforth`. Filed as a follow-up bead if anyone wants it.

## Design notes

* **Flat term list, not nested control flow.** `IF/ELSE/THEN`,
  `BEGIN/UNTIL`, `DO/LOOP` etc. appear in the tree as plain `word`
  nodes, not as structured `if_then` / `do_loop` nodes. The mforth
  Python parser (`src/mforth/parse.py`) builds the structured AST;
  tree-sitter stays flat because (a) tree-sitter highlighting only
  needs token classification, (b) folding works fine with the
  `definition` node alone, and (c) keeping the grammar simple keeps
  it stable as the dialect evolves.

* **Block comments are not extras.** The line comment is an `extras`
  rule (invisible to other grammar rules), but block comments are kept
  on the tree so editors can fold them. They are nestable in our lexer
  (deviates from standard Forth); the tree-sitter regex models two
  levels of nesting inline and degrades gracefully on deeper nesting.
  For correctness, the Python lexer is the source of truth — tree-sitter
  only needs the region for highlighting and folding.

* **`:foo` is a word, not `:` + `foo`.** mforth requires `:` to be
  whitespace-delimited to start a definition. Tree-sitter's longest-match
  rule handles this naturally: `\S+` (the `word` token) wins over the
  one-character `:` token whenever they're glued together. See the
  `non-standalone colon is a word` corpus test for the contract.

## License

MIT (same as the rest of mforth).
