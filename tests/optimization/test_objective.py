"""Check objective units, material accounting, and preference behavior."""

import pytest

from mill_yard.config.loader import DEFAULT_OBJECTIVE_PATH, load_yard_scenario
from mill_yard.optimization.objective import ObjectiveParameters, evaluate_objective
from mill_yard.policies.joint import ThresholdPolicy
from mill_yard.simulation.engine import run_yard


def test_shortfall_matches_requested_minus_consumed():
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    result = run_yard(scenario, ThresholdPolicy(), record="summary")
    score = evaluate_objective(result)
    requested = sum(x.consumption_rate * result.state.time for x in scenario.infeeds)
    assert score.shortfall_tonnes == pytest.approx(requested - sum(result.state.consumed.values()))
    assert score.shortfall_tonnes == pytest.approx(455)
    travel_only = evaluate_objective(
        result, ObjectiveParameters(wait_weight=0, distance_weight=1, shortfall_weight=0)
    )
    assert travel_only.total == pytest.approx(score.travel_metres / 5_000)
    assert score.total == pytest.approx(
        (score.truck_wait_minutes / 60 + score.travel_metres / 5_000 + 455 / 10) / 3
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reference_wait": 0},
        {"reference_shortfall": float("nan")},
        {"distance_weight": -1},
        {"wait_weight": 0, "distance_weight": 0, "shortfall_weight": 0},
    ],
)
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError, match="must"):
        ObjectiveParameters(**kwargs)
