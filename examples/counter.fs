\ counter.fs — v1 demo: the minimal pedagogical artifact for mforth.
\
\ Distinct from blink.fs in two ways: no string-prefix message, and no
\ WAIT-paced loop. Just "increment, print the value to display." The
\ runtime's auto-loop semantics (top-level re-executes until interrupted)
\ provides the implicit pacing.
\
\ Exercises: : / ; , VARIABLE / @ / !, integer literals, +, PRINT,
\ PRINTFLUSH, and (via the auto-loop) mlog's "fall off the end and
\ restart" semantics on a real logic processor.
\
\ Why PRINT and not `.`: in the host REPL, `.` would route through
\ MockWorld.print → MessagePrintEvent (the equivalence-safe sink), but
\ the mlog backend does NOT codegen `.` in v1 — see the followup bead
\ filed by mforth-10t.32 for the "host-only `.` lowers to display+PRINT
\ + PRINTFLUSH on a default sink in mlog" path. Until that lands, every
\ output-producing example uses PRINT explicitly with a sidecar-bound
\ display so REPL and mlog stay in lock-step (the headline equivalence
\ contract in CLAUDE.md).
\
\ Cell-free per CLAUDE.md "v1 stays cell-free": `n` compiles to a bare
\ mlog variable, not a memory cell. Inline-everything per the v1 codegen
\ strategy: `tick` has no call/return — its body is pasted at each call
\ site.

VARIABLE n

: tick ( -- )
  n @ 1 + n !       \ n := n + 1
  n @ PRINT         \ queue the new value into the print buffer
  display PRINTFLUSH \ flush to the sidecar-bound display block
;

tick
