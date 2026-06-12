\ @exercise forth-102/03-between
\ Reference solution. Walk the stack ( lo hi n ):
\   SWAP      ( lo n hi )
\   OVER      ( lo n hi n )   copy n up so we keep it after comparing
\   >=        ( lo n flagHi ) hi >= n, i.e. n <= hi
\   ROT ROT   ( flagHi lo n ) bury the first flag, expose lo and n
\   <=        ( flagHi flagLo ) lo <= n
\   AND       ( flag )        both must hold
: between? ( lo hi n -- flag ) SWAP OVER >= ROT ROT <= AND ;
