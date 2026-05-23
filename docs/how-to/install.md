# Install mforth

> **Goal:** get mforth on a development machine and verify
> the install.
>
> **Prerequisites:** a POSIX shell, `git`, and read access to
> https://github.com/fkberthold/mforth.

## Steps

1. **Clone the repository.**

    ```bash
    git clone https://github.com/fkberthold/mforth
    cd $(basename https://github.com/fkberthold/mforth .git)
    ```

2. **Install dependencies.** Replace this step with the project's
   actual installation command. Common shapes:

    ```bash
    # for a Go project
    ./scripts/setup

    # for a Python project
    pip install -r requirements.txt

    # for a Node project
    npm install

    # for a Bash-tool project (e.g. loom-shaped)
    ./install.sh
    ```

3. **Verify the install.** Replace this step with whatever invocation
   shows the install succeeded. Whatever you pick should *exit zero*
   and produce visible output.

    ```bash
    mforth --version
    ```

## Troubleshooting

- **Command not found after install.** Confirm the install target is
  on your `$PATH`. Whether to add the install dir to `$PATH` is a
  per-project choice; this guide leaves the answer to the project.
- **Permission denied.** Re-check the install command's output for
  the install location and confirm you have write access there.
- **Step 3 produces no output.** The install probably failed silently
  in step 2 — re-run it with verbose flags and read the log.

## What to read next

- [Tutorial: Getting started](../tutorials/getting-started.md) — your
  first guided run-through after install.
- [Reference](../reference/index.md) — the catalogue of every
  mforth surface.
