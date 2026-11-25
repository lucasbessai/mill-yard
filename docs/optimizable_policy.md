# Optimizable dispatch policy

`policies/optimizable.py` implements the proposal's four-weight dispatch rule:

```text
score = theta_T * truck + theta_M * mill + theta_I * inventory - theta_C * cost
```

Supply four finite nonnegative weights in that order. The policy normalizes them
to sum to one and rejects an all-zero vector. It has no default theta. Scaling
all four inputs by the same positive factor therefore gives the same policy.

```python
from mill_yard.config.loader import load_yard_scenario
from mill_yard.optimization.objective import evaluate_objective
from mill_yard.policies.optimizable import FeatureParameters, OptimizablePolicy
from mill_yard.simulation.engine import run_yard

scenario = load_yard_scenario("configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml")
features = FeatureParameters()  # Keep these fixed across optimizer evaluations.
policy = OptimizablePolicy([0.25, 0.40, 0.15, 0.20], features)
result = run_yard(scenario, policy, record="summary")
score = evaluate_objective(result)
print(score.total)  # Lower is better.
```

The optimizer creates a fresh policy for each candidate theta, runs the same
scenario, and minimizes the objective total. Objective preferences and references
stay fixed across trials. The original scored policy and its CLI options continue
to work. See the differential evolution search command below.

The policy selects the highest positive-scoring loader-task pair, reserves its
material and capacity, then recomputes the remaining options. Ties prefer shorter
travel distance, then loader order in the scenario, then candidate order. Scores
within the simulator's numerical tolerance of zero idle. The policy does not
mutate the supplied observation. Simulation metadata retains normalized theta,
its ordering, and all fixed feature parameters.

Adaptations from proposal sections 4.1-4.5:

- The simulator currently supports a single material and truck-to-pile or
  pile-to-infeed transfers. Direct truck-to-infeed tasks are not generated.
- Transfers use maximal feasible quantities rather than fixed batches; infeed
  transfers can fill to physical capacity, as specified by feasibility.
- Truck urgency averages capped elapsed time since arrival and capped queue
  length. Elapsed time follows the proposal; objective waiting excludes handling.
- Mill urgency combines time to first stockout and estimated task lateness, with
  both beta coefficients equal to one. The lateness feature can exceed one.
  Stockout assumes constant demand. Incoming deliveries count only when an
  active loader is depositing with a known completion time or when a job was
  proposed during this decision. Handling queues are excluded from estimates.
  A delivery after the first depletion cannot erase that earlier stockout.
- Pile targets use demand over a fixed replenishment lead time plus safety stock,
  distributed among piles by capacity. Targets leave room for committed incoming
  material. Inventory benefit is the clipped reduction in target deviation,
  divided by pile capacity; committed outgoing stock is excluded when scoring
  later assignments in the same decision.
- Operating cost defaults to travel metres divided by a fixed reference. Optional
  active-time and estimated holding costs can be enabled through feature settings.
  These are local estimates; the current objective still measures travel, waiting,
  and shortfall rather than full monetary yard cost.
- Feature defaults are illustrative, not calibrated. The policy requests the same
  low-inventory wake levels as existing policies. Use the default interval wake
  mode for periodic reconsideration as wait and stockout urgency change.

With four normalized weights there are three independent degrees of freedom.
The optimizer accounts for that constraint rather than interpreting scaled
copies of a vector as different policies.

## Optimize dispatch weights

Install dependencies with `uv sync` and run from the repository root.

```bash
# OMADS poll, the default
PYTHONPATH=src uv run python scripts/optimize_policy.py configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml --method omads

# Differential evolution with its historical settings
PYTHONPATH=src uv run python scripts/optimize_policy.py configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml --method de

# Independent runs with the same scenario, seed, and simulation cap
PYTHONPATH=src uv run python scripts/optimize_policy.py configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml --method both --output runs/optimization/medium.json --plot runs/optimization/medium.png

# Full MADS with Latin hypercube search and poll
PYTHONPATH=src uv run python scripts/optimize_policy.py configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml --method omads --full-mads
```

