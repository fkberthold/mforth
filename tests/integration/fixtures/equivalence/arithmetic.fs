\ Exercise arithmetic, comparison, and logical primitives.
\ Each line computes a value and PRINTs it; final PRINTFLUSH commits.
\
\ mforth-dlr fix (2026-05-23): `/` is now included. The host primitive
\ uses Python's `/` (float division) and the mlog backend emits
\ `op div` (also float). For `20 4 /` both sides produce 5.0 → text
\ "5" (post mforth-05h: integer-valued floats render without ".0").
\ Cross-checks both convergence fixes in one fixture line.
2 3 + PRINT
10 4 - PRINT
6 7 * PRINT
20 4 / PRINT
17 5 MOD PRINT
3 5 < PRINT
5 3 > PRINT
4 4 = PRINT
1 0 AND PRINT
0 1 OR PRINT
0 NOT PRINT
display PRINTFLUSH
