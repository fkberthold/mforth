\ @exercise forth-101/07-back-rot
\ Reference solution. Two ROTs undo one ROT: ( a b c -- c a b ).
: back-rot ( a b c -- c a b ) ROT ROT ;
