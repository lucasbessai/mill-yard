"""Deterministic queue allocation, per-loader affinity, and common priorities."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from mill_yard.model.feasibility import candidates, reserve, validate_joint
from mill_yard.model.queues import queue_work_counts
from mill_yard.model.state import Assignment, InventoryState, LoaderState, TruckState, YardState
from mill_yard.policies.base import PolicyContext
from mill_yard.policies.fifo import FifoPolicy
from mill_yard.policies.queue_first import EmptyQueueFirstPolicy, LongestQueueFirstPolicy
from mill_yard.policies.truck_dispatch import EmptyQueueFirstRule, LongestQueueFirstRule
from mill_yard.simulation.engine import run_yard


def observed(yard):
    return YardState(
        time=30,
        trucks={},
        queues={a.id: [] for a in yard.unloading_areas},
        stops={s.id: None for s in yard.stops},
        piles={p.id: InventoryState(p.initial) for p in yard.piles},
        infeeds={i.id: InventoryState(i.initial) for i in yard.infeeds},
        loaders={loader.id: LoaderState(loader.id, loader.location) for loader in yard.loaders},
    )


def add_truck(state, key, area, arrival, stop=None, quantity=10):
    state.trucks[key] = TruckState(key, arrival, quantity, area_id=area, stop_id=stop)
    if stop:
        state.stops[stop] = key
    else:
        state.queues[area].append(key)


def test_longest_queue_counts_queued_and_stopped_but_not_fully_reserved(multi_area_yard):
    state = observed(multi_area_yard)
    add_truck(state, "n", "north", 10, "north-1")
    add_truck(state, "n2", "north", 20)
    add_truck(state, "s", "south", 0, "south-1")
    rule = LongestQueueFirstRule()
    assert rule.select_pair(candidates(state, multi_area_yard), state, multi_area_yard)[1] == "n"
    reserve(state, multi_area_yard, Assignment("l1", "truck", "n", "pile", "p", 10))
    assert queue_work_counts(state) == {"north": 1, "south": 1}
    # North is no longer serviceable; queued n2 cannot use a South bay.
    assert rule.select_pair(candidates(state, multi_area_yard), state, multi_area_yard)[1] == "s"


def test_equal_length_uses_longest_actual_wait_before_arrival_and_area_id(multi_area_yard):
    state = observed(multi_area_yard)
    add_truck(state, "n", "north", 0, "north-1")
    add_truck(state, "s", "south", 5, "south-1")
    state.trucks["n"].pickup_service_time = 20
    rule = LongestQueueFirstRule()
    assert rule.select_pair(candidates(state, multi_area_yard), state, multi_area_yard)[1] == "s"
    state.trucks["n"].pickup_service_time = 5  # Both waited 25; North arrived first.
    assert rule.select_pair(candidates(state, multi_area_yard), state, multi_area_yard)[1] == "n"
    state.trucks["s"].arrived_at = 0
    state.trucks["s"].pickup_service_time = 5
    assert rule.select_pair(candidates(state, multi_area_yard), state, multi_area_yard)[1] == "n"


def test_empty_queue_rule_retains_independent_preferences(multi_area_yard):
    state = observed(multi_area_yard)
    add_truck(state, "n", "north", 10, "north-1")
    add_truck(state, "n2", "north", 20)
    add_truck(state, "s", "south", 0, "south-1")
    rule = EmptyQueueFirstRule()
    rule.preferred_areas = {"l1": "north", "l2": "south"}
    first = rule.select_pair(candidates(state, multi_area_yard), state, multi_area_yard)
    assert first == ("l2", "s")
    task = Assignment("l2", "truck", "s", "pile", "p", 10)
    reserve(state, multi_area_yard, task)
    rule.on_reserved(task, state)
    assert rule.preferred_areas == {"l1": "north"}  # South cleared for dispatch.
    assert rule.select_pair(candidates(state, multi_area_yard), state, multi_area_yard) == (
        "l1",
        "n",
    )


def test_blocked_preference_can_fall_back_without_losing_affinity(multi_area_yard):
    state = observed(multi_area_yard)
    add_truck(state, "waiting", "north", 0)
    add_truck(state, "s", "south", 5, "south-1")
    rule = EmptyQueueFirstRule()
    rule.preferred_areas = {"l1": "north"}
    options = [a for a in candidates(state, multi_area_yard) if a.loader_id == "l1"]
    assert rule.select_pair(options, state, multi_area_yard) == ("l1", "s")
    task = Assignment("l1", "truck", "s", "pile", "p", 10)
    reserve(state, multi_area_yard, task)
    rule.on_reserved(task, state)
    assert rule.preferred_areas == {"l1": "north"}
    saved = rule.get_state()
    saved["preferred_areas"].clear()
    assert rule.preferred_areas == {"l1": "north"}


def test_longest_queue_recomputes_counts_between_parallel_assignments(multi_area_yard):
    state = observed(multi_area_yard)
    add_truck(state, "n", "north", 10, "north-1")
    add_truck(state, "n2", "north", 20, "north-2")
    add_truck(state, "s", "south", 0, "south-1")
    before = asdict(state)
    chosen = LongestQueueFirstPolicy().choose(state, PolicyContext.from_scenario(multi_area_yard))
    assert [a.source_id for a in chosen] == ["n", "s"]  # 2:1, then 1:1 with oldest South.
    assert asdict(state) == before
    validate_joint(state, multi_area_yard, chosen)


def test_empty_queue_keeps_preference_through_mill_work(multi_area_yard):
    yard = replace(
        multi_area_yard,
        infeeds=(replace(multi_area_yard.infeeds[0], consumption_rate=1, initial=0),),
        piles=(replace(multi_area_yard.piles[0], initial=100),),
    )
    state = observed(yard)
    add_truck(state, "n", "north", 0, "north-1")
    policy = EmptyQueueFirstPolicy()
    policy.truck_rule.preferred_areas = {"l1": "north"}
    assert all(a.destination_kind == "infeed" for a in policy.choose(state, yard))
    assert policy.get_state()["truck_selection"]["preferred_areas"] == {"l1": "north"}


def test_three_rules_match_with_a_single_area(tiny_yard):
    traces = [
        run_yard(tiny_yard, policy(), record="events", validate=True).events
        for policy in (FifoPolicy, EmptyQueueFirstPolicy, LongestQueueFirstPolicy)
    ]
    assert traces[0] == traces[1] == traces[2]


def test_exhaustive_service_and_fifo_can_diverge_without_randomness(multi_area_yard):
    yard = replace(multi_area_yard, loaders=(multi_area_yard.loaders[0],))

    def truck_order(policy):
        result = run_yard(yard, policy(), record="events", validate=True)
        return [
            a["source_id"]
            for e in result.events
            if e["event"] == "decision"
            for a in e["assignments"]
            if a["source_kind"] == "truck"
        ]

    fifo, exhaustive = truck_order(FifoPolicy), truck_order(EmptyQueueFirstPolicy)
    assert fifo.index("s0") < fifo.index("n2")
    assert exhaustive.index("n2") < exhaustive.index("s0")
    assert exhaustive == truck_order(EmptyQueueFirstPolicy)


@pytest.mark.parametrize("policy", [EmptyQueueFirstPolicy, LongestQueueFirstPolicy])
def test_new_policies_run_with_invariants_and_are_reproducible(multi_area_yard, policy):
    first = run_yard(multi_area_yard, policy(), record="snapshots", validate=True)
    second = run_yard(multi_area_yard, policy(), record="snapshots", validate=True)
    assert first.events == second.events
    assert len(first.completed_trucks) == len(multi_area_yard.arrivals)
    assert first.seed is None


def test_area_order_and_seed_do_not_change_deterministic_ties(multi_area_yard):
    from mill_yard.policies.factory import create_policy

    first = run_yard(multi_area_yard, create_policy("longest-queue-first", seed=1), record="events")
    second = run_yard(
        multi_area_yard, create_policy("longest-queue-first", seed=99), record="events"
    )
    assert first.events == second.events
    state = observed(multi_area_yard)
    add_truck(state, "n", "north", 0, "north-1")
    add_truck(state, "s", "south", 0, "south-1")
    reordered = replace(
        multi_area_yard,
        unloading_areas=multi_area_yard.unloading_areas[::-1],
        stops=multi_area_yard.stops[::-1],
        loaders=multi_area_yard.loaders[::-1],
    )
    before = LongestQueueFirstPolicy().choose(state, multi_area_yard)
    after = LongestQueueFirstPolicy().choose(deepcopy(state), reordered)
    assert before == after
