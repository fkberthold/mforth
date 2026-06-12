\ @exercise forth-102/02-odd
\ Reference solution. `2 MOD 0 = NOT` reads as "is the remainder NOT zero".
: odd? ( n -- flag ) 2 MOD 0 = NOT ;
