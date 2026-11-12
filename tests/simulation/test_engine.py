"""Behavioral checks for queues, phased work, and multi-asset accounting."""

import json
from dataclasses import replace

import pytest

from mill_yard.evaluations.metrics import summary
from mill_yard.model.feasibility import validate_joint
from mill_yard.model.scenario import InfeedSpec, LoaderSpec, PileSpec, StopSpec, YardScenario
from mill_yard.model.state import Assignment
from mill_yard.model.types import Arrival, Point
from mill_yard.policies.random import RandomPolicy
from mill_yard.policies.threshold import ThresholdPolicy
from mill_yard.records.persistence import save_run
from mill_yard.simulation.engine import run_yard


@pytest.fixture
def yard():
    """A small yard with easy-to-calculate phase durations."""
    p = Point(0, 0)
    return YardScenario(
        horizon=30,
        arrivals=(),
        stops=(StopSpec("s", p, 2),),
        piles=(PileSpec("p", p, 100, 0, 4),),
        infeeds=(InfeedSpec("i", p, 50, 20, 0, 5, 15, 2),),
        loaders=(LoaderSpec("l1", p, 10, 10, 10, 10),),
        wake_interval=5,
    )


def test_fifo_queue_and_stop_release(yard):
    s = replace(yard, arrivals=(Arrival(0, 10, "a"), Arrival(0, 10, "b"), Arrival(0, 10, "c")))
    result = run_yard(s, ThresholdPolicy())
    assert [t["id"] for t in result.completed_trucks] == ["a", "b", "c"]
    assert [t["entered_stop_at"] for t in result.completed_trucks] == [0, 1, 3]
    assert [t["queue_wait"] for t in result.completed_trucks] == [0, 1, 3]


def test_arrival_during_active_pickup_does_not_interrupt_it(yard):
    scenario = replace(
        yard,
        arrivals=(Arrival(0, 10, "a"), Arrival(0.5, 10, "b")),
        horizon=3,
    )
    result = run_yard(scenario, ThresholdPolicy())
    events = [(event["time"], event["event"]) for event in result.events]
    assert (0.5, "arrival") in events
    assert (1, "pickup_complete") in events
    assert (2, "deposit_complete") in events
    assert result.completed_trucks[0]["id"] == "a"
    assert result.completed_trucks[0]["completed_at"] == 1
    assert result.completed_trucks[1]["id"] == "b"
    assert result.completed_trucks[1]["completed_at"] == 3


def test_initial_inventory_cannot_exceed_capacity(yard):
    with pytest.raises(ValueError, match="Invalid inventory or capacity"):
        replace(yard, piles=(replace(yard.piles[0], initial=101),))


def test_concurrent_truck_pickup_and_capacity_reservations(yard):
    loader2 = replace(yard.loaders[0], id="l2")
    s = replace(yard, arrivals=(Arrival(0, 20, "a"),), loaders=(*yard.loaders, loader2), horizon=1)
    result = run_yard(s, ThresholdPolicy())
    starts = [e for e in result.events if e["event"] == "pickup_start"]
    assert len(starts) == 2
    assert [e["time"] for e in starts] == [0, 0]
    assert result.completed_trucks[0]["completed_at"] == 1
    assert result.completed_trucks[0]["pickup_service_time"] == 1
    assert sum(x.carried for x in result.state.loaders.values()) == 20
    assert result.state.piles["p"].reserved_incoming == 20


def test_single_access_slot_serializes_pickup(yard):
    s = replace(
        yard,
        stops=(replace(yard.stops[0], max_loaders=1),),
        arrivals=(Arrival(0, 20, "a"),),
        loaders=(yard.loaders[0], replace(yard.loaders[0], id="l2")),
        horizon=5,
    )
    result = run_yard(s, ThresholdPolicy())
    assert [e["time"] for e in result.events if e["event"] == "pickup_start"] == [0, 1]
    assert result.completed_trucks[0]["completed_at"] == 2


def test_capacity_is_shared_by_concurrent_assignments(yard):
    s = replace(
        yard,
        arrivals=(Arrival(0, 20, "a"),),
        piles=(replace(yard.piles[0], capacity=15),),
        loaders=(yard.loaders[0], replace(yard.loaders[0], id="l2")),
        horizon=0.4,
    )
    result = run_yard(s, ThresholdPolicy())
    assert result.state.piles["p"].reserved_incoming == 15
    assert result.state.trucks["a"].reserved_quantity == 15


