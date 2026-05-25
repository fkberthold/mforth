# CLI

The `mforth` command-line tool. One binary, five subcommands. No global
flags beyond `-h` / `--help`. Every subcommand also accepts `-h` / `--help`
and exits 0 after printing usage.

```
mforth <subcommand> [options]
```

Subcommands:

| Name      | Purpose                                                       |
| --------- | ------------------------------------------------------------- |
| `run`     | Execute a `.fs` file against MockWorld (with mlog auto-loop). |
| `compile` | Compile a `.fs` file to mlog text.                            |
| `repl`    | Drop to an interactive mforth REPL prompt.                    |
| `lsp`     | Run the mforth language server over stdio.                    |
| `version` | Print the mforth package version and exit.                    |

A subcommand is required. Invoking `mforth` with no arguments exits 2
with an argparse usage error.

## `mforth run`

```
mforth run [--no-loop] <source>
```

Lex, parse, resolve, stack-check, and execute `<source>` against the host
MockWorld. If a sibling `<source>.world.toml` exists it is loaded as the
sidecar; if absent, an empty `WorldConfig` is used.

| Argument / flag | Default | Behaviour                                                                                                                |
| --------------- | ------- | ------------------------------------------------------------------------------------------------------------------------ |
| `source`        | —       | Required positional. Path to a `.fs` file.                                                                               |
| `--no-loop`     | off     | Execute the top-level sequence exactly once instead of mlog's auto-loop. Test-friendly for programs without a `WAIT`.    |
| `-h`, `--help`  | —       | Print subcommand usage and exit 0.                                                                                       |

Default (no `--no-loop`) is auto-loop: the top-level sequence re-runs
until interrupted with `Ctrl-C`. On `SIGINT` the process prints a
one-line summary (`mforth: interrupted after N iteration(s); simulated
tick=T`) to stderr and exits 130.

Exit codes:

| Code | Meaning                                                                                  |
| ---- | ---------------------------------------------------------------------------------------- |
| 0    | Clean completion under `--no-loop`.                                                      |
| 1    | Lex / parse / resolve / stack-check / sidecar / runner / execution error. Message on stderr in `file:line:col: <message>` form when source location is known. |
| 130  | `SIGINT` during the auto-loop. POSIX convention (128 + SIGINT).                          |

See also: [Getting started](../tutorials/getting-started.md),
[Writing mforth for Mindustry](../tutorials/writing-mforth-for-mindustry.md).

## `mforth compile`

```
mforth compile -o <output> [--emit-comments] <source>
```

Lex, parse, resolve, stack-check, allocate static stack slots, emit mlog,
finalize, and write the result to `<output>`. Sidecar resolution matches
`mforth run` — a sibling `<source>.world.toml` is loaded when present.

| Argument / flag         | Default | Behaviour                                                                                                                       |
| ----------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `source`                | —       | Required positional. Path to a `.fs` file.                                                                                      |
| `-o`, `--output OUTPUT` | —       | Required. Destination path for the emitted mlog text. Overwrites if the file exists.                                            |
| `--emit-comments`       | off     | Interleave per-term source-location comments in the output. Off by default — the in-game mlog editor can choke on comment lines. |
| `-h`, `--help`          | —       | Print subcommand usage and exit 0.                                                                                              |

Output is paste-ready: one mlog instruction per line, space-joined
`opcode op1 ... opN`, single trailing newline, plus one header `#`
comment line identifying the source.

Exit codes:

| Code | Meaning                                                                                                                                 |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | Successful write.                                                                                                                       |
| 1    | Source file not found, lex / parse / resolve / stack-check / sidecar / emit error, sidecar substitution failure, or output write error. |

Optimization-level flags (`-Ofast`, `-Osize`) are reserved for v2 and
are not accepted in v1.

## `mforth repl`

```
mforth repl [--load FILE]
```

Drop to an interactive prompt. Definitions and variables persist across
prompts within the session. Same parser, dictionary, stack-checker, and
host executor as `mforth run` — the REPL is the teaching surface for
the REPL ↔ mlog equivalence property.

| Argument / flag | Default | Behaviour                                                                                                            |
| --------------- | ------- | -------------------------------------------------------------------------------------------------------------------- |
| `--load FILE`   | none    | Preload the named `.fs` file before the first prompt. Its definitions and variables are available at the prompt.    |
| `-h`, `--help`  | —       | Print subcommand usage and exit 0.                                                                                   |

Exit codes:

| Code | Meaning                                                              |
| ---- | -------------------------------------------------------------------- |
| 0    | Normal exit (EOF / quit).                                            |
| 2    | `--load FILE` was given but the file could not be read (`OSError`). |

## `mforth lsp`

```
mforth lsp
```

Run the mforth Language Server Protocol server over stdio. No
positional arguments, no options. The server speaks LSP on stdin/stdout
and logs to stderr. Intended to be launched by an editor, not by hand.

| Argument / flag | Default | Behaviour                                              |
| --------------- | ------- | ------------------------------------------------------ |
| `-h`, `--help`  | —       | Print subcommand usage and exit 0.                     |

Exit code is whatever the server returns — typically 0 on a clean
shutdown initiated by the client.

See also: [Use with Helix](../how-to/use-with-helix.md),
[Use with Neovim](../how-to/use-with-nvim.md).

## `mforth version`

```
mforth version
```

Print `mforth <version>` to stdout and exit 0. Takes no arguments.
Version is resolved via `importlib.metadata.version("mforth")` and
falls back to `mforth.__version__` when dist-info is unavailable
(some editable-install configurations).

| Argument / flag | Default | Behaviour                                          |
| --------------- | ------- | -------------------------------------------------- |
| `-h`, `--help`  | —       | Print subcommand usage and exit 0.                 |

Exit code: always 0.
