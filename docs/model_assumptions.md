# Current model contract and limits

The model is a process-oriented SimPy simulation with centralized dispatch.
Specification types live in `model/scenario.py`, physical state in `model/state.py`,
and shared deterministic timing in `model/timing.py`. Consumption and loader phases
have separate execution components. These choices are the present model contract,
not calibration claims about a particular mill.

Independent yard definitions, truck schedules, and initial conditions live in
`model/configuration.py`. Their pure composition validates routing, stock limits,
and loader starts, then produces the existing resolved `YardScenario`. YAML
adapters and optional experiment manifests are in `config/loader.py`; the engine
does not read input files. Schedules own the default horizon, with explicit run
overrides. See [input ownership and compatibility](configuration.md).

Truck admission is implemented separately in `simulation/trucks.py`. Each
unloading area owns a FIFO waiting list and one or more bays. Trucks use only bays
in their configured area; available bays use scenario order. Legacy scenarios
default to one `main` area. Queue anchors affect visualization only; movement from
arrival to a bay is instantaneous. Area membership does not restrict loaders,
piles, or infeeds. See [the area and queue policy contract](queue_policies.md).

## Material and activity

- One homogeneous material is measured in tonnes. Pile and loader capacities are
  mass limits; there is no density, species, grade, quality, or age tracking.
- Movement is truck-to-pile or pile-to-infeed. Direct truck-to-infeed delivery,
  pile relocation, loader breakdowns, operating shifts, and weighing are absent.
- Sources retain material until pickup completes; loaders then carry it until
  deposit completes. Both phases use fixed rates. Jobs reserve source material
  and destination space before travel and are not redirected after commitment.
- All loaders can reach all assets using straight-line routes and fixed speeds.
  There are no road networks, truck travel times, compatibility restrictions,
  fixed setup times, or separate loaded/empty speeds.
- Access limits apply during handling. SimPy maintains FIFO access queues; state
  snapshots expose their users and ordered waiters. Handling delay is executed
  exactly, but the scored policy's timing predictions do not forecast queue waits.

## Consumption and decisions

Each infeed consumes independently at a fixed rate. Unmet demand is lost rather
than backlogged. Consumption is integrated analytically between physical events.
Boundary callbacks with unchanged crossing times are reused. Replaced callbacks
can remain in SimPy's calendar until their obsolete times, but cannot act.

Dispatch follows same-time physical updates and truck admissions. Starvation status
is evaluated after those updates, so simultaneous depletion and replenishment do
not create a positive interruption or an unmatched starvation event. Zero-rate
infeeds never enter starvation. Fixed-interval decision wake-ups remain the default;
policy mode honors only the inventory crossings explicitly requested by the policy.
Interval size is part of experimental design and should receive sensitivity analysis.

Policies see equipment settings and current physical state, not future arrivals.
Threshold hysteresis is policy memory. The random baseline records its effective
seed even when no seed was supplied. Use fresh policy instances for independent runs.

The scored policy evaluates complete sets against a common original observation
and prediction horizon. Its estimates account for delivery times and penalize travel
and delay. Canonical ordering removes YAML loader-order dependence. Its search still
uses a finite, maximal-quantity candidate set; it is not a globally optimal scheduler
and grows exponentially for larger fleets.

The separate `feature-scored` policy searches complete feasible assignment sets
using five normalized weights and bounded per-assignment truck, mill, inventory,
cost, and bay-release features. Joint scores combine net pile changes and chronological delivery benefits
against the same original observation. The search includes idling and uses maximal
feasible batches in canonical loader order; it is exponential and does not search
arbitrary continuous quantity allocations or future decisions. It uses constant current demand, reservation-adjusted pile inventories,
capacity-share pile targets, and approximate committed delivery times. Mill credit
is marginal avoided starvation within a common horizon and falls with lateness;
slower completion is not rewarded. Cost coefficients and safety/lead-time settings
are fixed policy-feature assumptions, not measured complete-shift yard costs.
See [the detailed proposal review](feature_scored_policy.md) for formulas, deviations,
default assumptions, and model capabilities that remain outside this policy's scope.

