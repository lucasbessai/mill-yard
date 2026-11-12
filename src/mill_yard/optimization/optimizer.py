"""Optimize four dispatch weights on one deterministic yard scenario."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

import numpy as np
from scipy.optimize import differential_evolution

from mill_yard.model.yard import YardScenario
from mill_yard.optimization.objective import ObjectiveParameters, ObjectiveScore, evaluate_objective
from mill_yard.policies.optimizable import FeatureParameters, OptimizablePolicy
from mill_yard.simulation.engine import run_yard


@dataclass(frozen=True)
class OptimizerParameters:
    """Reproducible, serial DE search budget; population size is max(5, 3*popsize)."""

    seed: int = 42
    maxiter: int = 50
    popsize: int = 10
    tol: float = 0.01
    atol: float = 0.0

    def __post_init__(self) -> None:
        for name, minimum in (("seed", 0), ("maxiter", 0), ("popsize", 1)):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        for name in ("tol", "atol"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class SearchProgress:
    """Best-so-far objective after initialization (0) or a completed generation."""

    generation: int
    objective: float


@dataclass(frozen=True)
class OptimizationResult:
    """Best weights found, baseline comparison, and solver termination details.

    Convergence is SciPy's population-energy criterion, not proof of a global
    optimum. A search hitting maxiter still returns its best evaluated policy.
    """

    theta: tuple[float, ...]
    score: ObjectiveScore
    initial_theta: tuple[float, ...]
    initial_score: ObjectiveScore
    settings: OptimizerParameters
    features: FeatureParameters
    objective_parameters: ObjectiveParameters
    generations: int
    function_evaluations: int
    simulations: int
    converged: bool
    message: str
    history: tuple[SearchProgress, ...]


def _to_theta(coordinates: Sequence[float]) -> tuple[float, float, float, float]:
    """Break a unit stick into four weights using three coordinates in [0, 1]."""
    a, b, c = coordinates
    return (
        float(a),
        float((1 - a) * b),
        float((1 - a) * (1 - b) * c),
        float((1 - a) * (1 - b) * (1 - c)),
    )


def _to_coordinates(theta: Sequence[float]) -> tuple[float, float, float]:
    """Invert stick breaking, including simplex faces and vertices."""
    t, m, i, c = theta
    remainder = m + i + c
    return (t, m / remainder if remainder > 0 else 0.0, i / (i + c) if i + c > 0 else 0.0)


def optimize_policy(
    scenario: YardScenario,
    *,
    initial_theta: Sequence[float] = (0.25, 0.40, 0.15, 0.20),
    settings: OptimizerParameters | None = None,
    features: FeatureParameters | None = None,
    objective: ObjectiveParameters | None = None,
) -> OptimizationResult:
    """Minimize the completed-run objective with SciPy differential evolution.

    Each evaluation creates a fresh policy and simulation. Feature definitions,
    objective preferences, and scenario remain fixed. Exact repeated normalized
    theta vectors are cached within this search. The initial population includes
    the supplied baseline and all four pure-weight policies; the remaining members
    are sampled uniformly on the simplex. 

    Three stick-breaking coordinates span the entire four-weight simplex without
    an equality constraint or an all-zero vector. Gradient polishing is disabled
    because dispatch decisions make this objective discontinuous and often flat.
    """
    settings = settings or OptimizerParameters()
    features = features or FeatureParameters()
    objective = objective or ObjectiveParameters()
    initial = OptimizablePolicy(initial_theta, features).theta
    cache: dict[tuple[float, ...], ObjectiveScore] = {}

    def evaluate(theta: Sequence[float]) -> ObjectiveScore:
        policy = OptimizablePolicy(theta, features)
        if policy.theta not in cache:
            result = run_yard(scenario, policy, record="summary")
            cache[policy.theta] = evaluate_objective(result, objective)
        return cache[policy.theta]

    initial_score = evaluate(initial)

    def loss(coordinates: Sequence[float]) -> float:
        return evaluate(_to_theta(coordinates)).total

    rng = np.random.default_rng(settings.seed)
    size = max(5, 3 * settings.popsize)
    weights = np.vstack((np.asarray(initial), np.eye(4), rng.dirichlet(np.ones(4), size - 5)))
    population = np.asarray([_to_coordinates(row) for row in weights])
    history = [SearchProgress(0, min(loss(row) for row in population))]

    def record_generation(intermediate_result):
        history.append(SearchProgress(int(intermediate_result.nit), float(intermediate_result.fun)))

    solution = differential_evolution(
        loss,
        bounds=[(0.0, 1.0)] * 3,
        init=population,
        rng=rng,
        maxiter=settings.maxiter,
        popsize=settings.popsize,
        tol=settings.tol,
        atol=settings.atol,
        polish=False,
        callback=record_generation,
        workers=1,
    )
    # Retain the best actually simulated policy, including the baseline, even
    # if several coordinate representations tie or normalization changes rounding.
    theta, score = min(cache.items(), key=lambda item: item[1].total)
    return OptimizationResult(
        theta=theta,
        score=score,
        initial_theta=initial,
        initial_score=initial_score,
        settings=settings,
        features=features,
        objective_parameters=objective,
        generations=int(solution.nit),
        function_evaluations=int(solution.nfev),
        simulations=len(cache),
        converged=bool(solution.success),
        message=str(solution.message),
        history=tuple(history),
    )
