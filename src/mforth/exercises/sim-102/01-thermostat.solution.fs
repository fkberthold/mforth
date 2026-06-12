\ @exercise sim-102/01-thermostat
\ The decide step of a control loop, on its own: one reading in, one
\ decision printed. `30 >` leaves a flag; IF/ELSE prints the verdict.
: thermostat ( temp -- )
  30 > IF S" COOL" PRINT ELSE S" HOLD" PRINT THEN
;
