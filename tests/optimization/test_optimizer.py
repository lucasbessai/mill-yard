"""Exercise both solvers and the shared simulation evaluation contract."""

import json
import runpy
import sys
from copy import deepcopy
from dataclasses import replace
from math import isfinite

import numpy as np
import pytest

from mill_yard.config.loader import DEFAULT_OBJECTIVE_PATH, load_yard_scenario
from mill_yard.optimization import optimizer
from mill_yard.optimization.objective import ObjectiveParameters, evaluate_objective
from mill_yard.optimization.optimizer import (
    OMADSParameters,
    OptimizerParameters,
    _to_coordinates,
    _to_theta,
    optimize_policy,
)
from mill_yard.policies.optimizable import FeatureParameters, OptimizablePolicy
from mill_yard.simulation.engine import run_yard


@pytest.mark.parametrize("theta", [*np.eye(4), [0.25, 0.4, 0.15, 0.2], [0, 0.5, 0, 0.5]])
def test_simplex_round_trip_including_boundaries(theta):
    actual = _to_theta(_to_coordinates(theta))
    assert actual == pytest.approx(theta)
    assert sum(actual) == pytest.approx(1)
    assert all(v >= 0 for v in actual)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"maxiter": -1},
        {"popsize": 0},
        {"seed": -1},
        {"tol": float("nan")},
        {"atol": -1},
        {"popsize": 1.5},
    ],
)
def test_invalid_search_settings(kwargs):
    with pytest.raises(ValueError, match="must be"):
        OptimizerParameters(**kwargs)


def test_search_improves_bad_baseline_and_replays_with_fixed_settings():
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    settings = OptimizerParameters(maxiter=2, popsize=3, seed=17)
    features = FeatureParameters(stockout_reference=20)
    objective = ObjectiveParameters(shortfall_weight=3)
    kwargs = {
        "initial_theta": [0, 0, 0, 1],
        "settings": settings,
        "method": "de",
        "features": features,
        "objective": objective,
    }
    result = optimize_policy(scenario, **kwargs)
    assert result == optimize_policy(scenario, **kwargs)
    assert sum(result.theta) == pytest.approx(1)
    assert all(0 <= v <= 1 for v in result.theta)
    assert result.score.total < result.initial_score.total
    replay = run_yard(scenario, OptimizablePolicy(result.theta, features), record="summary")
    assert evaluate_objective(replay, objective) == result.score
    assert result.simulations > 1
    assert result.function_evaluations <= (settings.maxiter + 1) * 3 * settings.popsize
    assert [h.generation for h in result.history] == list(range(result.generations + 1))
    totals = [h.objective for h in result.history]
    assert totals == sorted(totals, reverse=True)
    assert totals[-1] == pytest.approx(result.score.total)


def test_zero_generations_returns_best_initial_population_and_termination_reason():
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    result = optimize_policy(
        scenario, method="de", settings=OptimizerParameters(maxiter=0, popsize=1)
    )
    assert result.generations == 0
    assert not result.converged
    assert "maximum" in result.message.lower()
    assert result.score.total <= result.initial_score.total
    assert len(result.history) == 1


def test_population_can_converge_when_all_policies_have_same_outcomes():
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    scenario = replace(
        scenario,
        arrivals=(),
        infeeds=tuple(replace(i, consumption_rate=0) for i in scenario.infeeds),
    )
    # Only waiting is penalized, and there are no trucks.
    objective = ObjectiveParameters(wait_weight=1, distance_weight=0, shortfall_weight=0)
    result = optimize_policy(
        scenario,
        method="de",
        objective=objective,
        settings=OptimizerParameters(maxiter=3, popsize=2),
    )
    assert result.converged
    assert result.score.total == 0


@pytest.mark.parametrize("full_mads", [False, True])
def test_omads_reproducible_and_returns_simulated_weights(full_mads):
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    settings = OMADSParameters(budget=14, full_mads=full_mads)
    result = optimize_policy(scenario, omads_settings=settings)
    assert result == optimize_policy(scenario, omads_settings=settings)
    assert result.algorithm == ("OMADS (search + poll)" if full_mads else "OMADS (poll)")
    assert result.generations is None
    assert result.iterations >= 1
    assert len(result.theta) == 4
    assert all(isfinite(w) and w >= 0 for w in result.theta)
    assert sum(result.theta) == pytest.approx(1.0)
    assert 2 < result.simulations <= settings.budget
    initial_policy = OptimizablePolicy((0.25, 0.4, 0.15, 0.2))
    baseline = run_yard(scenario, initial_policy, record="summary")
    assert result.initial_score == evaluate_objective(baseline)
    replay = run_yard(scenario, OptimizablePolicy(result.theta), record="summary")
    assert result.score == evaluate_objective(replay)
    assert result.score.total <= result.initial_score.total
    points = result.evaluation_history
    assert (result.theta, result.score.total) in [(p.theta, p.evaluated_objective) for p in points]
    assert [p.simulations for p in points] == list(range(1, result.simulations + 1))
    assert [p.objective for p in points] == sorted((p.objective for p in points), reverse=True)


