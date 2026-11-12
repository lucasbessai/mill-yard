"""Exercise the DE/simulation integration and simplex search contract."""

from dataclasses import replace

import numpy as np
import pytest

from mill_yard.config.loader import load_yard_scenario
from mill_yard.optimization.objective import ObjectiveParameters, evaluate_objective
from mill_yard.optimization.optimizer import (
    OptimizerParameters,
    _to_coordinates,
    _to_theta,
    optimize_policy,
)
from mill_yard.policies.optimizable import FeatureParameters, OptimizablePolicy
from mill_yard.simulation.engine import run_yard


@pytest.mark.parametrize("theta", [*np.eye(4), [0.25, 0.4, 0.15, 0.2], [0, 0.5, 0, 0.5]])
def test_simplex_round_trip_including_boundaries(theta):
    actual = _to_theta(_to_coordinates(theta))
    assert actual == pytest.approx(theta)
    assert sum(actual) == pytest.approx(1)
    assert all(v >= 0 for v in actual)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"maxiter": -1},
        {"popsize": 0},
        {"seed": -1},
        {"tol": float("nan")},
        {"atol": -1},
        {"popsize": 1.5},
    ],
)
def test_invalid_search_settings(kwargs):
    with pytest.raises(ValueError, match="must be"):
        OptimizerParameters(**kwargs)


def test_search_improves_bad_baseline_and_replays_with_fixed_settings():
    scenario = load_yard_scenario("configs/example.yaml")
    settings = OptimizerParameters(maxiter=2, popsize=3, seed=17)
    features = FeatureParameters(stockout_reference=20)
    objective = ObjectiveParameters(shortfall_weight=3)
    kwargs = {
        "initial_theta": [0, 0, 0, 1],
        "settings": settings,
        "features": features,
        "objective": objective,
    }
    result = optimize_policy(scenario, **kwargs)
    assert result == optimize_policy(scenario, **kwargs)
    assert sum(result.theta) == pytest.approx(1)
    assert all(0 <= v <= 1 for v in result.theta)
    assert result.score.total < result.initial_score.total
    replay = run_yard(scenario, OptimizablePolicy(result.theta, features), record="summary")
    assert evaluate_objective(replay, objective) == result.score
    assert result.simulations > 1
    assert result.function_evaluations <= (settings.maxiter + 1) * 3 * settings.popsize
    assert [h.generation for h in result.history] == list(range(result.generations + 1))
    totals = [h.objective for h in result.history]
    assert totals == sorted(totals, reverse=True)
    assert totals[-1] == pytest.approx(result.score.total)


def test_zero_generations_returns_best_initial_population_and_termination_reason():
    scenario = load_yard_scenario("configs/example.yaml")
    result = optimize_policy(scenario, settings=OptimizerParameters(maxiter=0, popsize=1))
    assert result.generations == 0
    assert not result.converged
    assert "maximum" in result.message.lower()
    assert result.score.total <= result.initial_score.total
    assert len(result.history) == 1


def test_population_can_converge_when_all_policies_have_same_outcomes():
    scenario = load_yard_scenario("configs/example.yaml")
    scenario = replace(
        scenario,
        arrivals=(),
        infeeds=tuple(replace(i, consumption_rate=0) for i in scenario.infeeds),
    )
    # Only waiting is penalized, and there are no trucks.
    objective = ObjectiveParameters(wait_weight=1, distance_weight=0, shortfall_weight=0)
    result = optimize_policy(
        scenario, objective=objective, settings=OptimizerParameters(maxiter=3, popsize=2)
    )
    assert result.converged
    assert result.score.total == 0
