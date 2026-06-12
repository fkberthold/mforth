\ Part 2 — A counter you can paste in-game.
VARIABLE n

: tick ( -- )
  n @ 1 + n !
  S" count=" PRINT
  n @ PRINT
  display PRINTFLUSH
  1 WAIT
;

tick
