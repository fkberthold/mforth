# 9. Factoring like a Forther

> **You will:** take the Forth habit seriously — compose programs out
> of tiny, well-named words; build a small vocabulary where bigger
> words reuse smaller ones; and refactor a fat definition into clean
> factors that read like sentences.
>
> **Before this:** [3. Defining words](03-defining.md) showed you
> `: ... ;`. Chapters [4](04-arithmetic.md)–[8](08-output.md) gave you
> arithmetic, branching, looping, state, and output. This chapter is
> about *taste* — how to put those pieces together well.

You already know how to define a word. The question this chapter
answers is *how small should a word be?* The Forth answer, repeated
until it becomes instinct, is: **small enough to name honestly, and no
bigger.** A program is a vocabulary you grow — each word a clear term
defined from terms you already have. This habit even has a name:
**factoring**, like factoring a number into its parts.

## The smell: a fat word

Here is a word that works but reads badly:

```forth
: announce ( n -- ) S" doubled=" PRINT DUP + PRINT ;
```

It prints `doubled=` and then the number doubled. It is correct. But
read the body aloud: "S-quote doubled-equals print, dup plus print." The
`DUP +` in the middle is *doing arithmetic* in a word whose job is
*announcing*. You have to decode the stack ops to see that `DUP +`
means "double". The intent is buried.

## The fix: name the piece

Pull the doubling out into its own word with an honest name, and let
`announce` call it:

```forth
: double   ( n -- n*2 ) DUP + ;
: announce ( n -- ) S" doubled=" PRINT double PRINT ;
```

Now `announce`'s body reads as a sentence: *print the label, then
print the double*. The `DUP +` trick lives in `double`, where its name
explains it. Run it:

```forth
: double   ( n -- n*2 ) DUP + ;
: announce ( n -- ) S" doubled=" PRINT double PRINT ;
5 announce
```

That prints `doubled=` then `10`. Same output as the fat version —
but now anyone reading `announce` understands it at a glance, and
`double` is available to reuse anywhere else. **Factoring costs
nothing at runtime** (mforth inlines your words), so the only thing you
trade for clarity is... nothing. Factor freely.

## Build a vocabulary, reuse upward

The real payoff comes when bigger words stand on smaller ones. Define
`squared`, then define `cubed` *in terms of it* instead of repeating
the stack work:

```forth
: squared ( n -- n*n ) DUP * ;
: cubed   ( n -- n*n*n ) DUP squared * ;
```

`cubed` keeps a copy of `n` with `DUP`, squares that copy with
`squared`, then multiplies by the original. It never repeats `DUP *` —
it *names* the squaring. Try it:

```forth
: squared ( n -- n*n ) DUP * ;
: cubed   ( n -- n*n*n ) DUP squared * ;
3 cubed .
```

That prints `27`. If you later discover a faster `squared`, every word
built on it — `cubed` and anything else — improves for free. That is
what a vocabulary buys you: each word is a single place to get one idea
right.

### The rules of thumb

- **Name what you can name.** If a stretch of stack ops has a meaning
  (`DUP +` is "double", `DUP *` is "square"), give it a word.
- **Read the body aloud.** If it does not read like a sentence of
  intent, a factor is hiding in it.
- **Reuse upward.** A bigger word should call smaller words, not paste
  their bodies. One idea, one place.
- **Keep stack effects honest.** Every factor carries its own
  `( before -- after )`. mforth's stack checker enforces it, and the
  comment is the contract the next reader trusts.

## Exercises

Write a `.fs`, then `mforth check my-answer.fs`. A pass prints
`✓ <id> — N/N cases pass`. Use `mforth check --scaffold <id>` for a
starter, `mforth check --solution <id>` if you are stuck.

The checker only sees your *output*, so any correct factoring passes —
but write them the factored way. That is the skill you are practising.

### Exercise 1 — a small vocabulary

`id: forth-103/05-vocabulary`

Define `squared` ( n -- n*n ): the number times itself. Then define
`cubed` ( n -- n*n*n ) **by reusing `squared`** — `cubed` must call
`squared`, not repeat `DUP *`.

```forth
\ @exercise forth-103/05-vocabulary
: squared ( n -- n*n ) ... ;
: cubed   ( n -- n*n*n ) ... ;
```

`4 squared .` gives `16`; `2 cubed .` gives `8`.

### Exercise 2 — refactor the fat word

`id: forth-103/06-refactor`

Define `announce` ( n -- ): print the label `doubled=`, then print `n`
doubled. Pull the doubling into its own word `double` ( n -- n*2 ) and
have `announce` call it — so `announce` reads as intent, not a pile of
stack ops.

`5 announce` should produce `doubled=` then `10`.

## What you learned

- Factoring is the core Forth discipline: small, honestly-named words
  composed into bigger ones.
- A fat word with buried intent is a smell; the fix is to **name the
  piece** and call it.
- A program is a **vocabulary** — bigger words reuse smaller words, so
  each idea lives in exactly one place.
- Factoring is free at runtime (mforth inlines), so clarity costs you
  nothing.

That closes **Part I — Thinking in Forth**. You can now build, name,
branch, loop, remember, and report — the whole stack-language mental
model. Next comes **Part II**, where you point all of it at the
Mindustry simulator: [10. Meet the simulator](10-simulator.md) — real
blocks, a `.world.toml` sidecar, and output that lands on a message
block in-game.
