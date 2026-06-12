\ @exercise forth-103/02-total
\ Reference solution. `add` folds the number on the stack into the
\ running total: read the old total, add the new number, write it back.
VARIABLE total
0 total !
: add ( n -- ) total @ + total ! ;
