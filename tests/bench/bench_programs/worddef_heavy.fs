\ worddef_heavy.fs — several user words, some called multiple times. At
\ -O0/-O1/-Ofast every call is inlined (fast > small). This fixture is the
\ size-fallback exercise: it gives -Osize's @counter subroutine emitter a
\ real promotion candidate (a multi-call, non-trivial-body word) so the
\ benchmark can report the size knob's behavior alongside the speed tiers.
: square DUP * ;
: cube DUP square * ;
: quad square square ;

3 square PRINT
4 cube PRINT
2 quad PRINT
5 square PRINT
6 cube PRINT
display PRINTFLUSH
