# Proposal-based feature-scored dispatch

The proposal in this checkout is [GR_7_proposal.pdf](GR_7_proposal.pdf), dated
September 26, 2026. This implementation uses section 4's features, pages 18–23,
with the corrections below. It now searches complete feasible assignment sets
instead of the proposal's sequential assignment rule. The existing `ScoredPolicy`
and its `scored` CLI name remain available with the same behavior.

Current **feature version 4** adds area waiting urgency inside the truck feature.
It retains version 3's smooth individual waiting/local backlog and independent
bay-release feature. Versions 2 and 3 remain selectable for reproducibility.
Record schema 4 is a
separate version: it stores area membership and ordered queues. See
[unloading areas and queue policies](queue_policies.md).

The proposal's optimization variables are its four dispatch weights; the current
extension adds an independently tunable fifth weight. These are not the
Pareto objective preferences used to compare complete runs. A feature is a
state-action measurement: it depends on the observed yard and the candidate
loader's transfer, quantity, route, and timing. It is not an additional physical
state variable and does not change `YardState`.

## Dispatch rule

`FeatureScoredPolicy` implements

```text
score = θT * truck + θM * mill + θI * inventory - θC * cost + θR * bay_release
```

All five per-assignment features lie in `[0, 1]`. Weights must be finite,
nonnegative, and sum to one. Vector order is **truck, mill, inventory, cost,
bay_release**: R is appended to preserve the old four positional arguments.
Default weights are `(0.25, 0.40, 0.15, 0.20, 0)`, leaving R disabled for a
four-weight comparison. `feature_scored__v4_5weights.yaml` uses the illustrative, unoptimized
values `(0.20, 0.35, 0.10, 0.15, 0.20)`. Five trainable weights have four independent
degrees of freedom; fixing R to zero reduces this to three.

At each decision the policy searches a tree of complete assignment sets:

1. Sort the idle loaders by ID for deterministic candidate generation.
2. For each loader, explore leaving it idle and each currently feasible transfer.
3. Reserve material/capacity on a separate state for each branch. This rejects
   incompatible combinations and determines remaining feasible batch quantities.
4. At a leaf, score the entire set against the **original observation**, using
   combined inventory changes and delivery timelines.
5. Select the best positive-scoring set and return it for atomic engine validation.

A reservation made while exploring a branch is not a committed dispatch choice.
Every generated complete set is compared before any live loader starts work, and
simulated time does not advance during the search. Single-loader subsets and all
loaders idling are included. The dispatcher can therefore reject the highest
individual task score when a different combination has a greater joint score.

The candidate set uses the current DES's maximal feasible loader quantities,
including smaller remainder/capacity-limited loads, recomputed after earlier loader
reservations within each branch. Loader order is canonical, not a searched variable.
It does not optimize a continuous transfer quantity or impose the existing scored policy's additional
infeed-target cap; the proposal uses physical infeed capacity and mill urgency.

Scores within the model's numerical tolerance are tied. Ties use total empty plus
loaded travel distance across the set, then fewer assigned loaders, then canonical
loader/source/destination IDs and quantities. IDs make the last tie deterministic
without relying on YAML ordering. Zero or negative best
scores select idle. The policy schedules no SimPy events itself: existing arrivals,
completion/depletion events, fixed-interval reviews, and optional low-inventory
crossings create later decisions.

Fixed-interval review is the default and is recommended for this policy because
truck wait and mill urgency evolve continuously. In policy-crossing mode, a loader
that idles has no additional timer solely for increasing truck wait: with no future
physical event or crossing it can remain idle through the horizon. No zero-time
polling or silent override of the requested wake mode is introduced.

The search is exhaustive within this finite candidate space, not over every
continuous material allocation or future sequence of dispatches. With `n` idle
loaders and roughly `k` choices each, there can be `(k + 1)^n` branches before
feasibility pruning. It is suitable for the current small fleets; it does not
guarantee the best shift performance or exact predictions of handling-slot waits.

### Example: complete-set search versus sequential assignment

Suppose two loaders, A and B, can either unload a truck or refill the mill infeed.
There is one full batch available for each task and enough destination capacity
for both tasks together. Two loaders cannot both take that truck batch or both
take the refill batch. For illustration, assume independent benefits so that a
joint score is the sum of these individual scores:

