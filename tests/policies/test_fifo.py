"""Operator baseline priorities, parallel FIFO, and quantity-before-distance rules."""

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from mill_yard.model.feasibility import reserve, validate_joint
from mill_yard.model.scenario import InfeedSpec, LoaderSpec, PileSpec, StopSpec, YardScenario
from mill_yard.model.state import Assignment, InventoryState, LoaderState, TruckState, YardState
from mill_yard.model.types import Point
from mill_yard.policies.base import PolicyContext
from mill_yard.policies.fifo import FifoParameters, FifoPolicy
from mill_yard.simulation.engine import run_yard


@pytest.fixture
def yard():
    return YardScenario(
        horizon=120,
        arrivals=(),
        stops=(StopSpec("s1", Point(0, 0), 2), StopSpec("s2", Point(0, 20), 2)),
        piles=(PileSpec("p", Point(50, 0), 100, 40),),
        infeeds=(InfeedSpec("mill", Point(100, 0), 100, 50, 1, 20, 70),),
        loaders=(
            LoaderSpec("l1", Point(0, 0), 10, 10, 10, 10),
            LoaderSpec("l2", Point(100, 0), 10, 10, 10, 10),
        ),
    )


def observe(yard):
    return YardState(
        time=10,
        trucks={
            "old": TruckState("old", 0, 20, stop_id="s1"),
            "new": TruckState("new", 5, 20, stop_id="s2"),
        },
        queues={"main": []},
        stops={"s1": "old", "s2": "new"},
        piles={p.id: InventoryState(p.initial) for p in yard.piles},
        infeeds={i.id: InventoryState(i.initial) for i in yard.infeeds},
        loaders={loader.id: LoaderState(loader.id, loader.location) for loader in yard.loaders},
    )


def test_partial_oldest_truck_is_reserved_before_parallel_service_of_next_truck(yard):
    state = observe(yard)
    state.trucks["old"].remaining_quantity = 3
    before = asdict(state)
    chosen = FifoPolicy().choose(state, PolicyContext.from_scenario(yard))
    assert [(a.source_id, a.quantity) for a in chosen] == [("old", 3), ("new", 10)]
    assert chosen[0].loader_id == "l1"
    assert asdict(state) == before
    validate_joint(state, yard, chosen)


def test_unreserved_remainder_stays_ahead_of_newer_truck(yard):
    state = observe(yard)
    state.trucks["old"].remaining_quantity = 15
    chosen = FifoPolicy().choose(state, yard)
    assert [(a.source_id, a.quantity) for a in chosen] == [("old", 10), ("old", 5)]


def test_low_infeed_precedes_full_pile_relief_even_when_other_mill_has_less_coverage(yard):
    yard = replace(
        yard,
        piles=(replace(yard.piles[0], initial=100),),
        infeeds=(
            replace(yard.infeeds[0], initial=10),
            InfeedSpec("other", Point(90, 0), 100, 50, 10, 20, 70),
        ),
    )
    chosen = FifoPolicy(FifoParameters(buffer_cycles=0)).choose(observe(yard), yard)
    assert all(a.destination_id == "mill" for a in chosen)
    assert chosen[0].loader_id == "l2"


def test_multiple_low_infeeds_use_time_to_empty_not_fraction_alone(yard):
    yard = replace(
        yard,
        infeeds=(
            replace(yard.infeeds[0], initial=20),
            InfeedSpec("other", Point(90, 0), 100, 10, 0.1, 20, 70),
        ),
    )
    chosen = FifoPolicy(FifoParameters(buffer_cycles=0)).choose(observe(yard), yard)
    assert chosen[0].destination_id == "mill"  # 20 min, versus 100 min for other.


def test_full_pile_relief_precedes_trucks_and_can_use_space_above_buffer(yard):
    yard = replace(
        yard,
        piles=(replace(yard.piles[0], initial=100), PileSpec("spare", Point(5, 0), 100, 40)),
        infeeds=(replace(yard.infeeds[0], initial=80),),
    )
    state = observe(yard)
    chosen = FifoPolicy().choose(state, yard)
    assert (chosen[0].source_kind, chosen[0].source_id, chosen[0].destination_id) == (
        "pile",
        "p",
        "mill",
    )
    assert chosen[0].quantity == 10
    assert chosen[1].source_id == "old"
    assert chosen[1].destination_id == "spare"
    validate_joint(state, yard, chosen)


