\ @exercise sim-102/05-sorter-step
\ Milestone 3: the capstone, end to end. Same three-arm shape as
\ pick-drain, but every arm now ACTS on the unloader before it prints.
\ Configuring to NULL selects "no resource", which stops the drain.
: sorter-step ( surge blast -- )
  OVER OVER + 0 = IF
    DROP DROP
    unloader NULL CONTROL-CONFIG
    S" STOP" PRINT
  ELSE
    > IF
      unloader @surge-alloy CONTROL-CONFIG
      S" SURGE" PRINT
    ELSE
      unloader @blast-compound CONTROL-CONFIG
      S" BLAST" PRINT
    THEN
  THEN
;