def test_full_pile_empty_infeed_conserves_material(yard):
    s = replace(
        yard,
        piles=(replace(yard.piles[0], initial=100),),
        infeeds=(replace(yard.infeeds[0], initial=0, consumption_rate=1),),
        arrivals=(Arrival(0, 15, "a"),),
        horizon=20,
    )
    result = run_yard(s, ThresholdPolicy())
    total = (
        sum(x.remaining_quantity for x in result.state.trucks.values())
        + sum(x.amount for x in result.state.piles.values())
        + sum(x.amount for x in result.state.infeeds.values())
        + sum(x.carried for x in result.state.loaders.values())
        + sum(result.state.consumed.values())
    )
    assert total == pytest.approx(115)
    assert result.state.starvation["i"] == pytest.approx(2)
    assert result.completed_trucks


def test_interval_wakes_and_policy_requested_crossing(yard):
    s = replace(
        yard,
        piles=(replace(yard.piles[0], initial=50),),
        infeeds=(replace(yard.infeeds[0], initial=10, consumption_rate=1),),
        horizon=6,
        wake_interval=2,
    )
    interval = run_yard(s, ThresholdPolicy())
    assert [e["time"] for e in interval.events if e["event"] == "interval_wake"] == [2, 4, 6]
    policy = run_yard(replace(s, wake_mode="policy"), ThresholdPolicy())
    assert not [e for e in policy.events if e["event"] == "interval_wake"]
    assert any(e["event"] == "threshold_crossing" and e["time"] == 5 for e in policy.events)
    assert any(e["event"] == "decision_before" and e["time"] == 5 for e in policy.events)


def test_simultaneous_arrival_and_completion_before_decision(yard):
    s = replace(yard, arrivals=(Arrival(0, 10, "a"), Arrival(1, 10, "b")), horizon=2)
    result = run_yard(s, ThresholdPolicy())
    at_one = [e for e in result.events if e["time"] == 1]
    decision = next(i for i, e in enumerate(at_one) if e["event"] == "decision_before")
    assert {"arrival", "pickup_complete", "truck_departure", "stop_enter"} <= {
        e["event"] for e in at_one[:decision]
    }
    assert at_one[decision]["state"]["stops"]["s"] == "b"


def test_random_seed_and_snapshots_are_independent(yard):
    s = replace(yard, arrivals=(Arrival(0, 20, "a"),), piles=(replace(yard.piles[0], initial=30),))
    a = run_yard(s, RandomPolicy(42))
    b = run_yard(s, RandomPolicy(42))
    assert a.events == b.events
    assert a.events[0]["state"]["piles"]["p"]["amount"] == 30
    assert a.events[0]["state"]["trucks"] == {}


def test_partial_travel_and_unfinished_summary(yard):
    s = replace(
        yard, arrivals=(Arrival(0, 10, "a"),), stops=(StopSpec("s", Point(100, 0)),), horizon=5
    )
    result = run_yard(s, ThresholdPolicy())
    metrics = summary(result)
    assert metrics["loaders"]["l1"]["total_travel_distance"] == 50
    assert metrics["loaders"]["l1"]["total_travel_time"] == 5
    assert metrics["loaders"]["l1"]["completed_travel_distance"] == 0
    assert metrics["loaders"]["l1"]["current_leg_distance"] == 100
    assert result.state.loaders["l1"].completed_travel_distance == 0
    assert metrics["unfinished_trucks"][0]["waiting_time_so_far"] == 5


