\ @exercise forth-102/08-factorial
\ Reference solution. Seed 1 (identity for *), loop I from 1..n,
\ multiply. n = 0 -> range 1..1 is empty, seed 1 survives -> 0! = 1.
: factorial ( n -- n! ) 1 SWAP 1 + 1 DO I * LOOP ;
