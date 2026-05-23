"""Enable ``python -m mforth`` by delegating to :func:`mforth.cli.main`.

Bead mforth-326. This module exists solely so ``python -m mforth`` and
the installed ``mforth`` console script share the same entry point.
Keep the body trivial — all CLI logic lives in :mod:`mforth.cli`.
"""

from __future__ import annotations

import sys

from mforth.cli import main


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
