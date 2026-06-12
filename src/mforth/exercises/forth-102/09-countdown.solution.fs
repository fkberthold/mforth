\ @exercise forth-102/09-countdown
\ Reference solution. Each pass prints a copy of the counter, then
\ decrements; the loop stops the first time the counter hits 0. The
\ final 0 is left on the stack by the UNTIL test, so DROP clears it —
\ leaving the stack empty, as the declared effect ( n -- ) promises.
: countdown ( n -- ) BEGIN DUP . 1 - DUP 0 = UNTIL DROP ;