@pytest.mark.parametrize("budget", [1, 9, 34])
def test_both_mode_caps_and_accounts_for_each_run(monkeypatch, budget):
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    original = deepcopy(scenario)
    evaluated = []

    def count_simulation(scenario, policy, *, record):
        evaluated.append(policy.theta)
        return run_yard(scenario, policy, record=record)

    monkeypatch.setattr(optimizer, "run_yard", count_simulation)
    comparison = optimize_policy(
        scenario, method="both", omads_settings=OMADSParameters(budget=budget)
    )
    poll, de = comparison.omads, comparison.de
    assert scenario == original
    assert poll.initial_score == de.initial_score
    assert len(evaluated) == poll.simulations + de.simulations
    assert poll.simulations <= budget
    assert de.simulations == budget
    assert de.termination_reason == "evaluation_budget"
    assert de.generations == 0
    if budget == 34:
        assert de.history[0].generation == 0
        assert de.history[0].simulations < de.simulations
    else:
        assert not de.history
    assert poll.cache_hits >= 1  # Solver baseline reuses the measured baseline score.
    for result, candidates in (
        (poll, evaluated[: poll.simulations]),
        (de, evaluated[poll.simulations :]),
    ):
        assert candidates == [p.theta for p in result.evaluation_history]
        assert candidates[0] == result.initial_theta
        assert len(set(candidates)) == result.simulations
        assert result.evaluation_budget == budget
        assert result.stopped_before_budget == (result.simulations < budget)


def test_comparison_can_stop_at_mesh_and_population_tolerances():
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    result = optimize_policy(
        scenario,
        method="both",
        settings=OptimizerParameters(tol=1e6),
        omads_settings=OMADSParameters(tol=0.3),
    )
    assert result.omads.termination_reason == "mesh_tolerance"
    assert result.de.termination_reason == "population_convergence"
    assert result.omads.stopped_before_budget
    assert result.de.stopped_before_budget


def test_simulation_failure_propagates_with_candidate_context(monkeypatch):
    scenario = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    calls = 0

    def failing_simulation(scenario, policy, *, record):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulation failed")
        return run_yard(scenario, policy, record=record)

    monkeypatch.setattr(optimizer, "run_yard", failing_simulation)
    with pytest.raises(RuntimeError, match="simulation failed") as failure:
        optimize_policy(scenario)
    assert any("theta=" in note for note in failure.value.__notes__)


def test_cli_writes_comparison_and_plot_when_de_stops_early(monkeypatch, tmp_path, capsys):
    output = tmp_path / "comparison.json"
    plot = tmp_path / "convergence.png"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scripts/optimize_policy.py",
            str(DEFAULT_OBJECTIVE_PATH),
            "--method",
            "both",
            "--budget",
            "12",
            "--popsize",
            "1",
            "--maxiter",
            "0",
            "--output",
            str(output),
            "--plot",
            str(plot),
        ],
    )
    runpy.run_path("scripts/optimize_policy.py", run_name="__main__")
    payload = json.loads(output.read_text())
    assert payload["schema_version"] == 2
    assert payload["omads"]["method"] == "OMADS.poll.main"
    assert payload["de"]["stopped_before_budget"]
    assert payload["de"]["termination_reason"] == "generation_limit"
    assert payload["omads"]["initial_objective_total"] == payload["de"]["initial_objective_total"]
    assert plot.read_bytes().startswith(b"\x89PNG")
    printed = capsys.readouterr().out
    assert "OMADS (poll)" in printed
    assert "Differential Evolution" in printed


