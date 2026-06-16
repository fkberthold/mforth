\ CREATE / , / DOES> defining words with compile-time STAMPING (bead
\ mforth-7h1.2). The FIRST real equivalence fixture for the B2 metaprogramming
\ surface — it must be event-identical between the host REPL and the
\ compiled-then-interpreted mlog at O0 (CLAUDE.md headline property).
\
\ CONSTANT is the canonical defining word: `76 CONSTANT TROMBONES` runs the
\ CREATE-phase at compile time and stamps TROMBONES to a literal push of 76.
\ DOUBLED is the GENERAL stamper: its DOES> body `@ 2 *` is partial-evaluated
\ against the const field, so `21 DOUBLED X` stamps X to push 42. Both children
\ are cell-free (no memory-cell read/write) — values are literals on both
\ backends, so the PRINT events match exactly.
: CONSTANT CREATE , DOES> @ ;
: DOUBLED CREATE , DOES> @ 2 * ;
76 CONSTANT TROMBONES
21 DOUBLED X
TROMBONES PRINT
X PRINT
display PRINTFLUSH
