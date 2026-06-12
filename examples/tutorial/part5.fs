\ Part 5 — 'All In' as a definition.
\ Enable each conveyor when the vault has room for more of that resource.
: room-for-more? ( amount capacity -- 1/0 )
  >
;

\ --- pair 1: graphite → conveyor1 ---
foundation1 @itemCapacity SENSOR
foundation1 @graphite SENSOR room-for-more?
IF conveyor1 1 CONTROL-ENABLED ELSE conveyor1 0 CONTROL-ENABLED THEN

\ --- pair 2: metaglass → conveyor2 ---
foundation1 @itemCapacity SENSOR
foundation1 @metaglass SENSOR room-for-more?
IF conveyor2 1 CONTROL-ENABLED ELSE conveyor2 0 CONTROL-ENABLED THEN

\ --- pair 3: silicon → conveyor3 ---
foundation1 @itemCapacity SENSOR
foundation1 @silicon SENSOR room-for-more?
IF conveyor3 1 CONTROL-ENABLED ELSE conveyor3 0 CONTROL-ENABLED THEN

\ --- pair 4: plastanium → conveyor4 ---
foundation1 @itemCapacity SENSOR
foundation1 @plastanium SENSOR room-for-more?
IF conveyor4 1 CONTROL-ENABLED ELSE conveyor4 0 CONTROL-ENABLED THEN

\ --- pair 5: thorium → conveyor5 ---
foundation1 @itemCapacity SENSOR
foundation1 @thorium SENSOR room-for-more?
IF conveyor5 1 CONTROL-ENABLED ELSE conveyor5 0 CONTROL-ENABLED THEN
