\ @exercise forth-104/02-doubled
\ Reference solution. The DOES> body combines the field with a literal
\ (`@ 2 *`), which folds at compile time — so `x` stamps to a bare push
\ of 42. Field + literal folds; field + a runtime stack value would not.
: DOUBLED  CREATE , DOES> @ 2 * ;
21 DOUBLED x
