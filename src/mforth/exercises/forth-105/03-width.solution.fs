\ @exercise forth-105/03-width
\ Reference solution. A literal-only macro reads like a named constant
\ and costs nothing: `WIDTH` is replaced by `40` at compile time.
MACRO: WIDTH 40 ;
