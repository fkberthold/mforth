\ @exercise forth-105/01-macro
\ Reference solution. A macro pastes its body in place of the name at
\ compile time: every `bump` becomes `1 +` before the program runs.
MACRO: bump 1 + ;
