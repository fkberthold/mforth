\ @exercise sim-101/06-act
\ Reference solution. Sense -> decide -> act, with the decision printed
\ so the checker can see it.
: restock ( -- )
  vault1 @totalItems SENSOR 0 =   \ ( -- empty? )
  DUP .                           \ print a copy of the flag
  IF   miner 1 CONTROL-ENABLED    \ empty: run the miner
  ELSE miner 0 CONTROL-ENABLED    \ stocked: stop it
  THEN
;
