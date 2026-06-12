\ sensor_heavy.fs — a single-processor controller that reads several
\ sensor properties, does integer hysteresis math, and drives a CONTROL
\ sink. Models the realistic v1 demo shape (cf. the power-charge tutorial):
\ SENSOR reads are NOT constant-foldable, so this fixture measures what the
\ optimizer does to the surrounding glue (staging copies, dead stores,
\ peephole) WITHOUT a pure-arithmetic collapse skewing the numbers.
VARIABLE cap
VARIABLE stored

reactor1 @powerNetCapacity SENSOR cap !
reactor1 @powerNetStored   SENSOR stored !

stored @ 100 *  cap @ 95 *  <        \ notMin = stored < cap*0.95
IF generator1 1 CONTROL-ENABLED
ELSE generator1 0 CONTROL-ENABLED
THEN

stored @ PRINT
cap @ PRINT
display PRINTFLUSH
