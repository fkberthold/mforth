\ Mode B sidecar — display is bound via index, so the compiled mlog
\ emits a `getlink display 0` prologue. The program then uses display
\ for PRINTFLUSH. The REPL pre-seeds display as a UserVariable that
\ holds its own name; the mlog interpreter must execute the prologue
\ getlink so the consumer sees the same observable.
S" banner" PRINT
display PRINTFLUSH
