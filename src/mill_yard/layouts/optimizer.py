"""Dependency-free exhaustive coordinate search for yard layouts."""

from dataclasses import dataclass
from math import isfinite

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
    budget: int | None = 100,
    precision: float | None = 1e-3,
    seed: int = 0,
    initial: tuple[float, ...] | None = None,
    verbose: bool = True,
    algorithm: str | None = None,
) -> LayoutSearchResult:
    """Search coordinates using the configured exhaustive or opportunistic poll.

    An ``initial`` argument overrides the optional vector in LayoutConfig. If
    neither is provided, selected POIs start at their yard-config locations.
    Exhaustive polling evaluates both directions of every coordinate from the
    same incumbent, then accepts the best improvement. Opportunistic polling
    accepts each improvement immediately and continues from it. Search stops
    when the largest coordinate step is at most ``precision`` or the evaluation
    budget is exhausted. Either limit may be ``None`` to disable it.
    ``seed`` is retained for API compatibility.
    """
    if budget is not None and budget < 1:
        raise ValueError("budget must be at least one")
    if precision is not None and (not isfinite(precision) or precision <= 0):
        raise ValueError("precision must be finite and positive")
    algorithm = config.algorithm if algorithm is None else algorithm
    if algorithm not in ("exhaustive", "opportunistic"):
        raise ValueError("algorithm must be exhaustive or opportunistic")
    if initial is None:
        initial = config.initial
        if initial is None:
            pile_locations = {pile.id: pile.location for pile in config.scenario.piles}
            initial = tuple(
                value
                for point_id in config.point_ids
                for value in (pile_locations[point_id].x, pile_locations[point_id].y)
            )
    if len(initial) != 2 * len(config.point_ids):
        raise ValueError("Initial vector has the wrong number of coordinates")
    vector = tuple(map(float, initial))
    best = evaluate_layout(config, vector)
    evaluations = 1
    trajectory = [_trajectory_point(evaluations, vector, best)]
    if verbose:
        print(f"Initial incumbent after {evaluations} evaluation: objective={best.objective:g}")
    steps = [((config.x_bounds[1] - config.x_bounds[0]) / 4
              if i % 2 == 0 else (config.y_bounds[1] - config.y_bounds[0]) / 4)
             for i in range(len(vector))]
    poll_size = 2 * len(vector)
    while (budget is None or evaluations < budget) and (
        precision is None or max(steps, default=0.0) > precision
    ):
        if algorithm == "exhaustive":
            if budget is not None and evaluations + poll_size > budget:
                break
            poll_best = None
            poll_best_rank = _rank(best)
            for i in range(len(vector)):
                for direction in (-1.0, 1.0):
                    candidate = list(vector)
                    candidate[i] += direction * steps[i]
                    candidate_eval = evaluate_layout(config, candidate)
                    evaluations += 1
                    candidate_rank = _rank(candidate_eval)
                    if candidate_rank < poll_best_rank:
                        poll_best = (tuple(candidate), candidate_eval, evaluations)
                        poll_best_rank = candidate_rank
            if poll_best is not None:
                vector, best, incumbent_evaluation = poll_best
                trajectory.append(_trajectory_point(incumbent_evaluation, vector, best))
            elif budget is None or evaluations + poll_size <= budget:
                steps = _refine_grid(steps, evaluations, trajectory, best.objective, verbose)
        else:
            improved = False
            for i in range(len(vector)):
                for direction in (-1.0, 1.0):
                    if budget is not None and evaluations >= budget:
                        break
                    candidate = list(vector)
                    candidate[i] += direction * steps[i]
                    candidate_eval = evaluate_layout(config, candidate)
                    evaluations += 1
                    if _rank(candidate_eval) < _rank(best):
                        vector, best, improved = tuple(candidate), candidate_eval, True
                        trajectory.append(_trajectory_point(evaluations, vector, best))
                if budget is not None and evaluations >= budget:
                    break
            if not improved and (budget is None or evaluations < budget):
                steps = _refine_grid(steps, evaluations, trajectory, best.objective, verbose)
    return LayoutSearchResult(vector, best, evaluations, tuple(trajectory))


def _rank(evaluation: LayoutEvaluation):
    return (sum(evaluation.violations), evaluation.objective)


def _refine_grid(steps, evaluations, trajectory, objective, verbose):
    refined = [step / 2 for step in steps]
    if verbose:
        print(
            f"Grid refined after {evaluations} evaluations: "
            f"dx={refined[0]:g}, dy={refined[1]:g}; "
            f"incumbents={len(trajectory)}; objective={objective:g}"
        )
    return refined


def _trajectory_point(evaluation_number: int, vector: tuple[float, ...], evaluation: LayoutEvaluation):
    return LayoutTrajectoryPoint(
        evaluation_number=evaluation_number,
        vector=vector,
        objective=evaluation.objective,
        violations=evaluation.violations,
    )
