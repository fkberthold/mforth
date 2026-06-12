#!/usr/bin/env python3
"""Normalize .beads/issues.jsonl so the committed file is stable across exports.

WHY THIS EXISTS (bead mforth-e5n, a local workaround for upstream bug mforth-e5n):
  The `bd` export hook writes `.beads/issues.jsonl`. The `_type:memory` lines
  (one per `bd remember` key) are emitted in NON-deterministic hash-map order,
  so the file re-dirties on every commit — the content is identical, only the
  ORDER of the memory block shuffles. That churns the working tree endlessly.

WHAT THIS DOES:
  Reads JSONL on stdin, writes JSONL on stdout. Issue lines (every line that is
  NOT `"_type":"memory"`) are emitted FIRST, in their original input order
  (their order is already stable upstream). Memory lines are then emitted as a
  contiguous trailing block, SORTED deterministically by their `"key"` field.

  Each emitted line is the ORIGINAL input line, byte-for-byte — we parse only to
  classify a line and read its sort key, never to re-serialize. So a line's JSON
  content is never altered; only the order of the memory block changes. This
  makes the canonical output a pure permutation of the input lines.

USAGE (git clean filter — the portable mechanism for this):
  This is wired as a git clean filter via `.gitattributes`:
      .beads/issues.jsonl filter=beads-normalize
  Git clean filters require a PER-CLONE `git config` (it is not committable), so
  every clone must run this ONE-TIME setup once:

      git config filter.beads-normalize.clean "python3 scripts/normalize-beads-jsonl.py"
      git config filter.beads-normalize.smudge cat

  Or just run `scripts/install-beads-filter.sh`, which does exactly that.

  After setup, `git add .beads/issues.jsonl` (and any commit that stages it)
  passes the file through this normalizer, so what lands in the index/commit is
  the canonical, stable ordering regardless of bd's export order.

MANUAL USE / TESTING:
      python3 scripts/normalize-beads-jsonl.py < .beads/issues.jsonl
"""
from __future__ import annotations

import json
import sys


def normalize(lines: list[str]) -> list[str]:
    """Return lines reordered canonically: issue lines (original order) then
    memory lines sorted by their `key`. Input line text is preserved exactly.

    A line is a "memory" line iff it parses as a JSON object with
    `_type == "memory"`. Anything else (issue lines, and any line that does not
    parse — which we never expect, but handle conservatively) is treated as a
    non-memory line and keeps its original position.
    """
    issue_lines: list[str] = []
    memory_lines: list[tuple[str, str]] = []  # (sort_key, original_line)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Preserve blank lines in place as non-memory content.
            issue_lines.append(line)
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            # Unparseable line: never expected. Pass through in place rather
            # than dropping data.
            issue_lines.append(line)
            continue
        if isinstance(obj, dict) and obj.get("_type") == "memory":
            # Sort by key; fall back to the whole line if key is missing so the
            # order is still deterministic.
            sort_key = obj.get("key")
            if not isinstance(sort_key, str):
                sort_key = stripped
            memory_lines.append((sort_key, line))
        else:
            issue_lines.append(line)

    memory_lines.sort(key=lambda pair: pair[0])
    return issue_lines + [line for _key, line in memory_lines]


def main() -> int:
    # Read raw; splitlines(keepends=True) so we can faithfully round-trip the
    # final-newline situation. We emit each retained line followed by exactly
    # one "\n", and we do NOT introduce a trailing newline if the input lacked
    # one on its last line.
    data = sys.stdin.read()
    if data == "":
        return 0
    raw_lines = data.splitlines()  # drops line terminators; we re-add "\n"
    had_trailing_newline = data.endswith("\n")

    out_lines = normalize(raw_lines)

    out = "\n".join(out_lines)
    if had_trailing_newline and out:
        out += "\n"
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
