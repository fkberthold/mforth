\ Sorter Picker — full USR port (bead mforth-l8z).
\
\ Source: ~/wiki/Mindustry.md, section "Sorter Picker". The original
\ mlog (paraphrased to mforth):
\
\   read each vault's copper + lead levels; whichever resource has
\   more, point the unloader at that resource; if BOTH are empty, stop
\   unloading by configuring the unloader to null.
\
\ This is the canonical use case for the source-level NULL literal —
\ without it, v1 could only port the binary "drain the bigger pile"
\ shape (eri tutorial Part 4), missing the "stop when exhausted"
\ branch.
\
\ Exercises:
\   * NULL literal pushed onto the data stack.
\   * CONTROL-CONFIG lifted with @-identifier value (copper / lead arms).
\   * CONTROL-CONFIG lifted with NULL value (exhausted-supply arm).
\   * SENSOR with @-prop on link-uservar (the @copper / @lead reads).
\   * Nested IF/ELSE on (sum == 0) outer / (copper > lead) inner.
\   * PRINT + PRINTFLUSH status line so the event sequence carries
\     both ControlEvent AND MessagePrint*Event in defined order.
\
\ The equivalence harness asserts both backends (host REPL + compiled
\ mlog) produce the same event sequence against the same MockWorld.

vault1 @copper SENSOR        \ ( -- c )
vault1 @lead   SENSOR        \ ( c -- c l )
OVER OVER                    \ ( c l -- c l c l )  2DUP equivalent
+ 0 = IF
  \ Both vaults exhausted — stop unloading.
  DROP DROP
  unloader1 NULL CONTROL-CONFIG
  S" status: idle" PRINT
ELSE
  > IF
    \ More copper than lead — point the unloader at copper.
    unloader1 @copper CONTROL-CONFIG
    S" status: copper" PRINT
  ELSE
    \ Lead is the dominant resource (or tied — picks lead in the tie).
    unloader1 @lead CONTROL-CONFIG
    S" status: lead" PRINT
  THEN
THEN
display PRINTFLUSH