| Loader | Unload truck | Refill infeed |
| --- | ---: | ---: |
| A | **0.40** | 0.35 |
| B | 0.30 | 0.05 |

Sequential assignment with re-evaluation picks A → truck first because 0.40 is
the highest individual score. The truck batch is now reserved, leaving B → infeed:

```text
A → truck   0.40
B → infeed  0.05
Total       0.45
```

Complete-set search compares both compatible combinations before committing:

| Assignment set | Total score |
| --- | ---: |
| A → truck, B → infeed | 0.45 |
| **A → infeed, B → truck** | **0.65** |

It selects the second combination. A is slightly better at unloading the truck,
but much better at refilling the infeed. Assigning A to the truck spends its
availability where B could have performed nearly as well. The search also checks
single-loader assignments and the all-idle set; none beats 0.65 in this example.

These numbers illustrate the assignment methods, not measured scores from a
scenario. The production policy uses the joint feature aggregation below because
delivery and pile-inventory benefits are not generally independent. A regression
test reproduces the 0.45 versus 0.65 example with independent illustrative scores.

### How joint features are combined

All candidates are evaluated against the same original observation:

- Truck benefits add, with quantities divided by each truck's physical remaining
  quantity. Feasibility ensures several loaders cannot claim the same material.
- Bay-release benefit compares the complete pickup set with existing commitments,
  counting each admission opportunity once. Two partial pickups can jointly release
  a bay even when neither would release it alone.
- Task cost features add, including a cost for every assigned loader's transfer.
- For each pile, apply **all** selected incoming and outgoing quantities together
  before measuring target-deviation improvement. Unloading and refilling that
  cancel in volume cannot claim a net inventory improvement.
- For each infeed, start with the original committed-delivery timeline. Add the
  proposed deliveries in predicted arrival-time order, grouping simultaneous
  arrivals. Each arrival group receives only its additional starvation reduction,
  weighted by its timeliness relative to the original stockout estimate. Two
  deliveries cannot both claim the same avoided starvation interval.

The joint score is:

```text
joint_score = θT * truck_total + θM * mill_total
              + θI * inventory_total - θC * cost_total + θR * bay_release_total
```

Normalized contributions are summed
across entities; joint totals can exceed one. They are not clipped again, which
would hide the benefit/cost of serving multiple independent assets. Joint scores
are independent of the order of assignments in the supplied list. For a singleton
set, the joint score equals the existing individual pair score.

## Features and corrections

### Truck urgency — equations 47–48

For version 4, truck-origin tasks receive

```text
fraction = quantity / physical truck quantity remaining
individual = wait / (wait + Wref)
backlog = local_backlog / (local_backlog + Nref)
area_wait = longest_area_wait / (longest_area_wait + Aref)
truck = fraction * [(1 - γ) * (α * individual + (1 - α) * backlog)
                    + γ * area_wait]
```

Other tasks receive zero. `local_backlog` counts trucks in the selected truck's
own area, queued or stopped, with positive unreserved material. A fully reserved
truck does not attract further work. Backlog counts trucks, not tonnes, and is not
divided by the area's bay count. The smooth normalization retains distinctions
above the old 30-minute wait/five-truck cutoffs.

`longest_area_wait` is the maximum accumulated **nonservice** wait among trucks
with unreserved material in the selected truck's area. It includes queued trucks
and bay occupants, ignores fully reserved trucks, and is zero if no eligible work
remains. It is not total/mean waiting or simply the earliest arrival. Finished and
currently ongoing pickup-service intervals are excluded using the same wait
definition as the selected truck. Remote-area waiting does not affect the term.

The nested mix keeps the old individual/backlog split configurable while adding
one area share. The effective three shares always sum to one:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `truck_wait_share` (α) | 0.75 | Individual-wait share of the portion left after area urgency |
| `truck_area_wait_share` (γ) | 0.20 | Area maximum-wait share of the truck feature |
| `area_wait_reference` (Aref) | 30 min | Maximum area wait giving normalized value 0.5 |

The default effective coefficients are **0.60 individual wait, 0.20 backlog,
0.20 area waiting**. Shares must lie in `[0, 1]`, and Aref must be finite and
positive. Setting γ to zero exactly recovers version 3; setting it to one uses
only area waiting before the transferred-fraction factor. These are fixed feature
settings, separate from the five top-level theta weights; no sixth theta is added.

