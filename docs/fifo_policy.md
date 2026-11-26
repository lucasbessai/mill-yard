# Operator-style FIFO baseline

`FifoPolicy` (`--policy fifo`) is a fixed-rule operator baseline. It implements
FIFO truck service with concurrent loaders, prioritizes mill supply and blocked
storage, and prefers fuller loads to shorter pile trips. It supplements the
existing threshold, scored, feature-scored, and random policies.

The defaults are chosen as reasonable operating rules, not selected to lose to an
optimized policy. Optimization must demonstrate improvement using the same model,
scenarios, metrics, and treatment of unfinished work. Default scored weights are
also not evidence of an optimized solution.

## Priority order

Each dispatch builds a reservation copy of the observation. After choosing a task,
it updates reservations and checks these priorities again:

1. **Low infeeds:** start a replenishment episode at or below 25% of physical
   capacity. Continue above that trigger until enough incoming material is booked
   to reach the refill target. Among active infeeds, serve the shortest current
   time to empty first.
2. **Full piles:** if no low-infeed transfer can be assigned, move material from
   full piles to a consuming infeed with free capacity. Among receiving infeeds,
   prefer the shortest time to empty. This can use space above the normal refill
   target to open storage for truck unloading.
3. **FIFO trucks:** choose the oldest stopped truck with positive unreserved
   material. Assign another loader to that truck while any unreserved remainder
   exists, even if that remainder cannot fill the loader. Only after all its
   material is reserved may later trucks receive assignments.

FIFO applies to assigning material, not requiring physical completion/departure
before the next truck starts. For example, if the oldest truck has 3 t left and
two 10-t loaders are idle, one takes its 3 t and the other can unload the next
truck. Trucks still waiting for a bay cannot be serviced directly. Arrival ties
use truck ID. A blocked oldest truck is not skipped for a younger truck.

Existing jobs are not interrupted. If no higher-priority work is feasible, the
policy can perform lower-priority work. If nothing is feasible, it idles until
the next physical event or dispatch review.

## Loader, quantity, and pile selection

For a chosen request, select the nearest **idle, feasible** loader to the receiving
infeed, or to the truck bay for truck unloading. This preserves the nearest-loader
rule; it is not a fleet-wide search for the largest loader or best joint assignment.

For that loader, rank feasible piles by:

1. Largest transferable quantity.
2. Shortest loaded travel leg: truck → pile or pile → infeed.
3. Shortest empty approach to pickup, then stable asset IDs.

Thus a nearby pile that can supply only 3 t loses to a farther pile that can supply
10 t to the same 10-t loader. Likewise, a farther destination with room for 10 t
beats a nearer destination with room for only 3 t. For a truck with only 3 t left,
both piles permit the same transfer and proximity breaks the tie. Quantities always
respect loader capacity, unreserved source stock, and unreserved destination space.

## Refill targets and full-pile relief

The normal target begins at 65% of capacity. To cover a few delivery cycles, the
policy estimates a repeat cycle for the nearest eligible loader and its preferred
stocked pile:

```text
cycle_minutes = 2 * distance(pile, infeed) / loader_speed
                + quantity / pickup_rate + quantity / deposit_rate
target = min(0.75 * infeed_capacity,
             max(0.65 * infeed_capacity,
                 2 * cycle_minutes * consumption_rate))
```

This uses a repeat round trip starting at the infeed, plus pickup/deposition, and
excludes handling-slot queue delays. If no refill is currently feasible, the target
uses the base 65% value. Targets are recomputed at each dispatch and held fixed
while assembling that decision's reservations. The two-cycle coverage is an
estimate, not a guarantee: finite capacity, travel, source shortages, and access
queues can still cause starvation.

Normal replenishment may overshoot the target to take a fuller load, up to the
75% ceiling. At 60% stock during an active refill, a full 10%-capacity load takes
the infeed to 70%, rather than shaving the load to exactly 65%. If only 5% fits
under the 75% ceiling, the load is limited to that amount.

Incoming reservations count toward the booked refill level but are never treated
as physically present stock. If they already cover the target, another loader is
not dispatched solely because the physical level is still low. Refilling is
hysteretic: once active it continues above 25%; after meeting the target it waits
for the next low trigger.

