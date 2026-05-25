\ blink.fs — v1 demo: print an incrementing counter to a message block,
\ paced by WAIT. Pairs with blink.world.toml (sidecar binds `display`
\ to the in-game message1 block).
\
\ Exercises: : / ; , VARIABLE / @ / !, arithmetic, PRINT / PRINTFLUSH / WAIT,
\ and mlog's auto-loop semantics (top-level re-executes until interrupted).

VARIABLE counter

: tick ( -- )
  counter @ 1 + counter !
  ." count=" PRINT
  counter @ PRINT
  display PRINTFLUSH
  1 WAIT
;

tick
