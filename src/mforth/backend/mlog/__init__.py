"""mforth mlog codegen backend.

Submodules:

* `slots` — static stack-slot allocator (bead mforth-10t.15). Maps each
  annotated AST term to the `s<N>` mlog variable names it reads from and
  writes to, given the stack-checker's depth annotations.

* `emit` — walks the slot-annotated AST and emits mlog instruction tuples
  for arithmetic / stack / comparison / logical / variable primitives plus
  inlined user-definition calls (bead mforth-10t.16).

Future submodules (per design v1):

* `link` — joins emitted fragments and resolves jump labels (bead .17+).
"""

from mforth.backend.mlog.emit import MlogInstr, emit
from mforth.backend.mlog.slots import SlotMap, allocate_slots

__all__ = ["MlogInstr", "SlotMap", "allocate_slots", "emit"]
