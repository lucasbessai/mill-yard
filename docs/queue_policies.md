# Independent unloading areas and queue heuristics

An unloading area owns a FIFO waiting list and one or more truck stops (bays).
An arriving truck joins its configured area's list and enters the first free bay
in that area. It never moves into a free bay belonging to another area. Within an
area, simultaneous arrivals use truck ID to break ties and available bays use
their order in the scenario. Admission is separate from the loader dispatch policy.

## Configuration

Area and bay definitions belong to the yard file, alongside piles, infeeds,
and loader characteristics. This is a fragment of that file:

```yaml
kind: yard
schema_version: 1
unloading_areas:
  - {id: north, queue_location: {x: -20, y: 10}}
  - {id: south, queue_location: {x: 130, y: 0}}
stops:
  - {id: north-1, area_id: north, location: {x: 0, y: 0}, max_loaders: 2}
  - {id: north-2, area_id: north, location: {x: 0, y: 10}, max_loaders: 2}
  - {id: south-1, area_id: south, location: {x: 100, y: 0}, max_loaders: 2}
```

The independent truck schedule owns arrivals and the default horizon:

```yaml
kind: truck_schedule
schema_version: 1
horizon: 120
arrivals:
  - {id: n0, area_id: north, time: 0, quantity: 10}
  - {id: s0, area_id: south, time: 1, quantity: 20}
```

Each area must have at least one stop; every stop and arrival must reference a
known area. Area IDs must be unique. `max_loaders` limits concurrent pickup handling
at one stop; adding stop entries increases the number of trucks that can occupy
the area. Piles and infeeds remain shared yard assets.

Legacy combined YAML still loads. Omitting `unloading_areas` creates `main`.
In split schedules, omitted arrival areas resolve only when the yard has one
area; multi-area schedules must specify valid `area_id` values. The optional `queue_location`
is a display anchor, defaulting to the centroid of that area's stops. It introduces
no truck travel time. All loaders can still reach all areas; truck routing,
species restrictions, bay compatibility, and a road network are future extensions.

Complete examples are in [`configs/experiments/queues/`](../configs/experiments/queues/).
These presets reference reusable yard, schedule, and initial-condition files;
see [composition and compatibility rules](configuration.md).

[`3areas_central__042trucks_waves_480min.yaml`](../configs/experiments/queues/3areas_central__042trucks_waves_480min.yaml) is a longer, eight-hour
scenario with a 480-minute schedule: North, South, and East each have two bays, four shared
250-tonne piles occupy the yard centre, and one mill is on the west side. Three
20-tonne loaders move between these separated locations. Forty-two 30-tonne trucks
arrive in six waves through minute 405, leaving 75 minutes after the last arrival.
The mill consumes 1.25 tonnes/minute from a 120-tonne infeed. These parameters are
illustrative. To open it with full state recording:

```powershell
uv run --extra gui mill-yard gui configs/experiments/queues/3areas_central__042trucks_waves_480min.yaml --backend arcade --policy longest-queue-first --record snapshots --validate
```

## Common operator priorities

`fifo`, `empty-queue-first`, and `longest-queue-first` share the existing
[operator-style FIFO machinery](fifo_policy.md):

1. Replenish low infeeds, with hysteresis and the configured refill target.
2. Relieve full piles when a feasible infeed transfer is available.
3. Unload trucks using the selected queue rule.

The queue rule selects a truck/loader pair. For that pair, transferable quantity
takes priority over pile proximity. These policies reserve assignments sequentially
on a temporary state, recomputing feasibility and queue work after each assignment;
the engine validates and commits the resulting assignment set together. Jobs already
executing are never preempted. Mill priorities can interrupt a loader's truck work.

A waiting list contains only trucks awaiting a bay. For queue selection, **queue
work** counts all trucks in an area, including occupied bays, with positive
`remaining_quantity - reserved_quantity`. Each truck counts once, regardless of
tonnage. A fully reserved truck contributes zero work even while pickup is still
executing. This permits parallel unloading without waiting for physical departure.
Queued trucks affect priority but cannot be picked up until admitted to a stop.

