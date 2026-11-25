"""Dependency-free bounded coordinate search for yard layouts."""

from dataclasses import dataclass
from random import Random

from mill_yard.layouts.config import LayoutConfig
from mill_yard.layouts.objective import LayoutEvaluation, evaluate_layout


@dataclass(frozen=True)
class LayoutTrajectoryPoint:
    """An incumbent layout snapshot, suitable for plotting or animation."""

    evaluation_number: int
    vector: tuple[float, ...]
    objective: float
    violations: tuple[float, ...]


@dataclass(frozen=True)
class LayoutSearchResult:
    """Best layout and incumbent history from a search run."""

    vector: tuple[float, ...]
    evaluation: LayoutEvaluation
    evaluations: int
    trajectory: tuple[LayoutTrajectoryPoint, ...]


def optimize_layout(
    config: LayoutConfig,
    *,
    budget: int = 100,
    seed: int = 0,
    initial: tuple[float, ...] | None = None,
) -> LayoutSearchResult:
    """Search feasible coordinates by random sampling then shrinking local steps."""
    if budget < 1:
        raise ValueError("budget must be at least one")
    rng = Random(seed)
    if initial is None:
        initial = tuple(
            value
            for _ in config.point_ids
            for value in (
                rng.uniform(*config.x_bounds),
                rng.uniform(*config.y_bounds),
            )
        )
    if len(initial) != 2 * len(config.point_ids):
        raise ValueError("Initial vector has the wrong number of coordinates")
    vector = tuple(map(float, initial))
    best = evaluate_layout(config, vector)
    evaluations = 1
    trajectory = [_trajectory_point(evaluations, vector, best)]
    steps = [((config.x_bounds[1] - config.x_bounds[0]) / 4
              if i % 2 == 0 else (config.y_bounds[1] - config.y_bounds[0]) / 4)
             for i in range(len(vector))]
    while evaluations < budget and max(steps, default=0.0) > 1e-3:
        improved = False
        for i in range(len(vector)):
            for direction in (-1.0, 1.0):
                if evaluations >= budget:
                    break
                candidate = list(vector)
                candidate[i] += direction * steps[i]
                candidate_eval = evaluate_layout(config, candidate)
                evaluations += 1
                if _rank(candidate_eval) < _rank(best):
                    vector, best, improved = tuple(candidate), candidate_eval, True
                    trajectory.append(_trajectory_point(evaluations, vector, best))
            if evaluations >= budget:
                break
        if not improved:
            steps = [step / 2 for step in steps]
    return LayoutSearchResult(vector, best, evaluations, tuple(trajectory))


def _rank(evaluation: LayoutEvaluation):
    return (sum(evaluation.violations), evaluation.objective)


def _trajectory_point(evaluation_number: int, vector: tuple[float, ...], evaluation: LayoutEvaluation):
    return LayoutTrajectoryPoint(
        evaluation_number=evaluation_number,
        vector=vector,
        objective=evaluation.objective,
        violations=evaluation.violations,
    )
