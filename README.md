# mforth — a pragmatic Forth dialect that compiles to Mindustry mlog

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

mforth is a small Forth designed for a single, narrow target: the bytecode
([mlog](https://mindustrygame.github.io/wiki/logic/0-introduction/)) that
Mindustry's in-game logic processors execute. It ships two surfaces over one
front end:

- a **host REPL** (`mforth repl` / `mforth run example.fs`) that executes
  Forth against a Python simulation of the Mindustry world (a MockWorld +
  event stream), so you can author and debug without leaving your editor;
- an **AOT compiler** (`mforth compile example.fs -o example.mlog`) that
  emits paste-ready mlog text you can drop straight into a Mindustry logic
  processor.

Both surfaces share one parser, one AST, one dictionary, and one static
stack-checker. The headline correctness property is that the same `.fs`
source produces the same observable events under the host REPL as it does
when compiled to mlog and executed — the REPL is a teaching surface, and
divergence between it and the compiler is the highest-severity regression
class.

A `pygls`-based **LSP server** (`mforth lsp`), a **tree-sitter grammar**
for syntax highlighting, and a tiny **web visualizer** for the host REPL's
event stream all ship in the same package — they're just different
subscribers to the same pipeline.

## Quick start

```bash
pip install mforth

mforth repl                                          # interactive Forth REPL
mforth run examples/counter.fs                       # run a script under the host
mforth compile examples/counter.fs -o counter.mlog   # compile to mlog text
mforth lsp                                           # stdio LSP for editors
mforth version                                       # print version
```

`mforth run <file> --no-loop` exits after one pass instead of honoring
mlog's auto-loop semantics — handy for unit-style smoke tests.

## Example

`examples/counter.fs` is the minimal v1 demo: increment a variable and
print it to a message block.

```forth
VARIABLE n

: tick ( -- )
  n @ 1 + n !       \ n := n + 1
  n @ PRINT         \ queue the value into the print buffer
  display PRINTFLUSH \ flush to the sidecar-bound display block
;

tick
```

Paired with a sidecar `examples/counter.world.toml` that binds the symbolic
name `display` to the in-game `message1` block:

```toml
[links]
display = { target = "message1" }

[clock]
ipt = 8
```

Compiled with `mforth compile examples/counter.fs -o counter.mlog`, this
produces 7 mlog instructions ready to paste into a Mindustry logic
processor. Running the same source via `mforth run examples/counter.fs`
exercises it against a MockWorld and emits the same observable event
stream.

A slightly larger sibling, `examples/blink.fs`, adds a string prefix and
explicit `1 WAIT` pacing — it exercises the full set of load-bearing v1
dialect features in one program (`:` / `;`, `VARIABLE` / `@` / `!`,
integer arithmetic, string literals, `PRINT` / `PRINTFLUSH` / `WAIT`,
sidecar-bound block references).

## Status

This is **v1** — the pragmatic-Forth dialect (`POSTPONE` / `IMMEDIATE` /
`DOES>` / `EXECUTE` deliberately out of scope), static stack analysis
mandatory, inline-everything codegen, no return stack, no memory cells.
The full design and the optimization roadmap are captured in the MemPalace
decision drawers `drawer_mforth_decisions_3827fd238edc64f763e7b96b` (v1
locked design) and `drawer_mforth_decisions_d8910d0d11f2ce62c712c2ab` (v2
optimization tiers, fast > small).

v1 substantive work is complete: lex → parse → resolve → stackcheck →
host REPL → mlog backend → LSP → tree-sitter grammar → web viz, with
729+ tests pinning behavior and a property test that asserts REPL ↔ mlog
event-stream equivalence on every shipped example.

## Development

```bash
git clone https://github.com/fkberthold/mforth.git
cd mforth
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                          # full test suite
pytest tests/unit -q               # fast unit tests
pytest --cov                       # with coverage (85% gate on compiler core)
```

## License

MIT — see [LICENSE](LICENSE).

## Links

- **Source**: <https://github.com/fkberthold/mforth>
- **Issues**: <https://github.com/fkberthold/mforth/issues>
- **mlog reference**: <https://mindustrygame.github.io/wiki/logic/0-introduction/>