Within a selected area, unload the oldest stopped truck with unreserved material
(arrival time, then truck ID). Do not skip a blocked head truck to service a later
truck in that area. The two area policies may choose another serviceable area.

### Global FIFO

`fifo` retains its original global arrival order among stopped trucks with
unreserved material. It selects the nearest eligible idle loader, breaking equal
distances by loader ID. A blocked global head prevents later truck unloading at
that decision, while feasible mill work can still proceed.

### Empty-queue-first

`empty-queue-first` gives each loader a remembered preferred area. Continue serving
that area until no unreserved truck work remains there. New arrivals join the same
workload while it is being served; there is no frozen batch of trucks. Clear an
affinity as soon as all remaining work is reserved, rather than waiting for the last
pickup to complete.

Without a preference, choose the serviceable area containing the truck with the
longest accumulated nonservice wait, then the oldest arrival, then area ID. If a
preferred area is temporarily blocked (for example, its bays have not yet cleared),
allow useful work elsewhere while retaining the preference. A mill task also retains
the preference while that area has unreserved work.

Each eligible idle loader proposes a truck using its own preference. Select among
these proposals by truck arrival time, loader-to-truck distance, loader ID, and
truck ID. Thus preferences are independent, not an exclusive area lock. Multiple
loaders may serve one area subject to ordinary material and access limits.

### Longest-queue-first

`longest-queue-first` chooses among serviceable areas by:

1. Largest queue-work truck count.
2. Largest accumulated nonservice wait of any unassigned truck in that area.
3. Earliest arrival among its unassigned trucks.
4. Lexicographically smallest area ID.

Then select the area's FIFO head and its nearest eligible idle loader, with loader
ID breaking equal-distance ties. Recompute these priorities after every reservation,
so a second loader can select a different area at the same decision timestamp.
There is no random tie-break or dependence on the supplied random seed.

Accumulated wait excludes the union of actual pickup-service intervals, including
an interval currently in progress. It is not simply age since arrival. Queue-work
counts and wait comparisons exclude fully reserved trucks.

## Relation to Beaudoin 2012

Section 4.1 of the [local paper](dev_materials/2012Beaudoin_DiscreteEventSimulationToImproveLogYardOperations.pdf)
describes loaders staying with a queue until it is empty, then choosing a queue by
truck wait. It also describes selecting the largest queue, with random ties. The
implementation adapts those allocation ideas to partial loads, reservations,
concurrent loaders, and this model's infeed/full-pile priorities. Longest-queue ties
are deterministic as specified above. These are adaptations, rather than a
reproduction of the paper's full yard or experimental results.

## Run and inspect

```powershell
uv run mill-yard run configs/experiments/queues/2areas_travel__006trucks_alternating_120min.yaml --policy empty-queue-first --validate
uv run mill-yard run configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml --policy longest-queue-first --save --validate
uv run mill-yard run configs/experiments/queues/3areas_compact__008trucks_storage_blocked_090min.yaml --policy longest-queue-first --fifo-config configs/policies/fifo.yaml
uv run --extra gui mill-yard gui configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml --backend arcade --policy empty-queue-first
uv run python scripts/compare_queues.py
```

All three operator policies accept `--fifo-config`; mill and pile parameters retain
their existing meanings. Both GUI policy menus offer the new policies. Tkinter's
area selector changes the displayed queue. Arcade shows two labelled area rows at
a time, each independently scrollable; **Areas up/down** or **Page Up/Down** pages
through additional areas. Map labels identify areas, and truck-wait breakdowns
include area membership. The human operator can choose feasible trucks in any area.

## State and records

- `UnloadingAreaSpec`, `StopSpec.area_id`, and `Arrival.area_id` define routing.
  Normalized scenario metadata includes the resolved default area and anchors.
- `YardState.queues` maps area IDs to ordered waiting lists. `TruckState.area_id`
  persists until departure and is included in the completed-truck record.
- `YardState.queue` remains a derived, globally arrival-sorted aggregate for legacy
  readers. It is a detached list: mutating it does not change admission. Python
  callers constructing state should use `queues={"main": [...]}`, not `queue=...`.
