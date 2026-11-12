"""Incremental execution must agree with batch runs and pause safely for input."""

from dataclasses import asdict, replace

import pytest

from mill_yard.model.feasibility import candidates
from mill_yard.policies.human_controlled import HumanControlledPolicy
from mill_yard.policies.threshold import ThresholdPolicy
from mill_yard.simulation.engine import YardSimulation, run_yard


def test_timestamp_steps_match_batch_execution(tiny_yard):
    batch = run_yard(tiny_yard, ThresholdPolicy(), validate=True)
    incremental = YardSimulation(tiny_yard, ThresholdPolicy(), validate=True)
    incremental.start()
    while not incremental.finished:
        incremental.step()
    assert asdict(incremental.state) == asdict(batch.state)
    assert incremental.result().events == batch.events


def test_human_request_freezes_clock_and_rejects_invalid_commit(tiny_yard):
    simulation = YardSimulation(tiny_yard, HumanControlledPolicy(), validate=True)
    simulation.step()
    assert simulation.pending_decision is not None
    before = asdict(simulation.state)
    simulation.step()
    assert asdict(simulation.state) == before
    option = candidates(simulation.state, simulation.context)[0]
    with pytest.raises(ValueError, match="infeasible"):
        simulation.submit_decision([replace(option, quantity=100)])
    assert simulation.pending_decision is not None
    assert asdict(simulation.state) == before
    simulation.submit_decision([option])
    assert simulation.pending_decision is None
    simulation.step()
    assert simulation.state.time == 0
    assert simulation.state.loaders[option.loader_id].phase == "pickup"


def test_wait_advances_to_a_later_decision(tiny_yard):
    simulation = YardSimulation(tiny_yard, HumanControlledPolicy())
    simulation.step()
    simulation.submit_decision([])
    simulation.step()
    assert simulation.state.time == 2
    assert simulation.pending_decision is not None
    assert simulation.state.trucks["truck"].reserved_quantity == 0


def test_all_same_time_pickups_are_settled_before_a_step_returns(tiny_yard):
    simulation = YardSimulation(tiny_yard, ThresholdPolicy(), validate=True)
    simulation.step()  # Time zero, including zero-duration travel to the source.
    simulation.step()  # Both pickups complete at time one.
    assert simulation.state.time == 1
    assert sum(loader.carried for loader in simulation.state.loaders.values()) == 10
    assert simulation.state.stops["bay"] is None
    assert simulation.env.peek() > 1


def test_operator_can_wait_at_low_threshold_then_be_prompted_at_depletion(tiny_yard):
    scenario = replace(
        tiny_yard,
        wake_mode="policy",
        infeeds=(replace(tiny_yard.infeeds[0], initial=5, consumption_rate=1),),
    )
    simulation = YardSimulation(scenario, HumanControlledPolicy(), validate=True)
    simulation.step()
    assert simulation.state.time == 0
    simulation.submit_decision([])
    simulation.step()
    assert simulation.state.time == 3
    assert simulation.pending_decision is not None
    simulation.submit_decision([])
    simulation.step()
    assert simulation.state.time == 5
    assert simulation.pending_decision is not None
    assert simulation.state.infeeds["mill"].starved
