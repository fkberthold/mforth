\ @exercise forth-103/01-bump
\ Reference solution. `count` is the named cell; `bump` reads it, adds
\ one, and writes the new value back — pure state, nothing left on the
\ stack.
VARIABLE count
0 count !
: bump ( -- ) count @ 1 + count ! ;
