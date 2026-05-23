"""Backend implementations for mforth — host REPL (host.py + world.py)
and mlog codegen (mlog.py, future)."""

from mforth.backend.host import ExecutionError, Executor

__all__ = ["ExecutionError", "Executor"]
