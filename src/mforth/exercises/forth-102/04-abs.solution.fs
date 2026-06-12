\ @exercise forth-102/04-abs
\ Reference solution. The IF branch leaves one value (0 - n); the
\ skip-the-branch path also leaves one value (n). Balanced, so it
\ passes stackcheck.
: abs ( n -- |n| ) DUP 0 < IF 0 SWAP - THEN ;