Full-pile relief is a separate exception to the normal 75% ceiling. It may use any
unreserved physical infeed capacity, even above 75%, to relieve blocked storage.
A pile is full when `amount + reserved_incoming - reserved_outgoing` reaches its
configured full fraction (100% by default). Reserving an outgoing load already
relieves that projected fullness, so the next loader need not keep draining it.
Truck capacity checks still wait for the actual pickup to free physical space;
promised outgoing material cannot make a full destination immediately usable.

Zero-consumption infeeds are excluded from replenishment and full-pile relief.
If all piles and infeeds are full, loaders wait for mill consumption to create
space. If piles are empty, available loaders can unload trucks to establish supply.
The policy does not invent direct truck-to-infeed transfers or pile relocation.

## Running and configuring

```powershell
uv run mill-yard run configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml --policy fifo --validate
uv run mill-yard run configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml --policy fifo --fifo-config configs/policies/fifo.yaml --save
uv run --extra gui mill-yard gui configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml --backend arcade --policy fifo
```

Both desktop policy menus include `fifo`. The optional flat YAML configuration
lists all fixed rules:

```yaml
low_fraction: 0.25
target_fraction: 0.65
max_refill_fraction: 0.75
buffer_cycles: 2.0
full_pile_fraction: 1.0
```

The low/target/ceiling fractions must satisfy
`0 <= low < target <= max_refill <= 1`; full-pile fraction is in `(0, 1]` and buffer
cycles is nonnegative. Setting `buffer_cycles: 0` gives a fixed target fraction.
These settings are independent of the scenario's threshold-policy `low` and
`target` amounts. Policy-crossing mode requests this policy's own 25% crossing.
Fixed-interval reviews remain the default. Saved metadata contains all effective
settings and a heuristic version; snapshots expose refill activity and targets.

`policies/fifo.py` contains the policy and parameter definitions; `config/policy.py`
loads the YAML strictly. The factory, CLI, and desktop adapters share that policy
implementation. The SimPy execution model is unchanged.

The yard now supports independent unloading areas, each with its own FIFO waiting
list and bays. This policy retains global FIFO among stopped trucks; bay admission
is local to an area. `empty-queue-first` and `longest-queue-first` reuse the mill,
full-pile, loader, and quantity rules above while replacing truck selection. They
accept the same `--fifo-config`. Queue selection is separated into
`policies/truck_dispatch.py`; physical admission lives in `simulation/trucks.py`.
Metadata includes `queue_rule`, and decision snapshots expose truck-selection
memory. See [area configuration, queue rules, and comparisons](queue_policies.md).

## Verification and comparison

Tests cover partial oldest-truck loads and parallel service of later trucks;
low-infeed versus full-pile versus truck priorities; fuller loads versus nearer
piles; hysteresis and target overshoot; cycle coverage and its ceiling; existing
reservations; busy loaders; inactive mills; full/empty yards; deterministic ties;
policy wake-ups; reproducibility; configuration, saved metadata, and GUI switching.
All eight example/traffic scenarios run with physical invariant checks.

The comparison script now evaluates all five automated policies. Initial results
with the fixed defaults have zero starvation for both FIFO and existing scored on
the six traffic scenarios. Truck waiting includes unfinished trucks' wait so far:

| Scenario | FIFO wait (min) | Existing scored wait (min) | FIFO / scored travel (m) |
| --- | ---: | ---: | ---: |
| Light, 1 loader | 31.09 | 26.09 | 2912.31 / 3312.31 |
| Light, 2 loaders | 5.02 | 5.02 | 2811.20 / 3411.20 |
| Medium, 1 loader | 1200.11 | 1158.78 | 4126.85 / 4025.19 |
| Medium, 2 loaders | 243.59 | 187.25 | 7767.13 / 7863.23 |
| Heavy, 1 loader | 4800.11 | 4758.78 | 4126.85 / 4025.19 |
| Heavy, 2 loaders | 3766.12 | 3780.08 | 8469.93 / 8769.39 |

FIFO is a competitive comparator, not an intentionally weakened one. Lower wait
does not always coincide with lower travel. Neither these scored defaults nor
the comparison script performs parameter optimization. Further claims should
use a declared objective, representative training scenarios, and held-out cases,
with consistent penalties for unfinished trucks and loader work at the horizon.