Feature version 3 changes truck urgency to smooth individual waiting and local
unreserved truck count. Its fifth feature forecasts marginal bay-admission wait
saved by the complete pickup set, relative to existing commitments, within a fixed
window. It uses final pickup completion, excludes future handling access delays,
and assumes admitted queued trucks keep their bays for the rest of the window.
Future arrivals and future dispatches are not forecast. Release weight defaults to
zero; an example five-weight configuration enables it. Version 2 remains explicitly
selectable for legacy clipped/global-queue behavior. Named optimization profiles
separate trainable and fixed weights without changing the physical model.

Current feature version 4 adds the maximum accumulated nonservice wait of
unreserved trucks in the selected area, including queued trucks and bay occupants.
It combines this with individual wait and local backlog inside truck urgency;
effective defaults are 60/20/20 percent. Fully reserved trucks are excluded from
area urgency. `truck_area_wait_share` and `area_wait_reference` configure this
soft fairness preference. Version 3 remains available unchanged, and zero area
share in version 4 reproduces it. Bay release and the five top-level weights are
unchanged; record schema 4 remains separate from feature-definition version 4.

## Horizon, metrics, and records

Physical events at the horizon are processed, but no new jobs start there. Trucks
leave a stop when final pickup finishes; carrying their wood to a pile remains
loader work. Truck wait is elapsed presence minus the union of pickup intervals.
Completed waits and unfinished waits accrued so far are separate, censored measures.
Onboard material and outstanding jobs must also enter any future terminal penalty.
Outstanding job duration estimates exclude access-slot delay; no clearing/overtime
simulation or preferred optimization objective is defined yet.

`evaluations/metrics.py` computes metrics independently of file output. JSONL event
records use a versioned envelope, with optional detached state snapshots. Saved CLI
runs stream events during execution. Python callers can stream with `events_path`
and disable retention using `retain_events=False`. `--validate` enables transition
checks for conservation, reservations, capacities, and occupancy.

Snapshots include access queues, phase completion times, arrival position, effective
consumption boundaries, and policy memory at decisions. They support inspection and
future checkpoint design, but there is no runtime restore/branch implementation.
Snapshots at individual physical events can be intermediate states; settled decision
observations are the appropriate comparison points. Pending SimPy callbacks are not
fully serialized. No full Markov-state or checkpoint claim is made.

Future extensions should add explicit material compatibility, demand/supply process
interfaces, shared downstream constraints, and a tested checkpoint contract when those
research questions arise. They should not be encoded as incidental policy rules.

## Operator-style FIFO baseline

`fifo` prioritizes low infeeds, then full-pile relief, then the oldest stopped truck
with unreserved material. Later trucks can be assigned while an older truck's
fully reserved pickups are still executing. For each request it chooses the
nearest eligible idle loader, then favors a larger transferable quantity over
pile proximity. It does not preempt jobs or search joint assignments.

Its own fractional trigger is 25%, independent of the scenario's threshold-policy
settings. The default refill target is 65%, increased for two estimated repeat
delivery cycles but capped at 75%. Hysteresis preserves refill activity above the
trigger; fuller loads may overshoot the target within that ceiling. Separate
full-pile relief may use physical infeed capacity above 75% to open storage.
Incoming and outgoing reservations determine projected pile fullness; actual
pickup must still finish before truck deliveries can use newly freed capacity.
Zero-rate infeeds do not receive replenishment. Cycle estimates exclude access
queues and cannot guarantee starvation avoidance. This is a competitive fixed-rule
baseline with no artificial delays or performance penalties. See
[the FIFO policy specification](fifo_policy.md) for configuration and validation.

