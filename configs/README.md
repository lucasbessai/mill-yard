# Configuration library

| Directory | Reusable input |
| --- | --- |
| [yards/](yards/) | Fixed asset positions, capacities, equipment, and mill rates |
| [truck_schedules/](truck_schedules/) | Arrivals, area assignments, and default horizon |
| [initial_conditions/](initial_conditions/) | Starting inventories and loader positions |
| [experiments/](experiments/) | Small presets referencing those three files |
| [policies/](policies/) | Dispatch feature settings and weights |
| [optimization/](optimization/) | Trainable/fixed weight profiles |
| [archive/](archive/) | Retired demonstrations, kept for historical regression |

Every split input declares `kind` and `schema_version: 1`. Experiment paths are
relative to the experiment file, not the working directory. Policy selection
remains a separate CLI/GUI choice so a preset can be compared across policies.

## Naming convention

Files use lowercase snake case, with `__` separating identity from variants.
Family prefixes begin with area count so related configurations sort together.

| Input | Convention | Example |
| --- | --- | --- |
| Yard | `<family>__<asset_counts>[_mixed].yaml` | `1area_shared__2bays_1pile_1mill_2loaders.yaml` |
| Truck schedule | `<family>__<NNN>trucks_<pattern>_<MMM>min.yaml` | `1area_shared__015trucks_uniform_180min.yaml` |
| Initial conditions | `<exact_yard_stem>__<stock_condition>.yaml` | `1area_shared__2bays_1pile_1mill_2loaders__partial_stock.yaml` |
| Experiment | `<family>__<workload>[_fleet]_<MMM>min.yaml` | `1area_shared__015trucks_2loaders_180min.yaml` |
| Feature-policy profile | `feature_scored__v<version>_<N>weights.yaml` | `feature_scored__v4_5weights.yaml` |
| Optimization weight space | `feature_scored__<N>trainable_weights.yaml` | `feature_scored__4trainable_weights.yaml` |

Truck counts and horizons use three digits for consistent sorting. Horizons are
in minutes, not the last-arrival time. Yard counts describe the baseline design;
`mixed` means loaders have different characteristics. Initial-condition suffixes
describe stock only; loader starting positions are also specified inside the file.

Schedules identify a compatible **family**, not a particular loader count: all
three `1area_shared` schedules can be reused with either fleet. Initial conditions
identify the **exact yard variant** because their loader IDs and starting positions
must match. Names aid discovery; the actual IDs, capacities, and routing are still
validated by composition. Filename similarity is not a compatibility guarantee.
Optimization candidates should be built in memory rather than saved as a new YAML
file for every evaluation.

## Active families

| Family | Yard variants | Purpose |
| --- | --- | --- |
| `1area_shared` | 2 bays, 1 pile, 1 mill; 1 or 2 identical loaders | Six traffic comparisons: 3, 15, or 45 trucks |
| `1area_multi_mill` | 2 bays, 2 piles, 2 mills; 2 different loaders | Multi-infeed regression under `experiments/validation/` |
| `2areas_travel` | 3 bays, 2 piles, 1 inactive mill; 1 loader | Isolate travel and queue-switching behavior |
| `2areas_uneven` | 3 bays, 2 piles, 1 mill; 2 different loaders | Unequal bay counts and North-heavy arrivals |
| `3areas_compact` | 4 bays, 2 piles, 1 mill; 2 loaders | Full storage and initially empty infeed |
| `3areas_central` | 6 bays, 4 central piles, 1 mill; 3 loaders | Eight-hour, long-travel workload with arrival waves |

For a quick start, use
[the three-truck, one-loader preset](experiments/traffic/1area_shared__003trucks_1loader_180min.yaml).
Experiments under `traffic/` and `queues/` are the active comparison collections;
`validation/` retains useful model coverage without adding it to those batches.

For the eight-hour, three-area yard, open
[experiments/queues/3areas_central__042trucks_waves_480min.yaml](experiments/queues/3areas_central__042trucks_waves_480min.yaml).
Its separate inputs are [yards/3areas_central__6bays_4piles_1mill_3loaders.yaml](yards/3areas_central__6bays_4piles_1mill_3loaders.yaml),
[truck_schedules/3areas_central__042trucks_waves_480min.yaml](truck_schedules/3areas_central__042trucks_waves_480min.yaml), and
[initial_conditions/3areas_central__6bays_4piles_1mill_3loaders__partial_stock.yaml](initial_conditions/3areas_central__6bays_4piles_1mill_3loaders__partial_stock.yaml).

The traffic experiments share three schedules across the one- and two-loader
yards. Create another combination by referencing existing components; copying all
assets or truck arrivals into a new combined scenario is unnecessary.

## Retired and legacy inputs

The original generic `example` case is retired from the active library. Its
14-hour horizon with only three trucks is less useful as a general demonstration
than the shorter traffic cases. Its complete input set remains self-contained in
[archive/legacy_single_bay_14h/](archive/legacy_single_bay_14h/) for historical and
single-bay regression checks. It is not included in automatic comparison batches.

The former `multi_yard` case remains active as the explicitly named two-mill
validation preset; it is the only bundled case exercising multiple infeeds.
The six traffic and four queue experiments remain because they test different
loads, concurrency, storage constraints, and routing situations.

Version-2 and version-3 feature settings moved to [policies/legacy/](policies/legacy/).
Current profiles remain directly under `policies/`. The older formulas are still
available; this cleanup changes paths and organization, not parameters or model
behavior. The comparison script's `--include-legacy` / `--include-version-3`
options explicitly select these older profiles.

| Previous name | Current family/location |
| --- | --- |
| `example` | `archive/legacy_single_bay_14h/` |
| `multi_yard` | `1area_multi_mill`, `experiments/validation/` |
| `traffic_*` | `1area_shared`, `experiments/traffic/` |
| `alternating` | `2areas_travel`, `experiments/queues/` |
| `unbalanced` | `2areas_uneven`, `experiments/queues/` |
| `full_piles` | `3areas_compact`, `experiments/queues/` |
| `central_piles` | `3areas_central`, `experiments/queues/` |

There are 11 active experiments and one archived historical experiment. Old paths
are not kept as duplicate aliases. Saved runs retain their resolved scenario even
if the recorded source paths predate this rename.

See [configuration rules, APIs, and GUI controls](../docs/configuration.md).