Area urgency can make unloading a bay occupant attractive because older trucks
are waiting behind the occupied bays. It remains a soft priority rather than a
maximum-wait guarantee. Bay release continues to measure the additional admission
opportunity created by completing pickups; it does not acquire historical-wait
weighting. The selected truck itself may be the area's longest-waiting truck.

Only stopped trucks generate feasible pickup tasks. `wait` follows current DES
metrics: elapsed presence minus the union of pickup-service intervals. The
proposal instead uses elapsed time since arrival and defines its objective as
waiting before first service. Those definitions differ once a truck is partially
unloaded; this implementation consistently uses the model's accumulated non-service
wait rather than changing existing metrics.

All stopped trucks may be candidates. FIFO admission within each area does not
impose a hard FIFO unloading rule on this scored policy.

The quantity fraction adapts the proposal's common fixed batch to heterogeneous
loader capacities and partial loads. It prevents a tiny pickup from receiving the
same full-truck benefit as a complete unload. The physical remaining amount includes
reserved material, so multiple pickups at the same decision do not each claim full
truck credit.

Version 3 uses only `α * individual + (1-α) * backlog`, ignoring the new area
parameters. Use `legacy/feature_scored__v3_4weights.yaml` or `legacy/feature_scored__v3_5weights.yaml` to
reproduce the previous default/example weights. Version 2 instead uses
`α * clip(wait/Wref) + (1-α) * clip(global_waiting/Nref)`,
where `global_waiting` counts trucks awaiting bays, excluding stopped trucks.
Choose `feature_version: 2` to reproduce that definition; R must be zero.

### Bay-release benefit — version 3 extension

Truck urgency rewards progress on the selected truck. Bay release rewards the
admission opportunity created for trucks waiting behind occupied bays. An area
with no queued trucks receives no release credit, including in a single-bay yard.

Use a common window `H = min(bay_time_reference, remaining run horizon)`. For
each bay, predict its next opening from the **last required pickup completion**.
Travel to the truck and pickup handling count; loaded travel and deposit do not.
Without sufficient committed/selected pickups to empty its truck, the bay's opening
is unknown, represented by infinity and capped at H. A free bay opens at time zero.

Compute this timeline twice: existing committed pickups alone, then those pickups
plus all selected assignments. For each area, sort bay-opening times and match
the first `min(number of bays, queued trucks)` to the current FIFO waiting list.
Each newly admitted truck occupies that bay for the rest of the forecast window:
there is no assumed future service, dispatch, or arrival. Existing free bays and
already fully reserved pickups form the baseline and cannot earn duplicate credit.

```text
saved_wait_area = sum(capped baseline admission times)
                  - sum(capped proposed admission times)
release_area = clip(saved_wait_area / (bay_time_reference * bay_queue_reference))
bay_release_total = sum(release_area across areas)
```

The default scales are 30 minutes and five trucks (150 truck-minutes). Each area
contribution is bounded by one; joint totals across areas may exceed one. Only
observed queued trucks receive credit, so opening two bays for one waiting truck
cannot claim two admission benefits. Two loaders completing one truck together
also release only one bay. Partially reserved trucks can receive credit when new
pickups complete their remaining work; their last running pickup may determine
the release time. Openings at/after H earn no benefit within that window.

Pickup predictions honor known phase completion times but exclude future access-slot
waits. They are optimistic timing estimates when a stop's handling slots are busy,
not a second SimPy simulation. This feature measures bounded admission wait saved,
not all downstream waiting or throughput. It can be useful with one or many bays.

### Mill urgency — equations 49–51

Committed deliveries use shared remaining-job timing for work already running at
the original decision observation. Individual pair features use the formula below;
complete sets use its chronologically aggregated marginal-benefit extension above.
For each candidate destination infeed:

```text
Tout = first stockout under current inventory and committed deliveries
u = clip(1 - Tout / Tref)
late = clip((candidate duration - Tout) / Tref)
H = min(Tref, remaining simulation horizon)
B = predicted starvation over H with existing deliveries
A = predicted starvation over H with those deliveries plus the candidate
benefit = clip((B - A) / Tref)
mill = benefit * [β * u + (1 - β) * (1 - late)]
```

The proposal adds positive lateness to urgency. That can reward a slower loader
for the same transfer, and its positive-part expression is not bounded by one.
Here lateness reduces timeliness, and all feature terms are bounded. For an
otherwise identical transfer, arriving later cannot increase mill benefit.

