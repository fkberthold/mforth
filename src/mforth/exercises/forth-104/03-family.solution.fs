\ @exercise forth-104/03-family
\ Reference solution. One defining word, two independent children: each
\ carries its own field and folds to its own literal at compile time
\ (`six` -> 36, `nine` -> 81). Field-only arithmetic (`@ DUP *`) folds.
: SQUARED-C  CREATE , DOES> @ DUP * ;
6 SQUARED-C six
9 SQUARED-C nine