- Record schema **4** replaces the snapshot's single `queue` with `queues`, adds
  truck/arrival area fields and area metadata, and exposes queue-rule policy memory
  at decision snapshots. Existing saved files are not migrated. Summary output
  retains aggregate `queue` and adds `queues` and `unloading_areas` metrics.
- Per-area summaries include trucks awaiting bays, stopped trucks, unassigned work,
  completed/unfinished counts, accumulated wait, maximum wait, and 95th percentile.
  Global `truck_wait_so_far_max` and `truck_wait_so_far_p95` use completed waits plus
  unfinished waits accrued so far; the percentile uses linear interpolation.
  They are censored measures when trucks remain, not final wait percentiles.

Original `scored` and legacy feature version 2 retain aggregate queue pressure.
Feature version 3 explicitly switches to smooth local unreserved backlog and adds
a bay-release feature with an independent fifth weight. See the
[feature policy definition and optimization profiles](feature_scored_policy.md).
Current feature version 4 also includes the area's longest unreserved nonservice
wait inside truck urgency; previous versions remain explicitly selectable.
Saved area queues and policy preferences aid inspection; runtime checkpoint restore
and backward replay are still unimplemented.

## Verification and illustrative comparison

Tests cover isolated bay admission, multiple bays, same-time arrival/departure and
horizon events, invalid routing, invariant violations, per-area metrics, partial
reservations, deterministic wait/arrival/ID ties, independent loader preferences,
blocked fallback, mill-work interruption, policy metadata, and both GUI adapters.
Single-area cases preserve the existing queue behavior; these three operator rules
produce identical physical events on the single-area regression fixture.

`compare_queues.py` runs physical invariant checks for each of three policies in
four deterministic scenarios. These are illustrative configurations, without
parameter fitting:

| Scenario | Policy | Wait total (min) | Wait p95 so far (min) | Starvation (min) | Loader travel (m) | Unfinished trucks |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Alternating, 6 trucks / 1 loader / 2 areas | FIFO | 145.67 | 50.61 | 0.00 | 482.88 | 0 |
| Alternating | Empty-queue-first | 75.70 | 27.13 | 0.00 | 206.57 | 0 |
| Alternating | Longest-queue-first | 145.67 | 50.61 | 0.00 | 482.88 | 0 |
| Unbalanced, 12 trucks / 2 loaders / 2 areas | FIFO | 498.69 | 76.29 | 0.00 | 2006.76 | 0 |
| Unbalanced | Empty-queue-first | 552.72 | 88.38 | 0.00 | 2042.31 | 0 |
| Unbalanced | Longest-queue-first | 530.74 | 92.06 | 0.00 | 2242.02 | 0 |
| Full piles, 8 trucks / 2 loaders / 3 areas | FIFO | 483.34 | 87.71 | 7.88 | 2961.05 | 3 |
| Full piles | Empty-queue-first | 512.75 | 89.65 | 7.88 | 2965.18 | 4 |
| Full piles | Longest-queue-first | 493.36 | 88.07 | 7.88 | 2985.80 | 3 |
| Central piles, 42 trucks / 3 loaders / 3 areas / 480 min | FIFO | 4450.04 | 204.04 | 7.13 | 98220.64 | 15 |
| Central piles | Empty-queue-first | 4118.11 | 234.21 | 0.00 | 95688.72 | 13 |
| Central piles | Longest-queue-first | 4278.95 | 189.03 | 0.51 | 97129.55 | 14 |

The alternating scenario isolates queue switching with an inactive mill. The others
add active consumption and heterogeneous loaders; the full-pile scenario starts
with no infeed stock and saturated storage. Empty-queue-first reduces switching in
the alternating case but performs worse in the unbalanced case. No policy wins
universally. A stream that never empties an area can indefinitely delay other areas
under exhaustive service; an age cap is a possible future variant, not part of this
baseline. Evaluate final wait, unfinished work, and fairness together before making
optimization claims.

The central-pile scenario separates the mill and truck areas from storage by
hundreds of metres, making travel a significant constraint. Some trucks remain
unfinished at the end of this longer shift with all three operator policies;
its wait percentiles are therefore censored. Increasing the horizon does not
remove the need to account for unfinished work when comparing policies.
