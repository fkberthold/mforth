\ Sidecar-seeded SENSOR equivalence fixture (bead mforth-0pg).
\
\ The sidecar seeds vault1's @totalItems (80) and @itemCapacity (100)
\ via the new `sensors = { ... }` table. The program SENSES both, then
\ DECIDES "over half full?" with items*2 > capacity (160 > 100 -> true)
\ and PRINTs a status word per arm.
\
\ This is the headline equivalence gate for the seeding feature: the
\ host REPL reads the seeded values via `world.sensor`, the compiled
\ mlog reads them via the `sensor` instruction against the SAME
\ build_world-seeded MockWorld. Both backends MUST sense 80 / 100, take
\ the SAME branch, and emit the identical event sequence — otherwise the
\ seeding has diverged the two surfaces, the worst-severity regression.
vault1 @totalItems SENSOR 2 *      \ ( -- items*2 )      160
vault1 @itemCapacity SENSOR        \ ( i2 -- i2 cap )    100
> IF
  S" status: over-half" PRINT
ELSE
  S" status: under-half" PRINT
THEN
display PRINTFLUSH
