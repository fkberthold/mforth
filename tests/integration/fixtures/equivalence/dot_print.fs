\ mforth-va2 equivalence fixture — the standard Forth `.` word
\ ( n -- ): pop the top of the data stack and print it.
\
\ `.` works in the host REPL (backend/primitives.py::_dot funnels the
\ popped value through world.print → MessagePrintEvent) but the mlog
\ emitter previously deferred it (NotImplementedError). After mforth-va2
\ the emitter lowers `.` to `print s<i-1>` — the SAME slot-form PRINT
\ uses for a runtime value — so both backends emit an identical
\ MessagePrintEvent sequence.
\
\ Cases exercised:
\   5 .        → prints "5"   (bare literal popped and printed)
\   7 3 - .    → prints "4"   (arithmetic result popped and printed)
\   9 2 / .    → prints "4.5" (true float renders with its decimal —
\                              integer-valued floats strip `.0`, this
\                              one keeps it; confirms the formatting
\                              rule matches the host `.` exactly)
\   20 4 / .   → prints "5"   (integer-valued float renders WITHOUT
\                              a trailing `.0`, matching the in-game
\                              `print` stringification rule)
\
\ A final `display PRINTFLUSH` mirrors the established fixture pattern;
\ the `.`-driven MessagePrintEvents are the headline assertions.
5 .
7 3 - .
9 2 / .
20 4 / .
display PRINTFLUSH
