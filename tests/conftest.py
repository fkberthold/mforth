"""Shared pytest configuration for mforth.

Bead mforth-10t.28. This file currently exists only as a marker so that
pytest treats ``tests/`` as a rootdir-anchored package and so future
shared fixtures have an obvious home.

Per-file helpers in tests/unit/test_*.py (``lex()``, ``p()``, ``check()``)
were inspected at scaffold time and judged too test-file-specific to
hoist into a shared fixture without losing readability. Revisit once
tests/integration and tests/golden land — equivalence fixtures will
likely want a real shared rig here.
"""

from __future__ import annotations
