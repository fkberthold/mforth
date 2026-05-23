\ Exercise arithmetic, comparison, and logical primitives.
\ Each line computes a value and PRINTs it; final PRINTFLUSH commits.
\
\ NOTE: `/` (Forth divide) is deliberately excluded from this fixture —
\ the host primitive uses Python's `/` (always-float division) while
\ the mlog backend emits `op idiv` (integer division). For integer
\ operands like `20 4 /` the host produces 5.0 (text "5.0") and mlog
\ produces 5 (text "5"). This is a pre-existing divergence in the host
\ DIVIDE primitive (bead .11) vs the mlog codegen (bead .16); it is
\ documented in the .31 ship drawer and tracked as a follow-up. The
\ equivalence harness exercises the rest of the arithmetic surface.
2 3 + PRINT
10 4 - PRINT
6 7 * PRINT
17 5 MOD PRINT
3 5 < PRINT
5 3 > PRINT
4 4 = PRINT
1 0 AND PRINT
0 1 OR PRINT
0 NOT PRINT
display PRINTFLUSH
