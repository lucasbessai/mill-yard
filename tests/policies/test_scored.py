"""Tests for scored dispatch decisions and persisted parameters."""

from dataclasses import replace

import pytest

from mill_yard.model.scenario import InfeedSpec, LoaderSpec, PileSpec, StopSpec, YardScenario
from mill_yard.model.state import Assignment, InventoryState, LoaderState, TruckState, YardState
from mill_yard.model.types import Arrival, Point
from mill_yard.policies.scored import ScoredParameters, ScoredPolicy, predicted_starvation
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
        queues={"main": []},
        stops={"s1": "t", "s2": None},
        piles={"p": InventoryState(100)},
        infeeds={"i": InventoryState(15)},
        loaders={x.id: LoaderState(x.id, x.location) for x in yard.loaders},
    )


def test_joint_choice_serves_truck_and_urgent_infeed(yard):
    state = make_state(yard)
    assignments = ScoredPolicy().choose(state, yard)
    assert {(a.loader_id, a.source_kind) for a in assignments} == {("l1", "pile"), ("l2", "truck")}
    assert state.piles["p"].reserved_outgoing == 0


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
    before = policy.score_joint([task], state, yard)
    loader = state.loaders["l2"]
    loader.assignment = Assignment("l2", "pile", "p", "infeed", "i", 20)
    loader.phase = "deposit"
    loader.phase_completes_at = 12
    state.infeeds["i"].reserved_incoming = 20
    assert policy.score_joint([task], state, yard) < before
    loader.phase_completes_at = 100
    assert policy.score_joint([task], state, yard) == pytest.approx(before)


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
        sum(truck.remaining_quantity for truck in state.trucks.values())
        + sum(pile.amount for pile in state.piles.values())
        + sum(infeed.amount for infeed in state.infeeds.values())
        + sum(loader.carried for loader in state.loaders.values())
        + sum(state.consumed.values())
    )
    assert material == pytest.approx(140)


def test_loader_order_does_not_change_joint_choice(yard):
    state = make_state(yard)
    state.infeeds["i"].amount = 5
    policy = ScoredPolicy()
    forward = policy.choose(state, yard)
    reverse = policy.choose(state, replace(yard, loaders=yard.loaders[::-1]))
    assert set(forward) == set(reverse)
    assert policy.score_joint(forward, state, yard) == pytest.approx(
        policy.score_joint(forward[::-1], state, yard)
    )


def test_slower_loader_does_not_gain_shortage_credit(yard):
    point = Point(0, 0)
    scenario = replace(
        yard,
        horizon=8,
        wake_interval=10,
        piles=(PileSpec("p", point, 100, 100),),
        infeeds=(InfeedSpec("i", Point(10, 0), 30, 5, 1, 2, 20),),
        loaders=(
            LoaderSpec("fast", point, 20, 1000, 100, 100),
            LoaderSpec("slow", point, 20, 1, 100, 100),
        ),
    )
    result = run_yard(scenario, ScoredPolicy(), record="events", validate=True)
    decision = next(e for e in result.events if e["event"] == "decision")
    assert decision["assignments"][0]["loader_id"] == "fast"
    assert result.state.starvation["i"] == 0


def test_delivery_timeline_counts_shortages_before_late_incoming_stock():
    assert predicted_starvation(2, 1, [(5, 4)], 10) == 4


def test_automatically_seeded_random_policy_records_reproducible_seed(yard):
    from mill_yard.policies.random import RandomPolicy

    scenario = replace(yard, arrivals=(Arrival(0, 20, "a"),))
    first = run_yard(scenario, RandomPolicy())
    second = run_yard(scenario, RandomPolicy(first.seed))
    assert isinstance(first.seed, int)
    assert first.events == second.events
