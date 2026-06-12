\ loop_heavy.fs — a counted DO/LOOP whose body does constant arithmetic
\ and a few stack ops feeding a print sink. The loop body runs many times
\ per tick, so any per-iteration instruction the optimizer removes (folded
\ constants, eliminated staging copies, peephole collapse) multiplies into
\ a large dynamic-instructions-per-tick win.
VARIABLE acc
0 acc !
0 8 DO
  2 3 + 4 *          \ constant 20 each iteration (fold target)
  acc @ +           \ accumulate
  DUP acc !
  PRINT
LOOP
acc @ PRINT
display PRINTFLUSH
