"""Independent admission, same-time events, records, and area metrics."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from mill_yard.evaluations.metrics import summary
from mill_yard.model.invariants import check_invariants
from mill_yard.model.scenario import UnloadingAreaSpec
from mill_yard.model.types import Arrival, Point
from mill_yard.policies.fifo import FifoPolicy
from mill_yard.policies.human_controlled import HumanControlledPolicy
from mill_yard.simulation.engine import YardSimulation, run_yard


class IdlePolicy:
    def choose(self, state, context):
        return []

    def wake_levels(self, state, context):
        return {}


def test_no_cross_area_admission_even_when_other_area_has_free_bays(multi_area_yard):
    yard = replace(
        multi_area_yard, arrivals=tuple(Arrival(0, 10, f"n{i}", "north") for i in range(4))
    )
    result = run_yard(yard, IdlePolicy(), validate=True)
    assert result.state.stops == {"north-1": "n0", "north-2": "n1", "south-1": None}
    assert result.state.queues == {"north": ["n2", "n3"], "south": []}


def test_next_available_stop_within_area_and_fifo_refill(multi_area_yard):
    result = run_yard(multi_area_yard, FifoPolicy(), validate=True)
    entered = [e for e in result.events if e["event"] == "stop_enter"]
    assert next(e for e in entered if e["truck"] == "n0")["stop"] == "north-1"
    assert next(e for e in entered if e["truck"] == "n1")["stop"] == "north-2"
    assert next(e for e in entered if e["truck"] == "s1")["stop"] == "south-1"
    assert [e["truck"] for e in entered if e["area_id"] == "north"] == ["n0", "n1", "n2", "n3"]
    assert len(result.completed_trucks) == 6


def test_same_time_arrivals_in_all_areas_settle_before_human_request(multi_area_yard):
    yard = replace(
        multi_area_yard,
        arrivals=tuple(Arrival(0, 10, a.id, a.area_id) for a in multi_area_yard.arrivals),
    )
    sim = YardSimulation(yard, HumanControlledPolicy(), validate=True)
    sim.step()
    assert sim.pending_decision is not None
    assert len(sim.pending_decision.state.trucks) == 6
    assert sim.pending_decision.state.queues == {"north": ["n2", "n3"], "south": ["s1"]}


def test_arrival_at_departure_time_admits_only_to_its_own_area(multi_area_yard):
    yard = replace(
        multi_area_yard,
        arrivals=(Arrival(0, 10, "first", "north"), Arrival(1, 10, "south", "south")),
        horizon=1,
    )
    result = run_yard(yard, FifoPolicy(), validate=True)
    assert result.state.stops["south-1"] == "south"
    assert result.state.stops["north-1"] is None


def test_area_invariants_catch_wrong_queue_and_duplicate_membership(multi_area_yard):
    yard = replace(
        multi_area_yard, arrivals=tuple(Arrival(0, 10, f"n{i}", "north") for i in range(4))
    )
    state = run_yard(yard, IdlePolicy(), validate=True).state
    wrong = deepcopy(state)
    wrong.queues["south"].append(wrong.queues["north"].pop())
    with pytest.raises(AssertionError, match="wrong area"):
        check_invariants(wrong, yard)
    duplicate = deepcopy(state)
    duplicate.queues["south"].append(duplicate.queues["north"][0])
    with pytest.raises(AssertionError, match="Duplicate"):
        check_invariants(duplicate, yard)


def test_area_summary_waits_sum_to_global_and_records_are_versioned(multi_area_yard):
    result = run_yard(
        replace(multi_area_yard, horizon=15), FifoPolicy(), record="snapshots", validate=True
    )
    metrics = summary(result)
    assert sum(a["truck_wait_total"] for a in metrics["unloading_areas"].values()) == pytest.approx(
        metrics["completed_truck_wait_total"] + metrics["unfinished_truck_wait_so_far"]
    )
    assert sum(a["waiting_for_bay"] for a in metrics["unloading_areas"].values()) == len(
        result.state.queue
    )
    assert metrics["truck_wait_so_far_max"] >= metrics["truck_wait_so_far_p95"] >= 0
    assert result.events[0]["schema_version"] == 4
    assert "queues" in result.events[0]["state"]
    assert all("area_id" in t for t in metrics["completed_trucks"] + metrics["unfinished_trucks"])


def test_default_legacy_area_is_normalized_and_aggregate_view_is_not_mutable(tiny_yard):
    assert [a.id for a in tiny_yard.unloading_areas] == ["main"]
    state = run_yard(tiny_yard, IdlePolicy()).state
    before = asdict(state)
    state.queue.append("fake")
    assert asdict(state) == before


@pytest.mark.parametrize(
    "mutation", ["unknown_arrival", "unknown_stop", "no_stops", "duplicate", "invalid_point"]
)
def test_invalid_area_configuration_is_rejected(multi_area_yard, mutation):
    def changed():
        if mutation == "unknown_arrival":
            replace(multi_area_yard, arrivals=(Arrival(0, 5, "bad", "missing"),))
        elif mutation == "unknown_stop":
            replace(multi_area_yard, stops=(replace(multi_area_yard.stops[0], area_id="missing"),))
        elif mutation == "no_stops":
            replace(
                multi_area_yard,
                unloading_areas=(*multi_area_yard.unloading_areas, UnloadingAreaSpec("unused")),
            )
        elif mutation == "duplicate":
            replace(multi_area_yard, unloading_areas=(multi_area_yard.unloading_areas[0],) * 2)
        else:
            replace(
                multi_area_yard,
                unloading_areas=(
                    UnloadingAreaSpec("north", Point(float("nan"), 0)),
                    multi_area_yard.unloading_areas[1],
                ),
            )

    with pytest.raises(ValueError, match=r"area|Queue"):
        changed()
