\ @exercise sim-102/04-pick-drain
\ Milestone 2: the full three-arm decision. `OVER OVER` copies the
\ pair so the outer "both empty?" test (+ 0 =) can run without losing
\ the originals the inner `>` needs. Both arms of every IF leave the
\ stack empty, so the stack-checker is satisfied.
: pick-drain ( surge blast -- )
  OVER OVER + 0 = IF
    DROP DROP
    S" STOP" PRINT
  ELSE
    > IF S" SURGE" PRINT ELSE S" BLAST" PRINT THEN
  THEN
;