def test_truck_chooses_fuller_far_pile_before_near_partial_space(yard):
    yard = replace(yard, piles=(PileSpec("near", Point(5, 0), 100, 99), yard.piles[0]))
    chosen = FifoPolicy().choose(observe(yard), yard)
    assert chosen[0].destination_id == "p"
    assert chosen[0].quantity == 10


def test_small_truck_remainder_uses_nearest_pile_when_quantities_tie(yard):
    yard = replace(yard, piles=(PileSpec("near", Point(5, 0), 100, 95), yard.piles[0]))
    state = observe(yard)
    state.trucks["old"].remaining_quantity = 3
    chosen = FifoPolicy().choose(state, yard)
    assert chosen[0].destination_id == "near"
    assert chosen[0].quantity == 3


def test_mill_loader_chooses_fuller_far_source_before_nearer_small_source(yard):
    yard = replace(
        yard,
        piles=(PileSpec("near", Point(95, 0), 100, 3), yard.piles[0]),
        infeeds=(replace(yard.infeeds[0], initial=10),),
    )
    chosen = FifoPolicy().choose(observe(yard), yard)
    assert chosen[0].loader_id == "l2"
    assert chosen[0].source_id == "p"
    assert chosen[0].quantity == 10


def test_partial_mill_load_is_allowed_when_all_sources_are_small(yard):
    yard = replace(
        yard,
        piles=(replace(yard.piles[0], initial=3),),
        infeeds=(replace(yard.infeeds[0], initial=10),),
    )
    chosen = FifoPolicy().choose(observe(yard), yard)
    assert chosen[0].destination_id == "mill"
    assert chosen[0].quantity == 3
    assert chosen[1].source_id == "old"  # Remaining loader brings in more material.


def test_refill_hysteresis_continues_above_low_fraction_until_target(yard):
    state = observe(yard)
    state.infeeds["mill"].amount = 20
    policy = FifoPolicy(FifoParameters(buffer_cycles=0))
    policy.choose(state, yard)
    assert policy.refill_active["mill"]
    state.infeeds["mill"].amount = 45
    chosen = policy.choose(state, yard)
    assert sum(a.quantity for a in chosen if a.destination_kind == "infeed") == 20
    assert not policy.refill_active["mill"]
    state.infeeds["mill"].amount = 65
    assert policy.choose(state, yard)[0].source_kind == "truck"


def test_refill_prefers_full_load_over_shaving_quantity_to_exact_target(yard):
    state = observe(yard)
    state.infeeds["mill"].amount = 60
    policy = FifoPolicy(FifoParameters(buffer_cycles=0))
    policy.refill_active["mill"] = True
    chosen = policy.choose(state, yard)
    assert chosen[0].destination_kind == "infeed"
    assert chosen[0].quantity == 10  # 70%, rather than a 5-t partial to reach 65%.
    assert chosen[1].source_kind == "truck"


def test_cycle_coverage_target_can_rise_but_is_capped(yard):
    policy = FifoPolicy()
    assert policy.refill_targets(observe(yard), yard)["mill"] == 65
    medium = replace(yard, infeeds=(replace(yard.infeeds[0], consumption_rate=3),))
    assert (
        policy.refill_targets(observe(medium), medium)["mill"] == 72
    )  # 2 cycles x 12 min x 3 t/min.
    high = replace(yard, infeeds=(replace(yard.infeeds[0], consumption_rate=10),))
    assert policy.refill_targets(observe(high), high)["mill"] == 75


def test_normal_refill_never_overbooks_upper_buffer_fraction(yard):
    yard = replace(yard, infeeds=(replace(yard.infeeds[0], initial=70, consumption_rate=10),))
    policy = FifoPolicy()
    policy.refill_active["mill"] = True
    state = observe(yard)
    chosen = policy.choose(state, yard)
    assert chosen[0].destination_kind == "infeed"
    assert chosen[0].quantity == 5
    validate_joint(state, yard, chosen)


def test_committed_refills_and_pile_withdrawals_are_not_assigned_twice(yard):
    yard = replace(
        yard, piles=(replace(yard.piles[0], initial=100), PileSpec("spare", Point(5, 0), 100, 0))
    )
    state = observe(yard)
    reserve(state, yard, Assignment("l2", "pile", "p", "infeed", "mill", 10))
    chosen = FifoPolicy().choose(state, yard)
    assert len(chosen) == 1
    assert chosen[0].source_id == "old"  # Existing withdrawal already relieves full p.
    state = observe(yard)
    state.piles["p"].amount = 40
    state.infeeds["mill"].amount = 10
    state.infeeds["mill"].reserved_incoming = 65
    assert all(a.destination_kind == "pile" for a in FifoPolicy().choose(state, yard))