def test_batch_solvers_share_one_policy_per_candidate_and_keep_independent_counts(monkeypatch):
    example = load_yard_scenario(DEFAULT_OBJECTIVE_PATH)
    batch = {
        "short": replace(example, horizon=25),
        "longer": replace(example, horizon=35),
    }
    snapshots = deepcopy(batch)
    objective = ObjectiveParameters(shortfall_weight=2)
    features = FeatureParameters(stockout_reference=20)
    calls = []

    def observe(scenario, policy, *, record):
        assert policy.features == features
        result = run_yard(scenario, policy, record=record)
        calls.append((scenario, policy.theta, evaluate_objective(result, objective)))
        return result

    monkeypatch.setattr(optimizer, "run_yard", observe)
    kwargs = {
        "mode": "batch",
        "evaluation_budget": 10,
        "settings": OptimizerParameters(seed=17),
        "features": features,
        "objective": objective,
    }
    comparison = optimize_policy(batch, method="both", **kwargs)
    assert batch == snapshots
    assert comparison.omads.initial_score == comparison.de.initial_score
    assert len(calls) == comparison.omads.simulations + comparison.de.simulations
    by_method = (calls[: comparison.omads.simulations], calls[comparison.omads.simulations :])
    for method, result, observations in zip(
        ("omads", "de"), (comparison.omads, comparison.de), by_method, strict=True
    ):
        assert result == optimize_policy(batch, method=method, **kwargs)
        assert len(result.theta) == 4
        assert all(isfinite(value) and value >= 0 for value in result.theta)
        assert sum(result.theta) == pytest.approx(1)
        assert result.score.total <= result.initial_score.total
        assert result.scenario_count == len(batch)
        assert 1 < result.candidate_evaluations <= 10
        assert result.simulations == result.candidate_evaluations * len(batch)
        assert [entry.scenario_id for entry in result.scenario_scores] == list(batch)

        recorded_batches = list(zip(*[iter(observations)] * len(batch), strict=True))
        for i, (group, entry) in enumerate(
            zip(recorded_batches, result.evaluation_history, strict=True), start=1
        ):
            assert tuple(observation[0] for observation in group) == tuple(batch.values())
            assert {observation[1] for observation in group} == {entry.theta}
            assert entry.evaluated_objective == pytest.approx(
                sum(observation[2].total for observation in group) / len(batch)
            )
            assert entry.candidate_evaluations == i
            assert entry.simulations == i * len(batch)
        best_index = next(
            i for i, entry in enumerate(result.evaluation_history) if entry.theta == result.theta
        )
        for before, after, scores in zip(
            recorded_batches[0], recorded_batches[best_index], result.scenario_scores, strict=True
        ):
            assert before[2] == scores.initial_score
            assert after[2] == scores.score


@pytest.mark.parametrize(
    ("batch", "message"),
    [
        ({}, "nonempty mapping"),
        ({" ": object()}, "nonempty strings"),
        ({"invalid": object()}, "must be a YardScenario"),
    ],
)
def test_batch_validates_input(batch, message):
    with pytest.raises(ValueError, match=message):
        optimize_policy(batch, mode="batch")


def test_default_six_scenario_batch_cli(monkeypatch, tmp_path, capsys):
    output = tmp_path / "batch.json"
    plot = tmp_path / "batch.png"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scripts/optimize_policy.py",
            "--mode",
            "batch",
            "--method",
            "both",
            "--budget",
            "4",
            "--output",
            str(output),
            "--plot",
            str(plot),
        ],
    )
    runpy.run_path("scripts/optimize_policy.py", run_name="__main__")
    payload = json.loads(output.read_text())
    identifiers = {
        f"{traffic}_{loaders}loader"
        for traffic in ("light", "medium", "heavy")
        for loaders in (1, 2)
    }
    assert payload["schema_version"] == 3
    assert {scenario["id"] for scenario in payload["scenarios"]} == identifiers
    printed = capsys.readouterr().out
    assert printed.count("Batch mean") == 2
    for method in ("omads", "de"):
        result = payload[method]
        assert result["scenario_count"] == 6
        assert {entry["scenario_id"] for entry in result["scenario_scores"]} == identifiers
        assert result["candidate_evaluations"] == 4
        assert result["simulations"] == 24
        assert result["objective_total"] == pytest.approx(
            sum(entry["objective_total"] for entry in result["scenario_scores"]) / 6
        )
    assert plot.read_bytes().startswith(b"\x89PNG")


def test_batch_cli_rejects_duplicate_scenario_ids(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["scripts/optimize_policy.py", "--mode", "batch", "a/example.yaml", "b/example.yaml"],
    )
    with pytest.raises(SystemExit) as error:
        runpy.run_path("scripts/optimize_policy.py", run_name="__main__")
    assert error.value.code == 2
    assert "unique stems" in capsys.readouterr().err
