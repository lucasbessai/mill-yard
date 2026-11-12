"""Displayed metrics agree with recorded metrics, including partial work."""

from dataclasses import asdict, replace

import pytest

from mill_yard.evaluations.metrics import summary
from mill_yard.gui.presentation import live_metrics, metric_breakdown, mill_starvation, truck_wait
from mill_yard.gui.session import SimulationSession
from mill_yard.model.state import TruckState
from mill_yard.model.types import Arrival, Point
from mill_yard.policies.threshold import ThresholdPolicy


def test_truck_wait_excludes_pickup_service_and_continues_between_pickups():
    truck = TruckState("t", 0, 10, pickup_service_time=3, busy_since=5, active_pickups=2)
    assert truck_wait(truck, 12) == 2  # Active pickups share one service interval.
    truck.pickup_service_time = 10
    truck.busy_since = None
    assert truck_wait(truck, 15) == 5


def test_display_counters_include_partial_travel_without_mutating_state(tiny_yard):
    session = SimulationSession(tiny_yard, ThresholdPolicy, validate=True)
    session.step()  # t=1: both pickups complete and loaded travel starts.
    state = session.simulation.state
    before = asdict(state)
    metrics = live_metrics(session.simulation, 1.5)
    assert metrics["travel_distance"] == pytest.approx(10)
    assert metrics["travel_time"] == pytest.approx(1)
    assert metrics["truck_wait"] == 0
    assert asdict(state) == before


def test_display_totals_match_summary_at_every_event(tiny_yard):
    scenario = replace(
        tiny_yard, infeeds=(replace(tiny_yard.infeeds[0], initial=0, consumption_rate=1),)
    )
    session = SimulationSession(scenario, ThresholdPolicy, validate=True)
    while not session.simulation.finished:
        sim = session.simulation
        metrics, recorded = live_metrics(sim, sim.state.time), summary(sim.result())
        assert metrics["truck_wait"] == pytest.approx(
            recorded["completed_truck_wait_total"] + recorded["unfinished_truck_wait_so_far"]
        )
        assert metrics["travel_distance"] == pytest.approx(recorded["loader_travel_distance_total"])
        assert metrics["travel_time"] == pytest.approx(recorded["loader_travel_time_total"])
        assert metrics["starvation"] == pytest.approx(sum(recorded["starvation"].values()))
        session.step()


def test_display_starvation_accumulates_between_events(tiny_yard):
    scenario = replace(
        tiny_yard, infeeds=(replace(tiny_yard.infeeds[0], initial=0, consumption_rate=1),)
    )
    session = SimulationSession(scenario, ThresholdPolicy)
    assert live_metrics(session.simulation, 0.5)["starvation"] == pytest.approx(0.5)


def test_individual_metrics_sum_to_totals_including_queued_and_completed_trucks(tiny_yard):
    scenario = replace(
        tiny_yard,
        arrivals=tuple(Arrival(0, 10, name) for name in ("a", "b", "c")),
        infeeds=(
            replace(tiny_yard.infeeds[0], initial=0, consumption_rate=1),
            replace(
                tiny_yard.infeeds[0],
                id="other",
                location=Point(30, 0),
                initial=2,
                consumption_rate=0.5,
            ),
        ),
    )
    session = SimulationSession(scenario, ThresholdPolicy, validate=True)
    while True:
        sim = session.simulation
        time = (sim.state.time + sim.next_time) / 2
        before = asdict(sim.state)
        details, totals = metric_breakdown(sim, time), live_metrics(sim, time)
        assert sum(row["waiting_time"] for row in details["trucks"]) == pytest.approx(
            totals["truck_wait"]
        )
        assert sum(row["travel_distance"] for row in details["loaders"]) == pytest.approx(
            totals["travel_distance"]
        )
        assert sum(row["travel_time"] for row in details["loaders"]) == pytest.approx(
            totals["travel_time"]
        )
        assert sum(row["starvation"] for row in details["mills"]) == pytest.approx(
            totals["starvation"]
        )
        assert {row["id"] for row in details["trucks"]} == {"a", "b", "c"}
        assert asdict(sim.state) == before
        if sim.state.time == 1:
            assert {row["status"] for row in details["trucks"]} == {
                "completed",
                "at stop",
                "queued",
            }
        if sim.finished:
            break
        session.step()


def test_breakdown_is_detached_and_inactive_mills_do_not_accumulate(tiny_yard):
    session = SimulationSession(tiny_yard, ThresholdPolicy)
    state = session.simulation.state
    before = asdict(state)
    details = metric_breakdown(session.simulation, 0.5)
    details["trucks"][0]["waiting_time"] = 1000
    details["loaders"][0]["travel_distance"] = 1000
    assert asdict(state) == before
    assert details["mills"] == [{"id": "mill", "status": "inactive", "starvation": 0.0}]


def test_mill_starvation_preserves_previous_time_and_accounts_for_depletion(tiny_yard):
    session = SimulationSession(tiny_yard, ThresholdPolicy)
    state = session.simulation.state
    state.starvation["mill"] = 4
    state.infeeds["mill"].amount = 2
    spec = replace(tiny_yard.infeeds[0], consumption_rate=1)
    assert mill_starvation(state, spec, 1) == 4
    assert mill_starvation(state, spec, 3) == 5
