\ @exercise sim-102/06-sensed-pump
\ The full loop body, now SENSING its own reading instead of taking it
\ from the driver. The sidecar seeds tank1's liquid (30) and capacity
\ (100); `liquid*100 < capacity*40` asks "below 40% full?" with only
\ whole numbers. Both arms END at the same (empty) stack depth, so the
\ stack-checker accepts the IF/ELSE.
: sense-pump ( -- )
  tank1 @totalLiquids SENSOR 100 *
  tank1 @liquidCapacity SENSOR 40 *
  < IF
    pump 1 CONTROL-ENABLED
    S" FILLING" PRINT
  ELSE
    pump 0 CONTROL-ENABLED
    S" FULL" PRINT
  THEN
;
