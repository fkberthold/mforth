\ User-facing macro definition syntax `MACRO: name <body> ;` (bead
\ mforth-7h1.3). The first equivalence fixture for the B3 macro surface — it
\ must be event-identical between the host REPL and the compiled-then-
\ interpreted mlog at O0 (CLAUDE.md headline property).
\
\ `MACRO: sq DUP * ;` defines a user macro; at a use site the name expands at
\ compile time (hygienic term-substitution to a fixpoint) to its body. So
\ `6 sq` becomes `6 DUP *` -> 36, and `quad` (whose body references `sq`)
\ expands fully: `3 quad` -> 3 -> 9 -> 81. Every expanded macro is cell-free
\ (no memory-cell read/write) — values are literals/ops on both backends, so
\ the PRINT events match exactly.
MACRO: sq DUP * ;
MACRO: quad sq sq ;
6 sq PRINT
display PRINTFLUSH
3 quad PRINT
display PRINTFLUSH