The additional marginal-benefit factor prevents duplicate credit when another
loader's delivery already covers demand. It also distinguishes a useful delivery
from one that cannot help before the planning/run horizon. It reuses the existing
pure `predicted_starvation` integrator; the existing policy is unchanged. Constant
demand is the current infeed processing rate. No future truck schedule is exposed
or forecast.

With zero demand, `Tout` is infinite and the feature is zero. A delivery exactly at
depletion prevents a positive starvation interval, matching same-time DES
settlement; a delivery after a currently empty infeed does not erase the initial
stockout. Estimates exclude future handling-slot queue delays, as do the existing
policy timing estimates. These are forecasts, not a second simulation or an
optimal scheduling calculation.

### Pile inventory balance — equations 52–55

Current multiple piles all hold one homogeneous material. Let `share` be a pile's
capacity divided by total pile capacity. The target and feature are

```text
target = min(pile capacity,
             share * [total infeed demand rate * lead time + yard safety stock])
projected = actual amount + reserved incoming - reserved outgoing
delta = +quantity for truck-to-pile; -quantity for pile-to-infeed
inventory = clip([abs(projected - target)
                  - abs(projected + delta - target)] / Z)
```

The proposal has one pile per species. Capacity-share allocation avoids duplicating
the entire yard's demand/safety stock target in every pile when there are multiple
piles of the same material. It is an explicit starting assumption, not a species
compatibility model.

Equation 52's `capacity - committed incoming` is an upper bound on *physical*
inventory. Combining that bound with a commitment-adjusted inventory would count
incoming material twice. Here the target is the desired eventual inventory,
capped at physical capacity, and commitments enter `projected` exactly once.
Feasibility still uses the engine's actual source/destination reservation checks.
Using the combined selected pile change avoids repeatedly rewarding
the same target deficit. Worsening a target deviation receives zero inventory
benefit, matching the proposal's clipped equation 55 rather than adding a new
negative feature.

### Operating cost — equations 56–57

```text
task_cost = distance_cost * travel_metres + active_time_cost * job_minutes
            + [truck-to-pile] * holding_cost * quantity * residence_time
cost = clip(task_cost / cost_reference)
```

Job duration includes empty travel, pickup, loaded travel, and deposition, using
the same deterministic timing functions as execution. Holding cost is an estimated
consequence of new pile stock. The fleet's fixed capital cost is constant within
one dispatch decision and is omitted, as in the proposal's local task score.
These coefficients are illustrative currency units, not measured dollar costs.
The policy does **not** implement the proposal's complete-shift active-time and
inventory-holding-cost objective or claim those costs are currently measured.

## Configuration and code organization

[`configs/policies/feature_scored__v4_4weights.yaml`](../configs/policies/feature_scored__v4_4weights.yaml)
lists every default. References and mixing coefficients are fixed feature
definitions, separate from the five named `FeatureWeights` optimization variables.
Quantities are tonnes, times minutes, and distances metres, preserving repository
units rather than introducing an undocumented conversion from the proposal's m³.
The initial scales/costs are logical placeholders; hold them fixed while comparing
weight vectors and calibrate them separately when data become available.

```bash
uv run mill-yard run configs/experiments/validation/1area_multi_mill__004trucks_mixed_stock_120min.yaml --policy feature-scored --validate
uv run mill-yard run configs/experiments/validation/1area_multi_mill__004trucks_mixed_stock_120min.yaml --policy feature-scored \
  --feature-config configs/policies/feature_scored__v4_4weights.yaml \
  --feature-weights 0.2 0.5 0.1 0.2 --save
uv run --extra gui mill-yard gui configs/experiments/validation/1area_multi_mill__004trucks_mixed_stock_120min.yaml --backend arcade --policy feature-scored
uv run --extra gui mill-yard gui configs/experiments/queues/3areas_central__042trucks_waves_480min.yaml --backend arcade --policy feature-scored \
  --feature-config configs/policies/feature_scored__v4_5weights.yaml --record snapshots --validate
uv run mill-yard run configs/archive/legacy_single_bay_14h/experiment.yaml --policy feature-scored \
  --feature-config configs/policies/legacy/feature_scored__v2_4weights.yaml --validate
```