Use `--initial-theta T M I C` to change the default baseline
`(0.25, 0.40, 0.15, 0.20)`. Both solvers use three bounded stick-breaking
coordinates in `[0, 1]` to cover the four-weight simplex, including its boundaries.

```text
theta_T = a
theta_M = (1-a) b
theta_I = (1-a) (1-b) c
theta_C = (1-a) (1-b) (1-c)
```

Objective settings include `--shortfall-weight 3` and `--reference-wait 120`.
Fixed feature settings use a `--feature-` prefix, for example
`--feature-stockout-reference 20`. Each candidate runs a fresh summary-mode
simulation unless its exact policy weights already have a score in that run's cache.

### Settings and stopping

The tested dependency is [OMADS 2610](https://pypi.org/project/OMADS/2610/).
Poll calls `OMADS.poll.main(data)` and full MADS calls `OMADS.mads.main(data)`.
Both use the documented evaluator, parameter, and option sections. Full MADS
adds `search={"s_method": "LH", "ns": 4}`. The evaluator returns
`[objective, [0]]`, with a zero constraint residual for this bounded problem.
The orthogonal mesh in this release needs `REAL` for continuous mesh projection.
Scaling is `1.0`, so frame sizes are expressed in units of the coordinate range.
See the [OMADS configuration reference](https://ahmed-bayoumy.github.io/OMADS/user_guide/configuration.html).

`OMADSParameters` defaults to a 200-evaluation allowance, a 0.2 initial frame
size, and a 0.0001 mesh/frame tolerance. These allow initial moves at a meaningful
fraction of the unit coordinate range and limit refinement of a locally flat
objective. It uses `ORTHO_2N`, rich directions, opportunistic evaluation, and
internal cache storage and lookup. Both solvers run serially with seed 42 by
default. A shared seed makes each algorithm reproducible with the same inputs.
OMADS and DE use different random search processes.

DE retains its population size of `max(5, 3*popsize)`, with the initial policy,
four pure-weight policies, and a seeded Dirichlet sample. Its defaults are
`--maxiter 50 --popsize 10 --tol 0.01 --atol 0` with gradient polishing disabled.
`--maxiter 0` scores the initial population only. SciPy evaluates its initial
population directly, so there is no separate population pre-scoring pass.

`--budget N` caps actual simulations per method, including the initial policy
and any population initialization. In single mode, OMADS and both-method comparisons default to 200. DE alone
uses its generation and convergence controls unless you supply a simulation cap.
Both-mode uses independent caches. DE ends immediately before a cache miss
would exceed the cap, including during initialization or partway through a
generation. The best candidate from those already simulated remains eligible.
Each solver can terminate early according to its own criteria. Invalid
coordinates, nonfinite objective scores, and simulation errors abort with
diagnostic context.

### Results

The CLI prints weights, initial and best objectives, percentage improvement,
initial and final wait, travel and shortfall, runtime, counts, and termination.
`--output` writes JSON schema 2 with the resolved scenario and settings. Single
runs keep the original top-level result fields. Both-mode puts results under
`omads` and `de`. `--plot` saves a step plot against cumulative actual
simulations, with the initial policy as a horizontal baseline.

`objective_calls` counts calls from the solver to the blackbox, including calls
that hit the policy cache and any call that triggered the simulation cap. The
separate baseline measurement counts toward `simulations`. `cache_hits` counts
policy cache hits visible to the wrapper. `function_evaluations` copies the
solver's counter when available. OMADS 2610 poll can undercount evaluations in
its return dictionary, and SciPy supplies no final count when interrupted by
the cap. Use `simulations` for comparison. `evaluation_history` records each
simulated policy, its objective, and the best objective so far. DE also retains
generation history in `history`. OMADS reports solver iterations separately.
OMADS stopping details come from its termination log and measured budget usage.

The returned policy is the best observed on the selected scenario, including
the baseline. The discrete-event objective can be nonsmooth, discontinuous, or
locally flat. The result and stopping criterion provide no global optimality
guarantee.

### Python API

```python
from mill_yard.optimization.optimizer import OMADSParameters, optimize_policy

comparison = optimize_policy(
    scenario,
    method="both",
    evaluation_budget=200,
    omads_settings=OMADSParameters(full_mads=True),
)
print(comparison.omads.theta, comparison.omads.score.total)
print(comparison.de.theta, comparison.de.score.total)
```

An individual method returns an `OptimizationResult` directly. Use
`method="de"` when replaying legacy DE scripts and pass DE options through
`settings=OptimizerParameters(...)`. The shared seed also comes from
`OptimizerParameters.seed`.


### Batch optimization

Batch mode finds one policy across the selected scenarios. For each candidate,
the optimizer runs each scenario from its original initial conditions with the
same policy weights and the same feature and objective parameters. It minimizes
the arithmetic mean of the existing scenario objectives, giving every scenario
equal influence regardless of truck count or horizon.

Run the following commands from the checkout. With no scenario paths,
`--mode batch` loads all six YAML experiment presets in `configs/experiments/traffic`.

```bash
# Batch OMADS poll (the default algorithm)
PYTHONPATH=src uv run python scripts/optimize_policy.py --mode batch

# Batch DE
PYTHONPATH=src uv run python scripts/optimize_policy.py --mode batch --method de

# Compare the two solvers on the same batch
PYTHONPATH=src uv run python scripts/optimize_policy.py --mode batch --method both --output runs/optimization/batch.json --plot runs/optimization/batch.png

# Choose a subset and use full MADS
PYTHONPATH=src uv run python scripts/optimize_policy.py --mode batch --full-mads configs/experiments/traffic/1area_shared__003trucks_1loader_180min.yaml configs/experiments/traffic/1area_shared__045trucks_2loaders_180min.yaml
```

For a custom batch, list YAML paths with unique filename stems, or pass a nonempty
mapping from IDs to `YardScenario` objects in Python. The default YAML files
are available from a repository checkout. Installed-package callers without the
checkout can pass a mapping explicitly.

```python
from mill_yard.config.loader import load_yard_scenario
from mill_yard.optimization.optimizer import optimize_policy

batch = {
    "light": load_yard_scenario("configs/experiments/traffic/1area_shared__003trucks_1loader_180min.yaml"),
    "heavy": load_yard_scenario("configs/experiments/traffic/1area_shared__045trucks_2loaders_180min.yaml"),
}
result = optimize_policy(batch, mode="batch", method="omads")
print(result.theta, result.score.total, result.scenario_scores)
```

In batch mode, `--budget` caps unique candidate-policy evaluations per solver and
defaults to 200 for each method. The baseline uses one candidate evaluation.
A candidate uses one simulation per scenario, so 200 candidates across six
scenarios use 1,200 actual simulations. Cache hits reuse the complete candidate
result within one solver run. Each solver can stop early under its own stopping
criteria. The batch plot uses cumulative candidate-policy evaluations on the
horizontal axis. The CLI and batch JSON report simulation counts separately.

The terminal report shows the initial and best objective, wait, travel, and
shortfall for each scenario and the batch mean. Batch JSON uses schema 3 and
includes resolved scenario configurations, the per-scenario scores for the
initial and best common policy, and the best-so-far mean-objective history.
Single mode remains the default and its JSON retains schema 2.

### Dispatch policy studies

Generate the operating comparison and weight sensitivity plots from a saved OMADS
batch covering all six built-in traffic presets. The study accepts output from
`--method omads` or `--method both`.

```bash
PYTHONPATH=src uv run python scripts/study_dispatch_policy.py --source runs/optimization/batch.json --output runs/analysis/decision_policy_sensitivity
```

The study checks the resolved scenarios and replays the saved policy before
producing the CSVs, plots, and summary. Regenerate batch results produced before
the simulation extension to study the policy under the updated simulation.
