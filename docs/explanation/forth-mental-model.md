# Forth, the mental model

Forth is a language for *composing*, not *naming*. Names are how
abstractions persist; composition is how data flows. Once you
internalize that asymmetry, postfix stops feeling backwards and
starts feeling like it's getting out of the way.

The tutorial's [Part 0 — Forth at a
glance](../tutorials/writing-mforth-for-mindustry.md#part-0-forth-at-a-glance)
gave you the *operational* mental model: what the stack is, how the
five juggling words behave, why postfix exists. That is enough to
read every example in this documentation set.

This page is the *compositional* mental model. Part 0 told you the
rules; this page argues for the style they reward.

## Composition over abstraction

A new Forth reader reaches for `VARIABLE` the same way a Python
reader reaches for `x =`. Both name an intermediate value so the
reader can refer to it later. Both feel safe. In Forth, both are a
smell.

Here is the same predicate twice — first with a named temporary,
then without.

```forth
\ With a temporary.
VARIABLE held
: charge-ok? ( capacity amount -- flag )
  held !                  \ stash amount; stack: ( capacity )
  held @                  \ recall amount; stack: ( capacity amount )
  <                       \ amount < capacity → flag
;
```

```forth
\ Without.
: charge-ok? ( capacity amount -- flag )
  <                       \ amount < capacity
;
```

The second version reads the inputs in their arrival order and
produces a flag. There is no name to introduce, no name to
remember, no name to look up. The data flowed through.

The named-temporary version is not wrong; it compiles and runs. But
it turns *data flow* into *state lookup*. Once `held` exists, a
later reader can no longer answer "what's in this variable right
now?" by scanning the linear left-to-right sequence — they have to
trace every `!` and `@`. Multiply that by a few dozen variables and
Forth loses its single greatest legibility win.

The rule of thumb: if a value will be consumed within the next few
tokens, leave it on the stack. `DUP`, `OVER`, and `SWAP` are not
clever tricks — they are how Forth says *the data flowed through*.

## Factoring as a stack discipline

When a sequence of two-to-five tokens starts to recur, or starts to
have a name you would reach for in prose ("is the vault full?",
"how much headroom is left?"), that sequence wants to become its
own word.

The tutorial's Part 5 is the worked example. The wiki's "All In"
script enables a conveyor per resource, each only when its vault
has room. The natural-language predicate is the same five times in
a row: *is there room for more?*

Naming the predicate once collapses the duplication:

```forth
: room-for-more? ( capacity amount -- flag )
  <                       \ amount below capacity → there is room
;

foundation1 @itemCapacity SENSOR
foundation1 @graphite SENSOR room-for-more?
IF conveyor1 1 CONTROL-ENABLED ELSE conveyor1 0 CONTROL-ENABLED THEN
```

The factored version is shorter, but that's a side effect. The
substantive change is the stack-effect comment `( capacity amount
-- flag )`. That comment is the *contract*:

- The body is allowed to consume exactly two values and produce
  exactly one.
- Every call site must arrive with the matching depth.
- mforth's stack-checker verifies the depths. Hover over
  `room-for-more?` in your editor and the LSP shows you the same
  notation; if your call site is wrong it will be flagged before
  you compile.

This is why Forth-style factoring scales further than copy-paste
abstraction in most languages: the contract is one line, it lives
next to the word, and it is checked. A reader learning the program
top-down can read the predicate's name and effect and skip the body
until they need to. The body, when they do read it, is one token.

The smell test runs the other direction too. If you can't write a
clean two-name stack-effect comment for a candidate word, the word
is doing too much. Cut it before the body grows.

## Where postfix shapes programs differently

Postfix is not strictly better than infix. It wins some categories
and loses others, and an honest mental model knows the difference.

**Postfix wins** on *linear single-use pipelines* — every
intermediate value flows directly into the next consumer, and is
never referred to again. Most block-control programs in Mindustry
look like this: sense a value, compare it against a threshold,
decide, emit.

```forth
foundation1 @copper SENSOR  500 <  IF conveyor1 1 CONTROL-ENABLED THEN
```

In infix this becomes something like `if sensor(foundation1, copper)
< 500 then control_enabled(conveyor1, true)` — same shape, more
punctuation, more named arguments to follow.

**Postfix loses** on *deeply nested expressions with shared
subexpressions*, on *logic that mixes many independent flags*, and
on *anything that needs random access to a value computed several
steps back*. A C expression like `(a*b + c*d) / (a*b - c*d)` has to
materialize `a*b` and `c*d` twice or stash them; the Forth version
needs `DUP` / `OVER` / `ROT` choreography that the C version doesn't
spend a token on.

When that choreography gets noisy, the answer is usually to factor
the sub-expression into its own word with a clean stack effect —
not to reach for a `VARIABLE`. The factored word names the
sub-computation; the stack still carries the intermediate.

The mistake to avoid is concluding from "postfix loses on nested
arithmetic" that postfix is bad. Most mlog programs are pipelines,
not expressions. Postfix wins for the shape of program you're
actually writing.

## The mforth-specific take

Forth is a family of languages, not a single language. ANS Forth
(the 1994 standard) is the most permissive end of the family — it
includes `POSTPONE`, `IMMEDIATE`, `DOES>`, and `EXECUTE`, words
that let user code extend the compiler at compile time. That power
costs static analyzability.

mforth picks the other end:

- **Static stack analysis is mandatory.** Every word's stack effect
  is statically knowable. Branches must produce the same depth on
  both sides; loops are stack-neutral. The compiler refuses to emit
  code otherwise. This is what makes the LSP's diagnostics trustable
  — they're the same analyzer the compiler runs.
- **No POSTPONE, IMMEDIATE, DOES>, or EXECUTE.** These words let
  code transform the dictionary mid-compile, which makes static
  stack analysis undecidable in general. v1 mforth drops them
  entirely. That removes one layer of meta-programming Forth can
  reach for, and replaces it with: when you need a new abstraction,
  factor a new word.
- **Cell-free codegen (v1).** There is no memory pool. The data
  stack lives in mlog variables (`s0..sN`) assigned by the slot
  allocator. `VARIABLE foo` compiles to a bare mlog variable named
  `foo`; `@` and `!` are reads and writes of that variable. No
  address arithmetic. The shape of factoring you reach for is
  different because the cost of a `VARIABLE` isn't "one cell of
  memory" — it's "one named mlog variable, fine, but every
  cross-tick read still goes through a slot reload first".
- **Two backends, one analyzer.** The host REPL and the mlog
  compiler share the same parser, dictionary, and stack-checker.
  This forces a particular discipline: equivalence between the two
  surfaces becomes a *property* the compiler can be tested against,
  not a hope. CLAUDE.md treats divergence as the highest-severity
  regression class.

This places mforth closer to the
[colorForth](http://www.greenarraychips.com/home/documents/index.html)
/ embedded-Forth lineage (Chuck Moore's later work; Mecrisp; pforth
in some configurations) than to ANS. The bet is that for the
particular target — a constrained, line-numbered, statically-shaped
bytecode — losing meta-compilation costs less than gaining static
guarantees.

If you're coming from a Gforth or ANS background, the words that
feel missing are mostly the meta-compilation ones. The words that
manipulate data on the stack are all here.

## When to break the rules

The heuristics above are heuristics, not laws.

`VARIABLE` is the right tool when:

- **State crosses ticks.** mlog programs run in a loop forever;
  some predicates depend on what happened last tick. Part 3's
  `charging` hysteresis flag is the canonical case — `charging` is
  literally last-tick's decision read back this-tick. The data
  *cannot* live on the stack across the loop boundary.
- **The name shows up in the compiled mlog.** A `VARIABLE foo`
  becomes a bare mlog identifier in the output. If you want to
  scan the compiled `.mlog` and see a named value (for in-game
  debugging, for cross-processor handoff), naming it is the way.
- **A user might want to tweak it.** Configuration thresholds —
  anything a non-author might reasonably want to change without
  re-reading the algorithm. Names are documentation for that
  audience.

The smell test for any `VARIABLE` you wrote: *would this variable
go away if I factored or re-ordered the surrounding code?* If yes,
it's hiding a missing word. If no — if the value genuinely needs to
outlive the local computation — keep it.

The mental model isn't "don't use names." It's "names are for the
things that need to persist; the rest is data flow."

---

For the operational rules this page builds on, see the tutorial's
[Part 0 — Forth at a glance][part0]. For the full worked example
of factoring, see [Part 5 — 'All In' as a definition][part5]. The
dictionary and stack-checker that enforce stack-effect contracts
are catalogued in the [Reference](../reference/index.md) quadrant.

[part0]: ../tutorials/writing-mforth-for-mindustry.md#part-0-forth-at-a-glance
[part5]: ../tutorials/writing-mforth-for-mindustry.md#part-5-all-in-as-a-definition