`--feature-weights` accepts four or five values in truck, mill, inventory, cost,
bay_release order. Four values explicitly set R to zero; all supplied weights must
sum to one. `--feature-version 2`, `3`, or `4` overrides the definition version.
Omitting the version uses 4, including when loading YAML without a version field.
Request the earlier version explicitly to reproduce its definitions.
Settings meant for one scored policy are rejected when the other is selected.
Both desktop policy menus include the new policy. Switching between scored policy
families uses the other family's defaults and retains the original supplied
parameters when switching back. Saved metadata includes the five named weights, all
fixed feature settings, dispatch rule, and feature-definition version.

| File | Responsibility |
| --- | --- |
| `policies/features.py` | Immutable weights/feature settings, feature vector, stockout and reservation-aware feature extraction. |
| `policies/feature_scored.py` | Complete feasible assignment-set search, deterministic ties, idle behavior, metadata. |
| `policies/bay_release.py` | Common-observation pickup timelines and marginal bay-admission benefit. |
| `optimization/weight_space.py` | Named trainable/fixed weight adapter; no optimization algorithm. |
| `config/policy.py` | Strict YAML loading and validation. |
| `policies/factory.py`, `cli.py` | Named selection and parameter-family integration. |
| `scripts/compare_traffic.py` | Compare threshold, operator FIFO, existing scored, feature-scored, and seeded random policies. |

For Python optimization, construct `FeatureWeights` and `FeatureScoredParameters`
directly. `policy.features(assignment, state, context).as_vector()` exposes the
five values in T, M, I, C, R order; `.score(weights)` includes the release term. Feature
evaluation does not change the supplied state.

`policy.joint_features(assignments, state, context)` exposes the five aggregated
totals, and `policy.score_joint(...)` evaluates the set. Saved metadata now records
`feature_version: 4` by default, `dispatch_rule: complete_feasible_sets`, and the canonical
maximal-batch candidate rule. Earlier version-1 runs describe the former sequential
dispatcher and are not rewritten.

The search caches pair features and the original bay forecast only for the current
decision. Repeated net pile changes, infeed delivery plans, and pickup sets also
share feature calculations within that observation. Reservation branches copy just
the mutable reservation objects they change;
the original observation remains untouched. These changes reduce repeated work
without pruning assignment sets or changing the score/tie rules. The search is
still exponential and can pause a GUI decision on larger fleets/choice sets.

### Trainable versus fixed weights

Optimization profiles are separate from runtime policy YAML. They fix the parameter
space for an experiment; feature values may become zero at individual observations
without changing that space. [`feature_scored__4trainable_weights.yaml`](../configs/optimization/feature_scored__4trainable_weights.yaml)
excludes R from the search vector, while
[`feature_scored__5trainable_weights.yaml`](../configs/optimization/feature_scored__5trainable_weights.yaml) trains all five:

```python
from mill_yard.config.policy import load_feature_weight_space
from mill_yard.policies.feature_scored import FeatureScoredParameters, FeatureScoredPolicy

space = load_feature_weight_space("configs/optimization/feature_scored__4trainable_weights.yaml")
assert space.trainable_weights == ("truck", "mill", "inventory", "cost")
weights = space.build_weights(dict(truck=0.25, mill=0.40, inventory=0.15, cost=0.20))
policy = FeatureScoredPolicy(FeatureScoredParameters(weights=weights))
assert weights.bay_release == 0
```

`from_vector(values)` uses the profile's explicit name order and rejects incorrect
dimensions. Values are not silently normalized. The trainable weights must sum to
`1 - sum(fixed_weights)`, so a nonzero fixed weight is supported too. Profile
validation rejects missing, duplicate, unknown, or overlapping weight names.
`space.metadata()` exposes the search-space definition for an experiment recorder.
This adapter provides no optimizer, training objective, or automatic profile switch
based on bay count. Use version 4 with R fixed at zero to isolate its effect while
keeping truck definitions identical; version 2 is a separate legacy comparison.

## Scope and initial verification

The proposal's wider model includes species-specific demand schedules, direct
truck-to-infeed transfers, fixed batches, exact yard costs, physical-design
optimization, and stochastic study stages. Current feasibility supports only
truck-to-pile and pile-to-infeed, variable batch quantities, homogeneous material,
and independent fixed-rate infeeds. The new policy respects those physical rules
rather than adding unexecutable tasks. Multiple infeeds remain separate mills,
not alternative species on one shared processing machine. Backlog, interruption,
rollouts, failures, ageing, and optimization algorithms remain separate work.