def test_persisted_log_and_trace(yard, tmp_path, capsys):
    result = run_yard(yard, ThresholdPolicy(), trace=True)
    assert "initial" in capsys.readouterr().out
    path = save_run(result, tmp_path)
    assert json.loads((path / "metadata.json").read_text(encoding="utf-8"))["schema_version"] == 4
    events = [
        json.loads(line)
        for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events == result.events
    assert events[-1]["schema_version"] == 4
    inventory = events[-1]["state"]["piles"]["p"]
    assert "reserved_incoming" in inventory
    assert "incoming" not in inventory


def test_joint_validation_rejects_overcommit_without_mutation(yard):
    s = replace(
        yard,
        arrivals=(Arrival(0, 20, "a"),),
        piles=(replace(yard.piles[0], capacity=15),),
        loaders=(yard.loaders[0], replace(yard.loaders[0], id="l2")),
        horizon=0.1,
    )

    class IdlePolicy:
        def choose(self, state, scenario):
            return []

        def wake_levels(self, state, scenario):
            return {}

    result = run_yard(s, IdlePolicy())
    assignments = [
        Assignment("l1", "truck", "a", "pile", "p", 10),
        Assignment("l2", "truck", "a", "pile", "p", 10),
    ]
    with pytest.raises(ValueError, match="infeasible"):
        validate_joint(result.state, s, assignments)
    assert result.state.piles["p"].reserved_incoming == 0


def test_many_trucks_two_stops_and_horizon(yard):
    arrivals = tuple(Arrival(0, 10, f"t{i:02}") for i in range(12))
    s = replace(
        yard, arrivals=arrivals, stops=(yard.stops[0], StopSpec("s2", Point(0, 0))), horizon=1
    )
    result = run_yard(s, ThresholdPolicy())
    assert len(result.state.queue) == 9
    assert len(result.completed_trucks) == 1
    assert len(result.state.trucks) == 11
    assert (
        sum(x.remaining_quantity for x in result.state.trucks.values())
        + sum(x.carried for x in result.state.loaders.values())
        == 120
    )


def test_multiple_assets_and_policies_conserve(yard):
    s = replace(
        yard,
        arrivals=(Arrival(0, 20, "a"), Arrival(1, 20, "b")),
        stops=(yard.stops[0], StopSpec("s2", Point(10, 0))),
        piles=(replace(yard.piles[0], initial=30), PileSpec("p2", Point(20, 0), 100, 20)),
        infeeds=(
            replace(yard.infeeds[0], consumption_rate=1),
            InfeedSpec("i2", Point(30, 0), 50, 10, 0.5, 5, 15),
        ),
        loaders=(yard.loaders[0], replace(yard.loaders[0], id="l2")),
    )
    for policy in (ThresholdPolicy(), RandomPolicy(3)):
        r = run_yard(s, policy, validate=True)
        total = (
            sum(t.remaining_quantity for t in r.state.trucks.values())
            + sum(i.amount for i in r.state.piles.values())
            + sum(i.amount for i in r.state.infeeds.values())
            + sum(loader.carried for loader in r.state.loaders.values())
            + sum(r.state.consumed.values())
        )
        assert total == pytest.approx(120)


def test_full_pile_and_infeed_leave_trucks_waiting(yard):
    s = replace(
        yard,
        arrivals=(Arrival(0, 10, "a"), Arrival(0, 10, "b")),
        piles=(replace(yard.piles[0], initial=100),),
        infeeds=(replace(yard.infeeds[0], initial=50),),
        horizon=10,
    )
    r = run_yard(s, RandomPolicy(1))
    assert r.state.stops["s"] == "a"
    assert r.state.queue == ["b"]
    assert r.state.loaders["l1"].assignment is None
    assert not r.completed_trucks


def test_invalid_joint_decision_does_not_partially_commit(yard):
    s = replace(
        yard,
        arrivals=(Arrival(0, 20, "a"),),
        piles=(replace(yard.piles[0], capacity=15),),
        loaders=(yard.loaders[0], replace(yard.loaders[0], id="l2")),
    )

    class InvalidPolicy:
        def choose(self, state, scenario):
            return [
                Assignment("l1", "truck", "a", "pile", "p", 10),
                Assignment("l2", "truck", "a", "pile", "p", 10),
            ]

        def wake_levels(self, state, scenario):
            return {}

    from mill_yard.simulation.engine import YardSimulation

    simulation = YardSimulation(s, InvalidPolicy())
    with pytest.raises(ValueError, match="infeasible"):
        simulation.run()
    assert simulation.state.piles["p"].reserved_incoming == 0
    assert all(loader.assignment is None for loader in simulation.state.loaders.values())


def test_horizon_includes_pickup_completion(yard):
    s = replace(yard, arrivals=(Arrival(0, 10, "a"),), horizon=1)
    result = run_yard(s, ThresholdPolicy())
    assert result.completed_trucks[0]["completed_at"] == 1
    assert result.state.loaders["l1"].carried == 10


def test_simultaneous_depletion_and_deposit_do_not_create_false_starvation(yard):
    scenario = replace(
        yard,
        horizon=8,
        wake_interval=1,
        piles=(replace(yard.piles[0], initial=20),),
        infeeds=(replace(yard.infeeds[0], initial=3, consumption_rate=1, low=2, target=12),),
    )
    result = run_yard(scenario, ThresholdPolicy(), validate=True)
    assert result.state.starvation["i"] == 0
    assert not [e for e in result.events if e["event"].startswith("starvation")]


def test_zero_demand_never_emits_starvation(yard):
    scenario = replace(
        yard,
        piles=(replace(yard.piles[0], initial=20),),
        infeeds=(replace(yard.infeeds[0], initial=0),),
    )
    result = run_yard(scenario, ThresholdPolicy())
    assert not [e for e in result.events if e["event"].startswith("starvation")]


def test_repeated_ticks_reuse_unchanged_boundaries(yard):
    from mill_yard.records.recorder import Recorder
    from mill_yard.simulation.engine import YardSimulation

    scenario = replace(
        yard,
        horizon=100,
        wake_interval=0.1,
        infeeds=(replace(yard.infeeds[0], capacity=10000, initial=10000, consumption_rate=0.01),),
    )
    simulation = YardSimulation(scenario, ThresholdPolicy(), Recorder("summary"))
    simulation.run()
    assert len(simulation.env._queue) < 10


def test_policy_context_hides_arrival_schedule_and_can_decline_wakes(yard):
    class Observer:
        def choose(self, state, context):
            assert not hasattr(context, "arrivals")
            assert not hasattr(context, "scenario")
            return []

        def wake_levels(self, state, context):
            return {}

    scenario = replace(
        yard,
        horizon=8,
        wake_mode="policy",
        arrivals=(Arrival(20, 5, "future"),),
        infeeds=(replace(yard.infeeds[0], initial=10, low=5, consumption_rate=1),),
    )
    result = run_yard(scenario, Observer())
    assert [e["time"] for e in result.events if e["event"] == "decision"] == [0]
    assert not result.state.trucks


def test_ordered_handling_queues_are_present_in_state(yard):
    scenario = replace(
        yard,
        horizon=0.25,
        stops=(replace(yard.stops[0], max_loaders=1),),
        arrivals=(Arrival(0, 20, "a"),),
        loaders=(yard.loaders[0], replace(yard.loaders[0], id="l2")),
    )
    result = run_yard(scenario, ThresholdPolicy(), validate=True)
    assert result.state.handling["stop:s"].users == ["l1"]
    assert result.state.handling["stop:s"].queue == ["l2"]


def test_streamed_events_survive_save_without_retaining_history(yard, tmp_path):
    path = tmp_path / "stream.jsonl"
    result = run_yard(yard, ThresholdPolicy(), events_path=path, retain_events=False)
    assert result.events == []
    before = path.read_text(encoding="utf-8")
    saved = save_run(result, run_path=tmp_path)
    assert (saved / "events.jsonl").read_text(encoding="utf-8") == before
    assert json.loads(before.splitlines()[-1])["event"] == "horizon"


def test_terminal_metrics_include_outstanding_material_and_jobs(yard):
    scenario = replace(yard, horizon=1, arrivals=(Arrival(0, 20, "a"),))
    metrics = summary(run_yard(scenario, ThresholdPolicy()))
    assert metrics["unfinished_truck_quantity"] == 10
    assert metrics["onboard_quantity"] == 10
    assert metrics["active_loader_jobs"] == 1
    assert metrics["estimated_outstanding_loader_minutes"] > 0


def test_horizon_arrival_is_registered_without_starting_new_work(yard):
    result = run_yard(replace(yard, horizon=5, arrivals=(Arrival(5, 10, "a"),)), ThresholdPolicy())
    assert "a" in result.state.trucks
    assert result.state.trucks["a"].reserved_quantity == 0
    assert result.state.loaders["l1"].assignment is None


def test_shared_timing_matches_execution_without_contention(yard):
    from mill_yard.model.timing import job_duration
    from mill_yard.simulation.engine import YardSimulation

    scenario = replace(
        yard,
        piles=(replace(yard.piles[0], initial=10, location=Point(10, 0)),),
        infeeds=(replace(yard.infeeds[0], initial=0, location=Point(20, 0)),),
    )
    simulation = YardSimulation(scenario, ThresholdPolicy(), validate=True)
    assignment = Assignment("l1", "pile", "p", "infeed", "i", 10)
    travel, total = job_duration(assignment, simulation.state, simulation.context)
    result = simulation.run()
    completion = next(e for e in result.events if e["event"] == "deposit_complete")
    assert travel == 2
    assert total == 4
    assert completion["time"] == total
