#!/usr/bin/env bash
# One-time, per-clone setup for the beads-normalize git clean filter.
#
# Git clean filters require a `git config` entry that CANNOT be committed, so
# each fresh clone of this repo must run this script once. After that, staging
# `.beads/issues.jsonl` passes it through scripts/normalize-beads-jsonl.py, which
# stabilizes the order of the `_type:memory` lines (see that script + bead
# mforth-e5n).
#
# Safe to re-run (idempotent): it just re-sets the same two config values.
set -euo pipefail

# Run from the repo root regardless of where the script is invoked from.
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

git config filter.beads-normalize.clean "python3 scripts/normalize-beads-jsonl.py"
git config filter.beads-normalize.smudge cat

echo "Installed git clean filter 'beads-normalize':"
echo "  clean  = $(git config --get filter.beads-normalize.clean)"
echo "  smudge = $(git config --get filter.beads-normalize.smudge)"
echo
echo "Now '.beads/issues.jsonl' is normalized on stage (per .gitattributes)."
