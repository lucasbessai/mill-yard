"""Tests for encoded decisions, sequence dispatch, and terminal scoring."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mill_yard.model.feasibility import candidates
from mill_yard.model.types import Arrival, Point
from mill_yard.model.yard import (
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
from mill_yard.policies.joint import ThresholdPolicy
from mill_yard.records.persistence import summary
from mill_yard.searchtree import decode_decisions, encode_decisions, workflow
from mill_yard.searchtree.blackbox import make_omads_blackbox
from mill_yard.searchtree.calibration import calibrate_random_paths
from mill_yard.searchtree.objective import (
    MetricStatistics,
    ObjectiveStatistics,
    ObjectiveWeights,
    normalized_objective_value,
    objective_value,
)
from mill_yard.searchtree.optimizer import omads_configuration
from mill_yard.searchtree.policy import DecisionSequencePolicy
from mill_yard.simulation.engine import run_yard


@pytest.fixture
def yard():
    """A small yard with one feasible truck-to-pile assignment."""
    origin = Point(0, 0)
    return YardScenario(
        horizon=2,
        arrivals=(Arrival(0, 10, "t"),),
        stops=(StopSpec("s", origin),),
        piles=(PileSpec("p", origin, 20, 0),),
        infeeds=(InfeedSpec("i", origin, 20, 0, 0, 1, 10),),
        loaders=(LoaderSpec("l", origin, 10, 10, 10, 10),),
    )


def dispatch_state(yard):
    """Construct a state with a stopped truck and one available loader."""
    return YardState(
        time=0,
        trucks={"t": TruckState("t", 0, 10, stop_id="s")},
        queue=[],
        stops={"s": "t"},
        piles={"p": InventoryState(0)},
        infeeds={"i": InventoryState(0)},
        loaders={"l": LoaderState("l", yard.loaders[0].location)},
    )


@pytest.mark.parametrize(
    ("digits", "base"),
    [([], 2), ([0], 2), ([2, 0, 4], 5), ([1, 1, 1, 1], 2)],
)
def test_encoding_round_trips_fixed_length_sequences(digits, base):
    encoded = encode_decisions(digits, base)
    assert decode_decisions(encoded, base, len(digits)) == digits


def test_encoding_validates_digits_and_decode_width():
    with pytest.raises(ValueError, match="at least 2"):
        encode_decisions([0], 1)
    with pytest.raises(ValueError, match="range"):
        encode_decisions([2], 2)
    with pytest.raises(ValueError, match="fit"):
        decode_decisions(4, base=2, length=2)


def test_sequence_policy_selects_candidate_in_model_order(yard):
    state = dispatch_state(yard)
    expected = candidates(state, yard)[0]
    policy = DecisionSequencePolicy([1], yard)

    assert policy.choose(state, yard) == [expected]
    assert policy.exhausted
    assert policy.choose(state, yard) == []
    assert policy.metadata()["decisions_used"] == 1


def test_sequence_policy_idle_and_infeasible_action(yard):
    state = dispatch_state(yard)
    idle = DecisionSequencePolicy([0], yard)
    invalid = DecisionSequencePolicy([2], yard)

    assert idle.choose(state, yard) == []
    assert idle.invalid_decisions == 0
    assert invalid.choose(state, yard) == []
    assert invalid.invalid_decisions == 1


def test_sequence_policy_validates_vector_codes(yard):
    with pytest.raises(ValueError, match="range"):
        DecisionSequencePolicy([3], yard)
    with pytest.raises(TypeError, match="integers"):
        DecisionSequencePolicy([1.5], yard)


def test_objective_uses_weighted_terminal_metrics(yard):
    scenario = replace(yard, horizon=1, arrivals=(Arrival(0, 10, "t"),))
    result = run_yard(scenario, ThresholdPolicy(), record="summary")
    metrics = summary(result)
    weights = ObjectiveWeights(starvation=2, truck_waiting=3, loader_travel=4)
    expected = (
        2 * sum(metrics["starvation"].values())
        + 3
        * (
            metrics["completed_truck_wait_total"]
            + metrics["unfinished_truck_wait_so_far"]
        )
        + 4 * metrics["loader_travel_distance_total"]
    )

    assert objective_value(result, weights) == pytest.approx(expected)


def test_objective_weights_must_be_finite_and_nonnegative():
    with pytest.raises(ValueError, match="finite and nonnegative"):
        ObjectiveWeights(truck_waiting=-1)
    with pytest.raises(ValueError, match="finite and nonnegative"):
        ObjectiveWeights(loader_travel=float("inf"))


def test_omads_blackbox_evaluates_fixed_length_vector(yard):
    blackbox = make_omads_blackbox(yard, decision_length=10)

    objective, constraints = blackbox([0.0] * 10)

    assert objective >= 0
    assert constraints == [0.0]


def test_omads_blackbox_validates_vector_length_and_action_bounds(yard):
    blackbox = make_omads_blackbox(yard, decision_length=2)
    with pytest.raises(ValueError, match="exactly 2"):
        blackbox([0])
    with pytest.raises(ValueError, match="action codes"):
        blackbox([0, 3])


def test_omads_configuration_uses_vector_length_and_action_bounds(yard, tmp_path):
    configuration = omads_configuration(yard, post_dir=tmp_path)

    assert len(configuration["param"]["baseline"]) == 10
    assert configuration["param"]["lb"] == [0.0] * 10
    assert configuration["param"]["ub"] == [2.0] * 10
    assert configuration["options"]["budget"] == 20
    assert callable(configuration["evaluator"]["blackbox"])


def test_random_path_calibration_is_reproducible_and_reports_metric_statistics(yard):
    first = calibrate_random_paths(yard, samples=3, decision_length=2, seed=19)
    second = calibrate_random_paths(yard, samples=3, decision_length=2, seed=19)

    assert first == second
    assert first.sample_count == 3
    assert first.action_code_count == 3
    assert len(first.observations) == 3
    assert first.statistics.starvation.maximum >= first.statistics.starvation.mean
    assert all(len(item.decision_codes) == 2 for item in first.observations)
    assert all(
        0 <= code < first.action_code_count
        for item in first.observations
        for code in item.decision_codes
    )


def test_normalized_objective_scores_costs_in_random_path_standard_deviations(yard):
    scenario = replace(yard, horizon=1)
    policy = DecisionSequencePolicy([0], scenario)
    result = run_yard(scenario, policy, record="summary")
    reference = ObjectiveStatistics(
        starvation=MetricStatistics(0, 0, 1, 0),
        truck_waiting=MetricStatistics(2, 4, 2, 0),
        loader_travel=MetricStatistics(0, 0, 1, 0),
    )

    assert normalized_objective_value(result, reference) == pytest.approx(-0.5)


def test_search_workflow_calibrates_then_passes_statistics_to_optimizer(yard, tmp_path, monkeypatch):
    calibration = calibrate_random_paths(yard, samples=2, decision_length=3, seed=7)
    calls = {}

    def fake_calibrate(scenario, **kwargs):
        calls["calibration"] = (scenario, kwargs)
        return calibration

    def fake_run_omads(scenario, **kwargs):
        calls["optimizer"] = (scenario, kwargs)
        return {"xmin": [1, 0, 2], "fmin": [-1.0]}

    monkeypatch.setattr(workflow, "calibrate_random_paths", fake_calibrate)
    monkeypatch.setattr(workflow, "run_omads", fake_run_omads)
    weights = ObjectiveWeights(starvation=2, truck_waiting=3, loader_travel=4)

    result = workflow.run_search_workflow(
        yard,
        decision_length=3,
        samples=2,
        budget=11,
        seed=7,
        weights=weights,
        post_dir=tmp_path,
    )

    assert calls["calibration"] == (
        yard,
        {"samples": 2, "decision_length": 3, "seed": 7},
    )
    assert calls["optimizer"] == (
        yard,
        {
            "decision_length": 3,
            "budget": 11,
            "seed": 7,
            "weights": weights,
            "normalization": calibration.statistics,
            "post_dir": tmp_path,
        },
    )
    assert result.optimizer_result == {"xmin": [1, 0, 2], "fmin": [-1.0]}
    expected_json = json.loads(json.dumps(calibration.to_dict()))
    assert json.loads(result.calibration_path.read_text()) == expected_json


def test_searchtreeoptimizer_cli_invokes_workflow_with_options(yard, monkeypatch, capsys):
    from mill_yard import cli

    observed = {}
    fake_result = SimpleNamespace(
        calibration=SimpleNamespace(sample_count=5, decision_length=4, statistics="stats"),
        calibration_path="calibration.json",
        optimizer_result={"fmin": [-2.0]},
    )

    monkeypatch.setattr(cli, "load_yard_scenario", lambda path: yard)

    def fake_workflow(scenario, **kwargs):
        observed["scenario"] = scenario
        observed.update(kwargs)
        return fake_result

    monkeypatch.setattr(workflow, "run_search_workflow", fake_workflow)

    assert cli.main(
        [
            "searchtreeoptimizer",
            "scenario.yaml",
            "--decision-length",
            "4",
            "--samples",
            "5",
            "--budget",
            "12",
            "--seed",
            "8",
            "--starvation-weight",
            "2",
            "--truck-waiting-weight",
            "3",
            "--loader-travel-weight",
            "4",
            "--post-dir",
            "search-runs",
        ]
    ) == 0

    assert observed["scenario"] is yard
    assert observed["decision_length"] == 4
    assert observed["samples"] == 5
    assert observed["budget"] == 12
    assert observed["seed"] == 8
    assert observed["weights"] == ObjectiveWeights(2, 3, 4)
    assert observed["post_dir"] == "search-runs"
    assert "OMADS result" in capsys.readouterr().out
