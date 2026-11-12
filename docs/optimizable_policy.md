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

scenario = load_yard_scenario("configs/traffic/medium_2loader.yaml")
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

## Search with differential evolution

Install the project dependencies (`uv sync`), then run:

```bash
PYTHONPATH=src uv run python scripts/optimize_policy.py configs/traffic/medium_2loader.yaml --maxiter 50 --popsize 10 --seed 42 --output runs/optimization/medium.json
```

The script prints the best normalized theta, initial and best objective totals,
raw outcomes, simulation count, and the stopping reason. The JSON output also
contains the complete resolved scenario, objective and feature parameters, seed,
search settings, and best-so-far objective by generation. Generation zero records
the best initial population member. Output paths are overwritten when reused.

The Python entry point is:

```python
from mill_yard.optimization.optimizer import OptimizerParameters, optimize_policy

search = optimize_policy(
    scenario,
    settings=OptimizerParameters(seed=42, maxiter=50, popsize=10),
    features=features,
)
print(search.theta, search.score.total, search.message)
# Replay using OptimizablePolicy(search.theta, search.features).
```

The implementation uses [SciPy differential evolution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html).
Three bounded stick-breaking coordinates produce the four weights:

```text
theta_T = a
theta_M = (1-a) b
theta_I = (1-a) (1-b) c
theta_C = (1-a) (1-b) (1-c)
```

This covers the simplex, including zero weights, without an equality constraint.
The population has `max(5, 3*popsize)` members. It starts with the supplied initial
theta, four pure-weight policies, and random weights sampled uniformly on the
simplex. The default initial theta is the proposal's example (0.25, 0.4, 0.15, 0.2).
Use `--initial-theta T M I C` to supply a different baseline. The returned policy
is at least as good as that baseline on this scenario and objective, since every
evaluated candidate remains eligible for the result.

Each evaluation creates a fresh simulation in summary-recording mode. Exact
repeated normalized theta vectors reuse cached scores within the search. All
simulation errors propagate instead of being disguised as poor scores. Execution
is serial and seeded. Gradient polishing is disabled because dispatch decisions
create a discontinuous objective with flat regions.

The DE budget is at most `(maxiter+1)*max(5,3*popsize)` function evaluations,
plus initialization scoring and the baseline evaluation. Initialization scores
are cached, so repeated evaluations normally avoid another simulation. Result
`function_evaluations` is SciPy's count; `simulations` includes all actual runs,
including initialization and baseline. `--maxiter 0` evaluates only the initial
population. `--tol` and `--atol` control SciPy's population-energy convergence
criterion. Reaching the generation limit still returns the best policy found;
`converged` reports whether SciPy met its stopping criterion. Neither convergence
nor a low objective proves global optimality. Try several seeds and larger budgets
before drawing conclusions, and evaluate chosen weights on other scenarios.

All objective arguments from `compare_objectives.py` are available, for example
`--shortfall-weight 3` or `--reference-wait 120`. Fixed feature settings use a
`--feature-` prefix, such as `--feature-stockout-reference 20`. Keep these settings
constant when comparing searches. Theta weights control dispatch decisions;
objective weights express which simulation outcomes the search should favor.
