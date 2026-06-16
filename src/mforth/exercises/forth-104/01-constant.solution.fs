\ @exercise forth-104/01-constant
\ Reference solution. CONSTANT is a defining word you build yourself:
\ CREATE starts the child + gives it a field, , fills the field from the
\ stack, DOES> @ says the child fetches that field. `42 CONSTANT answer`
\ stamps `answer`, which folds to a bare literal push of 42 — no cell.
: CONSTANT  CREATE , DOES> @ ;
42 CONSTANT answer
