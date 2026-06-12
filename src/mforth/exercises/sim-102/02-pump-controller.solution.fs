\ @exercise sim-102/02-pump-controller
\ One full loop body: decide from the reading, act on the pump, then
\ announce the decision. Both arms END at the same stack depth (empty)
\ — that's what lets the stack-checker accept the IF/ELSE.
: pump-controller ( level -- )
  20 < IF
    pump 1 CONTROL-ENABLED
    S" FILLING" PRINT
  ELSE
    pump 0 CONTROL-ENABLED
    S" FULL" PRINT
  THEN
;
