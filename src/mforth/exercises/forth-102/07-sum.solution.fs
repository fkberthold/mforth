\ @exercise forth-102/07-sum
\ Reference solution. ( n ) 0 SWAP -> ( 0 n ); 1 + 1 DO ... LOOP runs
\ the index from 1 up to n (the limit n+1 is exclusive). Each pass adds
\ I to the running total. For n = 0 the range 1..1 is empty (zero-trip),
\ so the seed 0 falls straight through.
: sum ( n -- total ) 0 SWAP 1 + 1 DO I + LOOP ;