def test_busy_nearest_loader_is_not_preempted(yard):
    state = observe(yard)
    state.infeeds["mill"].amount = 10
    reserve(state, yard, Assignment("l2", "truck", "old", "pile", "p", 10))
    chosen = FifoPolicy().choose(state, yard)
    assert len(chosen) == 1
    assert chosen[0].loader_id == "l1"
    assert chosen[0].destination_kind == "infeed"


def test_inactive_mill_and_full_yard_limit_cases(yard):
    inactive = replace(yard, infeeds=(replace(yard.infeeds[0], consumption_rate=0, initial=0),))
    assert all(a.source_kind == "truck" for a in FifoPolicy().choose(observe(inactive), inactive))
    full = replace(
        yard,
        piles=(replace(yard.piles[0], initial=100),),
        infeeds=(replace(yard.infeeds[0], initial=100),),
    )
    assert FifoPolicy().choose(observe(full), full) == []
    empty = replace(
        yard,
        piles=(replace(yard.piles[0], initial=0),),
        infeeds=(replace(yard.infeeds[0], initial=0),),
    )
    assert all(a.source_kind == "truck" for a in FifoPolicy().choose(observe(empty), empty))


def test_yaml_order_and_snapshot_mutation_do_not_change_policy_decisions(yard):
    state = observe(yard)
    forward = FifoPolicy().choose(state, yard)
    reverse = FifoPolicy().choose(
        state, replace(yard, loaders=yard.loaders[::-1], stops=yard.stops[::-1])
    )
    assert forward == reverse
    policy = FifoPolicy()
    policy.choose(state, yard)
    snapshot = policy.get_state()
    snapshot["refill_active"]["mill"] = "changed"
    assert policy.get_state()["refill_active"]["mill"] != "changed"
    assert policy.wake_levels(state, yard)["mill"] == 25


@pytest.mark.parametrize(
    "parameters",
    [
        {"low_fraction": 0.7},
        {"target_fraction": 0.9},
        {"buffer_cycles": -1},
        {"low_fraction": float("nan")},
        {"full_pile_fraction": 0},
    ],
)
def test_invalid_fifo_parameters_are_rejected(parameters):
    with pytest.raises(ValueError, match=r"Require|must"):
        FifoParameters(**parameters)


@pytest.mark.parametrize(
    "filename",
    [
        "archive/legacy_single_bay_14h/experiment.yaml",
        "experiments/validation/1area_multi_mill__004trucks_mixed_stock_120min.yaml",
        "experiments/traffic/1area_shared__003trucks_1loader_180min.yaml",
        "experiments/traffic/1area_shared__003trucks_2loaders_180min.yaml",
        "experiments/traffic/1area_shared__015trucks_1loader_180min.yaml",
        "experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml",
        "experiments/traffic/1area_shared__045trucks_1loader_180min.yaml",
        "experiments/traffic/1area_shared__045trucks_2loaders_180min.yaml",
    ],
)
def test_fifo_scenarios_preserve_physical_invariants(filename):
    from mill_yard.config.loader import load_yard_scenario

    scenario = load_yard_scenario(Path(__file__).resolve().parents[2] / "configs" / filename)
    result = run_yard(scenario, FifoPolicy(), record="summary", validate=True)
    assert result.state.time == scenario.horizon
    assert result.policy_parameters["dispatch_rule"] == "infeed_full_pile_fifo"


def test_policy_wake_uses_quarter_capacity_even_if_scenario_low_is_different(yard):
    scenario = replace(yard, infeeds=(replace(yard.infeeds[0], initial=80),), wake_mode="policy")
    result = run_yard(scenario, FifoPolicy(), record="events", validate=True)
    first_work = next(e for e in result.events if e["event"] == "decision" and e["assignments"])
    assert first_work["time"] == 55  # 80 t - 55 min * 1 t/min = 25 t.
    assert all(a["destination_kind"] == "infeed" for a in first_work["assignments"])
    assert not any(e["event"] == "interval_wake" for e in result.events)


def test_fifo_run_is_reproducible_with_fresh_policy_memory(yard):
    scenario = replace(yard, infeeds=(replace(yard.infeeds[0], initial=0),))
    first = run_yard(scenario, FifoPolicy(), record="snapshots", validate=True)
    second = run_yard(scenario, FifoPolicy(), record="snapshots", validate=True)
    assert first.events == second.events
