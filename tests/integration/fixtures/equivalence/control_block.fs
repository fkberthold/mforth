\ CONTROL block-instruction equivalence (bead mforth-cto).
\ Smallest version of USR's ConveyorBlock pattern (~/wiki/Mindustry.md):
\   sense a vault's item level, branch on a threshold, control a
\   conveyor's enabled flag, and update a sorter's config to the
\   dominant item. Then print + flush a status line so the event
\   sequence carries both PRINT/PRINTFLUSH AND CONTROL events.
\
\ Exercises:
\   * CONTROL-ENABLED with literal flag → lift to single instruction.
\   * CONTROL-CONFIG with @-identifier value → lift to single instruction.
\   * Mode A sidecar substitution (display → message1) still works for
\     PRINTFLUSH after the CONTROL ops.
\
\ The equivalence harness exercises both backends (host REPL + compiled
\ mlog) against the same MockWorld and asserts the ControlEvent +
\ MessagePrintEvent + MessagePrintflushEvent sequence matches exactly.

cv1 1 CONTROL-ENABLED
sorter1 @copper CONTROL-CONFIG
S" status: armed" PRINT
display PRINTFLUSH
