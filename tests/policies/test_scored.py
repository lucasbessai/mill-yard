"""Tests for scored dispatch decisions and persisted parameters."""

from dataclasses import replace

import pytest

from mill_yard.model.types import Arrival, Point
from mill_yard.model.yard import (
    Assignment,
    InfeedSpec,
    InventoryState,
    LoaderSpec,
    LoaderState,
    PileSpec,
    StopSpec,
    TruckState,
    YardScenario,
    YardState,
)
from mill_yard.policies.scored import ScoredParameters, ScoredPolicy
from mill_yard.simulation.engine import run_yard


@pytest.fixture
def yard():
    """Two loaders and two stops with a low infeed."""
    return YardScenario(
        horizon=30,
        arrivals=(),
        stops=(StopSpec("s1", Point(0, 0)), StopSpec("s2", Point(0, 20))),
        piles=(PileSpec("p", Point(100, 0), 500, 100),),
        infeeds=(InfeedSpec("i", Point(200, 0), 100, 5, 1, 20, 70),),
        loaders=(
            LoaderSpec("l1", Point(100, 0), 20, 100, 5, 5),
            LoaderSpec("l2", Point(0, 0), 20, 100, 5, 5),
        ),
    )


def make_state(yard):
    """Construct a dispatch observation with one waiting truck."""
    return YardState(
        time=10,
        trucks={"t": TruckState("t", 0, 20, stop_id="s1")},
        queue=[],
        stops={"s1": "t", "s2": None},
        piles={"p": InventoryState(100)},
        infeeds={"i": InventoryState(5)},
        loaders={x.id: LoaderState(x.id, x.location) for x in yard.loaders},
    )


def test_joint_choice_serves_truck_and_urgent_infeed(yard):
    state = make_state(yard)
    assignments = ScoredPolicy().choose(state, yard)
    assert {(a.loader_id, a.source_kind) for a in assignments} == {("l1", "pile"), ("l2", "truck")}
    assert state.piles["p"].outgoing == 0


def test_idle_or_topup_when_no_truck(yard):
    state = make_state(yard)
    state.trucks.clear()
    state.stops["s1"] = None
    state.infeeds["i"].amount = 40
    tasks = ScoredPolicy().choose(state, yard)
    assert tasks
    assert all(a.destination_kind == "infeed" for a in tasks)
    state.infeeds["i"].amount = 70
    assert ScoredPolicy().choose(state, yard) == []


def test_completed_incoming_delivery_reduces_immediate_risk(yard):
    state = make_state(yard)
    policy = ScoredPolicy()
    task = Assignment("l1", "pile", "p", "infeed", "i", 20)
    before = policy._score(task, state, yard)
    loader = state.loaders["l2"]
    loader.assignment = Assignment("l2", "pile", "p", "infeed", "i", 20)
    loader.phase = "deposit"
    loader.phase_end = 12
    state.infeeds["i"].incoming = 20
    assert policy._score(task, state, yard) < before
    loader.phase_end = 100
    assert policy._score(task, state, yard) == pytest.approx(before)


def test_parameters_are_validated_and_retained(yard):
    with pytest.raises(ValueError, match="positive"):
        ScoredParameters(wait_reference=0)
    scenario = replace(yard, arrivals=(Arrival(0, 20, "truck"),))
    params = ScoredParameters(truck_weight=3)
    result = run_yard(scenario, ScoredPolicy(params), record="summary")
    assert result.policy_parameters["truck_weight"] == 3
    assert len(result.completed_trucks) == 1


def test_scored_policy_conserves_material_in_active_jobs(yard):
    scenario = replace(yard, arrivals=(Arrival(0, 35, "truck"),), horizon=1)
    result = run_yard(scenario, ScoredPolicy(), record="summary")
    state = result.state
    material = (
        sum(truck.remaining for truck in state.trucks.values())
        + sum(pile.amount for pile in state.piles.values())
        + sum(infeed.amount for infeed in state.infeeds.values())
        + sum(loader.carried for loader in state.loaders.values())
        + sum(state.consumed.values())
    )
    assert material == pytest.approx(140)
