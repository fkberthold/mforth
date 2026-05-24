# Install mforth

> **Goal:** install mforth and verify the `mforth` command works.
>
> **Prerequisites:** Python 3.11 or newer, and `pip`.

## Steps

1. **Install from PyPI.**

    ```bash
    pip install mforth
    ```

    Or, to keep it isolated in a virtual environment (recommended
    if you're going to work on multiple Python projects):

    ```bash
    python -m venv ~/.venvs/mforth
    source ~/.venvs/mforth/bin/activate
    pip install mforth
    ```

2. **Verify the install.**

    ```bash
    mforth version
    ```

    Should print `mforth 0.1.0` (or whatever the current version is)
    and exit zero.

3. **(Optional) Confirm the full toolchain works.**

    ```bash
    mforth --help
    ```

    Should list at least the `repl`, `run`, `compile`, `lsp`, and
    `version` subcommands.

## Install from source (for contributors)

If you want to hack on mforth itself rather than use it as a tool,
install from a git checkout in editable mode:

```bash
git clone https://github.com/fkberthold/mforth
cd mforth
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The `.[dev]` extra pulls in `pytest` and `pytest-cov` for the test
suite. Verify:

```bash
pytest -q              # full test suite
pytest --cov           # with coverage gate on the compiler core
```

## Troubleshooting

- **`mforth: command not found` after `pip install`.** Confirm
  Python's user-script directory is on your `$PATH`. On most systems,
  `pip install --user mforth` puts the script at `~/.local/bin/mforth`.
  Add `~/.local/bin` to `$PATH` if it isn't already (or use a
  virtual environment, which puts the script on the venv's `PATH`
  automatically when activated).
- **`ERROR: Package 'mforth' requires a different Python: ...`** —
  mforth needs Python 3.11 or newer. Check `python --version`; if
  older, install a newer Python and retry. Most distributions ship
  multiple versions side-by-side (`python3.11`, `python3.12`, etc).
- **`mforth lsp` doesn't talk to Helix/Neovim.** The install step
  is fine; the editor wiring is separate. See
  [Use with Helix](use-with-helix.md) or [Use with Neovim](use-with-nvim.md).

## What to read next

- [Tutorial: Getting started](../tutorials/getting-started.md) — your
  first guided run-through after install.
- [Reference](../reference/index.md) — the catalogue of every
  mforth surface.
