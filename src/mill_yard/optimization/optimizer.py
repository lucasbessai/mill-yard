"""Optimize four dispatch weights on fixed deterministic yard scenarios."""

import logging
import sys
import warnings
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, field, fields, replace
from io import StringIO
from math import isclose, isfinite
from pathlib import Path
from statistics import fmean
from tempfile import TemporaryDirectory
from threading import Lock
from time import perf_counter
from typing import Literal

import numpy as np
from scipy.optimize import differential_evolution

from mill_yard.config.loader import default_traffic_paths, load_yard_scenario
from mill_yard.model.yard import YardScenario
from mill_yard.optimization.objective import ObjectiveParameters, ObjectiveScore, evaluate_objective
from mill_yard.policies.optimizable import FeatureParameters, OptimizablePolicy
from mill_yard.simulation.engine import run_yard


@dataclass(frozen=True)
class OptimizerParameters:
    """Reproducible, serial DE search budget; population size is max(5, 3*popsize)."""

    seed: int = 42
    maxiter: int = 50
    popsize: int = 10
    tol: float = 0.01
    atol: float = 0.0

    def __post_init__(self) -> None:
        for name, minimum in (("seed", 0), ("maxiter", 0), ("popsize", 1)):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        for name in ("tol", "atol"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class OMADSParameters:
    """Settings for serial OMADS poll or Latin hypercube search plus poll."""

    budget: int = 200
    psize_init: float = 0.2
    tol: float = 1e-4
    direction_type: str = "ORTHO_2N"
    rich_direction: bool = True
    opportunistic: bool = True
    store_cache: bool = True
    check_cache: bool = True
    full_mads: bool = False
    search_samples: int = 4

    def __post_init__(self) -> None:
        for name in ("budget", "search_samples"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("psize_init", "tol"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not self.store_cache:
            raise ValueError("OMADS 2610 requires store_cache for frame centres")


@dataclass(frozen=True)
class SearchProgress:
    """Best-so-far objective after a DE generation or a candidate evaluation."""

    generation: int | None
    objective: float
    simulations: int = 0
    theta: tuple[float, ...] | None = None
    evaluated_objective: float | None = None
    candidate_evaluations: int = 0


@dataclass(frozen=True)
class BatchObjectiveScore(ObjectiveScore):
    """Mean objective and mean component metrics across the scenario batch."""

    mean_total: float

    @property
    def total(self) -> float:
        return self.mean_total


@dataclass(frozen=True)
class ScenarioOptimizationScores:
    scenario_id: str
    initial_score: ObjectiveScore
    score: ObjectiveScore


@dataclass(frozen=True)
class _CandidateScore:
    summary: ObjectiveScore
    scenario_scores: tuple[ObjectiveScore, ...]


@dataclass(frozen=True)
class OptimizationResult:
    """Best simulated dispatch policy and diagnostics for one run."""

    theta: tuple[float, ...]
    score: ObjectiveScore
    initial_theta: tuple[float, ...]
    initial_score: ObjectiveScore
    settings: OptimizerParameters | OMADSParameters
    features: FeatureParameters
    objective_parameters: ObjectiveParameters
    generations: int | None
    function_evaluations: int | None
    simulations: int
    converged: bool
    message: str
    history: tuple[SearchProgress, ...]
    algorithm: str
    seed: int
    iterations: int | None
    objective_calls: int
    cache_hits: int
    evaluation_budget: int | None
    stopped_before_budget: bool | None
    termination_reason: str
    evaluation_history: tuple[SearchProgress, ...]
    runtime_seconds: float = field(compare=False)
    mode: Literal["single", "batch"] = "single"
    candidate_evaluations: int = 0
    scenario_scores: tuple[ScenarioOptimizationScores, ...] = ()

    @property
    def scenario_count(self) -> int:
        return len(self.scenario_scores) if self.mode == "batch" else 1

    @property
    def improvement_percent(self) -> float:
        initial = self.initial_score.total
        return 100 * (initial - self.score.total) / initial if initial else 0.0


@dataclass(frozen=True)
class OptimizationComparison:
    omads: OptimizationResult
    de: OptimizationResult


class _BudgetExhausted(Exception):
    """Raised before a cache miss would exceed the candidate evaluation limit."""


class _Evaluation:
    def __init__(
        self,
        scenarios: tuple[tuple[str, YardScenario], ...],
        mode: Literal["single", "batch"],
        features: FeatureParameters,
        objective: ObjectiveParameters,
        budget: int | None,
    ) -> None:
        self.scenarios = scenarios
        self.mode = mode
        self.features = features
        self.objective = objective
        self.budget = budget
        self.cache: dict[tuple[float, ...], _CandidateScore] = {}
        self.history: list[SearchProgress] = []
        self.best_theta: tuple[float, ...] = ()
        self.best_score: _CandidateScore | None = None
        self.simulations = 0
        self.calls = 0
        self.cache_hits = 0

    def evaluate(self, policy: OptimizablePolicy) -> _CandidateScore:
        theta = policy.theta
        if (
            len(theta) != 4
            or any(not isfinite(w) or w < 0 for w in theta)
            or not isclose(sum(theta), 1.0, rel_tol=0.0, abs_tol=1e-12)
        ):
            raise ValueError(f"Invalid dispatch weights: {theta}")
        if theta in self.cache:
            self.cache_hits += 1
            return self.cache[theta]
        if self.budget is not None and len(self.cache) >= self.budget:
            raise _BudgetExhausted
        scores = []
        for scenario_id, scenario in self.scenarios:
            try:
                result = run_yard(scenario, policy, record="summary")
                self.simulations += 1
                score = evaluate_objective(result, self.objective)
            except Exception as error:
                error.add_note(
                    f"Dispatch evaluation failed for scenario {scenario_id!r}, theta={theta}"
                )
                raise
            if not isfinite(score.total):
                raise ValueError(
                    f"Nonfinite objective for scenario {scenario_id!r}, theta={theta}: {score.total}"
                )
            scores.append(score)

        summary = scores[0]
        if self.mode == "batch":
            summary = BatchObjectiveScore(
                **{
                    metric.name: fmean(getattr(score, metric.name) for score in scores)
                    for metric in fields(ObjectiveScore)
                },
                mean_total=fmean(score.total for score in scores),
            )
        candidate = _CandidateScore(summary, tuple(scores))
        self.cache[theta] = candidate
        if self.best_score is None or summary.total < self.best_score.summary.total:
            self.best_theta, self.best_score = theta, candidate
        self.history.append(
            SearchProgress(
                None,
                self.best_score.summary.total,
                self.simulations,
                theta,
                summary.total,
                candidate_evaluations=len(self.cache),
            )
        )
        return candidate

    @property
    def budget_message(self) -> str:
        if self.mode == "batch":
            return "Candidate-policy evaluation budget exhausted."
        return "Simulation evaluation budget exhausted."

    def loss(
        self, coordinates: Sequence[float], baseline: OptimizablePolicy | None = None
    ) -> float:
        self.calls += 1
        if len(coordinates) != 3 or any(not isfinite(v) or not 0 <= v <= 1 for v in coordinates):
            raise ValueError(f"Invalid solver coordinates: {coordinates}")
        if baseline is not None and tuple(coordinates) == _to_coordinates(baseline.theta):
            return self.evaluate(baseline).summary.total
        return self.evaluate(OptimizablePolicy(_to_theta(coordinates), self.features)).summary.total


def _to_theta(coordinates: Sequence[float]) -> tuple[float, float, float, float]:
    """Break a unit stick into four weights using three coordinates in [0, 1]."""
    a, b, c = coordinates
    return (
        float(a),
        float((1 - a) * b),
        float((1 - a) * (1 - b) * c),
        float((1 - a) * (1 - b) * (1 - c)),
    )


def _to_coordinates(theta: Sequence[float]) -> tuple[float, float, float]:
    """Invert stick breaking, including simplex faces and vertices."""
    t, m, i, c = theta
    remainder = m + i + c
    return (t, m / remainder if remainder > 0 else 0.0, i / (i + c) if i + c > 0 else 0.0)


def _run_de(evaluation: _Evaluation, initial: tuple[float, ...], settings: OptimizerParameters):
    rng = np.random.default_rng(settings.seed)
    size = max(5, 3 * settings.popsize)
    weights = np.vstack((np.asarray(initial), np.eye(4), rng.dirichlet(np.ones(4), size - 5)))
    population = np.asarray([_to_coordinates(row) for row in weights])
    history: list[SearchProgress] = []

    def record(generation):
        history.append(
            SearchProgress(
                generation,
                evaluation.best_score.summary.total,
                evaluation.simulations,
                candidate_evaluations=len(evaluation.cache),
            )
        )

    def loss(coordinates):
        value = evaluation.loss(coordinates)
        if evaluation.calls == size:
            record(0)
        return value

    def record_generation(intermediate_result):
        record(int(intermediate_result.nit))

    try:
        solution = differential_evolution(
            loss,
            bounds=[(0.0, 1.0)] * 3,
            init=population,
            rng=rng,
            maxiter=settings.maxiter,
            popsize=settings.popsize,
            tol=settings.tol,
            atol=settings.atol,
            polish=False,
            callback=record_generation,
            workers=1,
        )
    except _BudgetExhausted:
        return (
            max(0, len(history) - 1),
            None,
            "evaluation_budget",
            evaluation.budget_message,
            tuple(history),
        )
    reason = "population_convergence" if solution.success else "generation_limit"
    return int(solution.nit), int(solution.nfev), reason, str(solution.message), tuple(history)


# OMADS 2610 changes process-wide logging, warning filters and NumPy RNG state.
_omads_lock = Lock()


def _run_omads(
    evaluation: _Evaluation, initial: OptimizablePolicy, settings: OMADSParameters, seed: int
):
    def blackbox(coordinates):
        return [evaluation.loss(coordinates, initial), [0]]

    with _omads_lock, TemporaryDirectory(prefix="mill-yard-omads-") as directory:
        data = {
            "evaluator": {"blackbox": blackbox},
            "param": {
                "baseline": list(_to_coordinates(initial.theta)),
                "lb": [0.0] * 3,
                "ub": [1.0] * 3,
                # OMADS 2610 mesh projection tests for the full enum name.
                "var_type": ["REAL"] * 3,
                "mesh_type": "ORTHO",
                "scaling": 1.0,
                "post_dir": directory,
            },
            "options": {
                "seed": seed,
                "budget": settings.budget,
                "psize_init": settings.psize_init,
                "tol": settings.tol,
                "direction_type": settings.direction_type,
                "rich_direction": settings.rich_direction,
                "opportunistic": settings.opportunistic,
                "store_cache": settings.store_cache,
                "check_cache": settings.check_cache,
                "parallel_mode": False,
                "display": False,
            },
        }
        root = logging.getLogger()
        handlers, level = root.handlers[:], root.level
        rng_state, stderr = np.random.get_state(), sys.stderr  # noqa: NPY002
        try:
            with warnings.catch_warnings(), redirect_stdout(StringIO()):
                from OMADS import mads, poll

                root.handlers = [logging.NullHandler()]
                if settings.full_mads:
                    data["search"] = {"s_method": "LH", "ns": settings.search_samples}
                    output, *_ = mads.main(data)
                else:
                    output, *_ = poll.main(data)
        except _BudgetExhausted:
            return None, None, "evaluation_budget", evaluation.budget_message
        finally:
            for handler in root.handlers:
                if handler not in handlers:
                    handler.close()
            root.handlers = handlers
            root.setLevel(level)
            np.random.set_state(rng_state)  # noqa: NPY002
            sys.stderr = stderr

        # The 2610 return dictionary has counts but omits the stopping reason.
        log = (Path(directory) / "OMADS.log").read_text(encoding="utf-8")
        messages = [
            line.strip().removeprefix("Termination criterion hit: ")
            for line in log.splitlines()
            if line.strip().startswith("Termination criterion hit: ")
        ]
        reported = int(output["nbb_evals"])
        if (
            len(evaluation.cache) >= settings.budget
            or reported >= settings.budget
            or "MAX_BB_EVAL_REACHED." in messages
        ):
            reason = "evaluation_budget"
        elif "MIN_MESH_REACHED." in messages or any(
            "below the minimum threshold" in message for message in messages
        ):
            reason = "mesh_tolerance"
        else:
            reason = "solver_stop"
        return (
            int(output["niterations"]),
            reported,
            reason,
            " ".join(messages) or "OMADS stopped.",
        )


def optimize_policy(
    scenario: YardScenario | Mapping[str, YardScenario] | None = None,
    *,
    mode: Literal["single", "batch"] = "single",
    initial_theta: Sequence[float] = (0.25, 0.40, 0.15, 0.20),
    settings: OptimizerParameters | None = None,
    features: FeatureParameters | None = None,
    objective: ObjectiveParameters | None = None,
    method: Literal["omads", "de", "both"] = "omads",
    omads_settings: OMADSParameters | None = None,
    evaluation_budget: int | None = None,
) -> OptimizationResult | OptimizationComparison:
    """Minimize one scenario's objective or a fixed batch's equal-weight mean.

    Pass a named mapping in batch mode, or omit it to load the checkout's traffic
    scenarios. Budgets count unique policies evaluated, including the baseline.
    """
    if mode == "single":
        if not isinstance(scenario, YardScenario):
            raise ValueError("Single mode requires one YardScenario")
        scenarios = (("scenario", scenario),)
    elif mode == "batch":
        if scenario is None:
            scenario = {path.stem: load_yard_scenario(path) for path in default_traffic_paths()}
        if not isinstance(scenario, Mapping) or not scenario:
            raise ValueError(
                "Batch mode requires a nonempty mapping of IDs to YardScenario objects"
            )
        scenarios = tuple(scenario.items())
        identifiers = [name for name, _ in scenarios]
        if any(not isinstance(name, str) or not name.strip() for name in identifiers):
            raise ValueError("Scenario IDs must be nonempty strings")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Scenario IDs must be unique")
        if any(not isinstance(item, YardScenario) for _, item in scenarios):
            raise ValueError("Each batch scenario must be a YardScenario")
    else:
        raise ValueError(f"Unknown optimization mode: {mode}")
    if method not in ("omads", "de", "both"):
        raise ValueError(f"Unknown optimization method: {method}")
    if evaluation_budget is not None and (
        type(evaluation_budget) is not int or evaluation_budget < 1
    ):
        raise ValueError("evaluation_budget must be a positive integer")
    settings = settings or OptimizerParameters()
    if method != "de" and settings.seed >= 2**32:
        raise ValueError("OMADS seed must be below 2**32")
    omads_settings = omads_settings or OMADSParameters()
    features = features or FeatureParameters()
    objective = objective or ObjectiveParameters()
    initial = OptimizablePolicy(initial_theta, features)
    budget = evaluation_budget
    if budget is None and (mode == "batch" or method != "de"):
        budget = omads_settings.budget

    def run(use_omads: bool) -> OptimizationResult:
        start = perf_counter()
        evaluation = _Evaluation(scenarios, mode, features, objective, budget)
        initial_score = evaluation.evaluate(initial)
        if use_omads:
            algorithm = "OMADS (search + poll)" if omads_settings.full_mads else "OMADS (poll)"
            assert budget is not None
            config = replace(omads_settings, budget=budget)
            iterations, reported, reason, message = _run_omads(
                evaluation, initial, config, settings.seed
            )
            history = tuple(evaluation.history)
        else:
            algorithm = "Differential Evolution"
            config = settings
            iterations, reported, reason, message, history = _run_de(
                evaluation, initial.theta, settings
            )
        assert evaluation.best_score is not None
        return OptimizationResult(
            theta=evaluation.best_theta,
            score=evaluation.best_score.summary,
            initial_theta=initial.theta,
            initial_score=initial_score.summary,
            settings=config,
            features=features,
            objective_parameters=objective,
            generations=None if use_omads else iterations,
            function_evaluations=reported,
            simulations=evaluation.simulations,
            converged=reason in ("mesh_tolerance", "population_convergence"),
            message=message,
            history=history,
            algorithm=algorithm,
            seed=settings.seed,
            iterations=iterations,
            objective_calls=evaluation.calls,
            cache_hits=evaluation.cache_hits,
            evaluation_budget=budget,
            stopped_before_budget=None if budget is None else len(evaluation.cache) < budget,
            termination_reason=reason,
            evaluation_history=tuple(evaluation.history),
            runtime_seconds=perf_counter() - start,
            mode=mode,
            candidate_evaluations=len(evaluation.cache),
            scenario_scores=tuple(
                ScenarioOptimizationScores(identifier, before, after)
                for (identifier, _), before, after in zip(
                    scenarios,
                    initial_score.scenario_scores,
                    evaluation.best_score.scenario_scores,
                    strict=True,
                )
            )
            if mode == "batch"
            else (),
        )

    if method == "both":
        return OptimizationComparison(run(True), run(False))
    return run(method == "omads")