`empty-queue-first` and `longest-queue-first` reuse these priorities and quantity
rules, replacing only truck selection. Queue work counts queued and stopped trucks
with unreserved material, not tonnes. Exhaustive service has per-loader area
memory; longest-queue-first recomputes counts after each reservation and uses
deterministic wait/arrival/area-ID ties. Neither policy searches joint decisions.
An area with continuing arrivals can postpone other areas under exhaustive service;
there is no maximum-wait override. Original `scored` and feature version 2 retain
aggregate queue pressure; versions 3 and 4 use local backlog, with area waiting
added in version 4. All policies use the same area admission and physical constraints.

## Interactive execution

`YardSimulation.start()` initializes a run and `step()` processes the next simulation
timestamp, including same-time events. Batch `run()` uses the same stepping path.
A `HumanControlledPolicy` can return a `DecisionRequest` instead of assignments;
the engine exposes it as `pending_decision` and does not advance again until
`submit_decision()` validates a complete assignment set. Invalid submissions leave
the state and request unchanged. Terminal input uses `TerminalHumanPolicy`; the
desktop controller uses nonblocking requests so the window remains responsive.

`gui/session.py` controls playback without depending on Tk. `gui/app.py` supplies the
desktop widgets and canvas. Interpolated loader positions and infeed values in
`gui/presentation.py` are display projections and never mutate physical state.
Operator control selects complete transfer jobs, not individual driving or handling
phases. Source/destination handling limits and FIFO access queues remain enforced.

The right arrow advances one timestamp; the left arrow does not restore past state.
Saved GUI sessions are marked `partial` when stopped before the horizon. Uncommitted
draft assignments are excluded. GUI and terminal operator input are separate session
modes. Operator play is most convenient with the default fixed-interval wake-ups;
policy mode prompts at low inventory and, after waiting there, at depletion.

## Naming and output schema version 3

Policy methods receive a `context: PolicyContext`, distinct from the full scenario.
The scored-policy setting and CLI option previously named `min_score` and
`--min-score` are now `assignment_penalty` and `--assignment-penalty`: the value
is subtracted per assignment rather than defining a minimum total score.

The state/JSON field names are now explicit:

| Earlier field | Version 3 field |
| --- | --- |
| `InventoryState.incoming`, `outgoing` | `reserved_incoming`, `reserved_outgoing` |
| `TruckState.arrival`, `entered_at` | `arrived_at`, `entered_stop_at` |
| `TruckState.remaining`, `reserved` | `remaining_quantity`, `reserved_quantity` |
| `TruckState.handling_time` | `pickup_service_time` |
| `LoaderState.phase_end`, `travel_started` | `phase_completes_at`, `travel_started_at` |
| `LoaderState.distance`, `travel_time` | `completed_travel_distance`, `completed_travel_time` |
| `LoaderState.travel_distance` | `current_leg_distance` |

Per-loader summary entries retain the completed counters and add
`total_travel_distance` and `total_travel_time`, including progress along an
unfinished leg. The previous ambiguous summary keys `distance` and `travel_time`
are removed. Aggregate `loader_travel_distance_total` and `loader_travel_time_total`
keep their previous names and include that progress. Existing saved runs are not
rewritten; consumers should inspect `schema_version` before reading older fields.

## Output schema version 4: independent areas

Current output uses version 4. Snapshots replace the single waiting-list `queue`
with `queues`, mapping area IDs to ordered truck IDs, and add `TruckState.area_id`.
Scenario metadata includes normalized unloading areas and arrival/stop routing;
arrival, admission, and departure records expose area membership. Queue-policy
parameters and empty-queue-first preferences are recorded alongside existing
policy memory. Legacy saved runs are not rewritten.

Summary output retains its aggregate `queue` for readers and adds `queues` and
per-area metrics. The state property `queue` is a detached aggregate view; canonical
admission state is `queues`. Code constructing `YardState` must supply that mapping.
Maximum and p95 wait-so-far metrics combine completed waits with unfinished accrued
waits, globally and per area; they are censored at the horizon. They do not estimate
final waits or an overtime cost.