Tests cover the proposal's illustrative Table 7 arithmetic, weight/feature bounds,
zero demand, simultaneous depletion/delivery, slower-loader bias, committed
deliveries, repeated inventory credit, multi-pile target allocation, complete-set
search versus greedy assignment, order-independent joint scores, net pile changes,
canonical ties, idling, YAML/CLI metadata, GUI switching, and all
eight example/traffic scenarios with physical invariant checks.

The following historical results use **version 2**, reproduced through the legacy
configuration. With those untuned defaults, all six traffic scenarios have zero starvation.
Waiting includes completed and unfinished trucks, in minutes:

| Scenario | Existing scored wait | Feature-scored wait | Existing / feature unfinished trucks |
| --- | ---: | ---: | ---: |
| Light, 1 loader | 26.09 | 115.52 | 0 / 0 |
| Light, 2 loaders | 5.02 | 101.16 | 0 / 0 |
| Medium, 1 loader | 1158.78 | 1383.53 | 9 / 10 |
| Medium, 2 loaders | 187.25 | 669.18 | 0 / 1 |
| Heavy, 1 loader | 4758.78 | 4983.53 | 39 / 40 |
| Heavy, 2 loaders | 3780.08 | 4081.64 | 29 / 30 |

These are behavior checks, not evidence of superior performance or calibrated
economics. These figures use version 2's complete-set search. Normalization,
cost tradeoffs, clipping, and the finite candidate space can
favor idling or different tasks from the existing heuristic. The intended next
experiment is weight search on training scenarios with held-out verification;
the proposal's example weights should not be presented as an optimized policy.

Version 3 adds tests for local rather than global backlog, smooth long-wait
distinctions, zero/late bay opportunities, joint partial pickups, existing
commitments, bay-count/waiter limits, unchanged observations, cached/uncached score
equivalence, and fixed/trainable weight profiles. All eight legacy event streams
are checked against pre-change output using version 2.

To compare the current untuned four/five profiles, run:

```bash
uv run python scripts/compare_feature_profiles.py
uv run python scripts/compare_feature_profiles.py configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml --include-legacy
uv run python scripts/compare_feature_profiles.py configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml --include-version-3
```

Each run checks physical invariants and reports total wait (including unfinished
wait so far), starvation, travel, unfinished trucks, and wall-clock runtime. The
supplied profiles change several weights, so their difference is not an isolated
causal estimate of R. A controlled ablation should hold definitions fixed and use
declared training/evaluation budgets and held-out scenarios.

The earlier untuned **version-3** profiles produced these historical checks. They
remain reproducible using the explicit v3 profile files; the default files now
use version 4:

| Scenario | Four-weight wait (min) | Five-weight wait (min) | Four / five unfinished trucks | Four / five starvation (min) |
| --- | ---: | ---: | ---: | ---: |
| Alternating | 142.31 | 110.40 | 0 / 0 | 0 / 0 |
| Unbalanced | 748.44 | 654.13 | 0 / 0 | 0 / 0 |
| Full piles | 506.80 | 497.78 | 3 / 3 | 7.88 / 7.88 |
| Central piles, eight hours | 11670.00 | 11496.53 | 42 / 38 | 344.00 / 285.69 |

The long-route case exposes poor default tradeoffs: two-trip truck service can
cost more than its urgency/release credit, so feasible work is left idle. The
five-weight profile completes four trucks there, but still performs poorly. These
defaults are not suitable claims of competitive yard operation. Normalize/tune on
representative training scenarios and evaluate unfinished work explicitly. Each
central-pile run took roughly 90 seconds on the verification machine, illustrating
the cost of complete enumeration despite decision-local caching.

Version 4 tests additionally cover older queued trucks changing area priority,
locality, maximum rather than total wait, partial reservations, ongoing pickup
service, configurable sensitivity, bounded partial-load credit, input immutability,
cached scores, and exact version-3 equivalence at zero area share. Nineteen
pre-change event streams are reproduced through the earlier version settings.

The version-4 default/example wait totals were 142.31 / 110.40 minutes on
Alternating, 731.83 / 668.90 on Unbalanced, and 510.75 / 497.78 on Full piles.
Completed/unfinished counts and starvation matched the version-3 counts for these
three cases. This change is not uniformly beneficial under untuned weights; its
purpose is to expose the additional fairness signal for policy comparison/tuning.
