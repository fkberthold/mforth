\ arith_heavy.fs — a long chain of constant arithmetic feeding a print
\ sink. The point: const-folding (Tier A) should collapse the whole chain
\ to a single literal, so -O1/-Ofast emit DRAMATICALLY fewer instructions
\ (and execute far fewer per tick) than the O0 stack-machine lowering.
1 2 + 3 * 4 - 5 * 6 + 7 - 8 * 9 + PRINT
10 20 + 30 * 2 / 5 - PRINT
100 7 MOD 3 * 1 + PRINT
display PRINTFLUSH
