"""Optimizer-facing contract and proposal dispatch behavior."""

from copy import deepcopy

import pytest

from mill_yard.config.loader import DEFAULT_OBJECTIVE_PATH, load_yard_scenario
from mill_yard.model.feasibility import candidates, validate_joint
from mill_yard.model.yard import InventoryState, LoaderState, TruckState, YardState
from mill_yard.optimization.objective import evaluate_objective
from mill_yard.policies.optimizable import OptimizablePolicy
from mill_yard.simulation.engine import run_yard


@pytest.fixture
def observation():
    scenario = load_yard_scenario(
        "configs/experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml"
    )
    state = YardState(
        time=30,
        trucks={"t": TruckState("t", 0, 100, stop_id=scenario.stops[0].id)},
        queues={area.id: [] for area in scenario.unloading_areas},
        stops={s.id: "t" if s == scenario.stops[0] else None for s in scenario.stops},
        piles={p.id: InventoryState(p.capacity / 2) for p in scenario.piles},
        infeeds={i.id: InventoryState(0) for i in scenario.infeeds},
        loaders={loader.id: LoaderState(loader.id, loader.location) for loader in scenario.loaders},
    )
    return scenario, state


@pytest.mark.parametrize(
    "theta",
    [[], [1, 2, 3], [0, 0, 0, 0], [-1, 1, 1, 1], [float("nan"), 1, 1, 1], [float("inf"), 1, 1, 1]],
)
def test_invalid_theta(theta):
    with pytest.raises(ValueError, match="theta"):
        OptimizablePolicy(theta)


def test_weights_change_dispatch_without_mutating_state(observation):
    scenario, state = observation
    before = deepcopy(state)
    trucks = OptimizablePolicy([1, 0, 0, 0]).choose(state, scenario)
    mill = OptimizablePolicy([0, 1, 0, 0]).choose(state, scenario)
    assert trucks
    assert all(a.source_kind == "truck" for a in trucks)
    assert mill
    assert all(a.destination_kind == "infeed" for a in mill)
    validate_joint(state, scenario, trucks)
    validate_joint(state, scenario, mill)
    assert state == before
    assert OptimizablePolicy([0, 0, 0, 1]).choose(state, scenario) == []


def test_committed_delivery_only_helps_before_first_stockout(observation):
    scenario, state = observation
    policy = OptimizablePolicy([0, 1, 0, 0])
    a = next(a for a in candidates(state, scenario) if a.destination_kind == "infeed")
    spec = next(i for i in scenario.infeeds if i.id == a.destination_id)
    state.infeeds[spec.id].amount = spec.consumption_rate * 10
    loader = state.loaders[a.loader_id]
    loader.assignment = a
    loader.phase = "deposit"
    loader.phase_completes_at = state.time + 5
    assert policy._stockout(spec.id, state, scenario) == pytest.approx(
        10 + a.quantity / spec.consumption_rate
    )
    loader.phase_completes_at = state.time + 11
    assert policy._stockout(spec.id, state, scenario) == pytest.approx(10)


def test_simulation_objective_and_metadata():
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    policy = OptimizablePolicy([2, 4, 1, 1])
    result = run_yard(scenario, policy, record="summary")
    assert result.policy == "OptimizablePolicy"
    assert result.policy_parameters["theta"] == [0.25, 0.5, 0.125, 0.125]
    assert evaluate_objective(result).total >= 0
    assert run_yard(scenario, policy, record="summary").state == result.state
