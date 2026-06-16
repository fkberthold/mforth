\ @exercise forth-105/02-compose
\ Reference solution. Macros expand all the way down: `hundredx` ->
\ `tenx tenx` -> `10 * 10 *`. Both names evaporate before the program runs.
MACRO: tenx 10 * ;
MACRO: hundredx tenx tenx ;
