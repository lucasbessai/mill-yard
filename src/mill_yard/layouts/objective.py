"""Translate layout vectors into scenarios and score simulation outcomes."""

from dataclasses import dataclass, replace
from itertools import combinations

from mill_yard.layouts.config import LayoutConfig
from mill_yard.model.types import Point
from mill_yard.model.yard import YardScenario
from mill_yard.policies.joint import ThresholdPolicy
from mill_yard.records.persistence import summary
from mill_yard.simulation.engine import run_yard


@dataclass(frozen=True)
class LayoutEvaluation:
    """Scalar cost and nonnegative geometric constraint violations."""

    objective: float
    violations: tuple[float, ...]
    metrics: dict


def vector_to_scenario(config: LayoutConfig, vector) -> YardScenario:
    """Replace configured pile x/y locations from ``[x1, y1, x2, y2, ...]``."""
    if len(vector) != 2 * len(config.point_ids):
        raise ValueError(f"Expected {2 * len(config.point_ids)} coordinates, got {len(vector)}")
    locations = {
        point_id: Point(float(vector[2 * i]), float(vector[2 * i + 1]))
        for i, point_id in enumerate(config.point_ids)
    }
    piles = tuple(
        replace(pile, location=locations.get(pile.id, pile.location))
        for pile in config.scenario.piles
    )
    return replace(config.scenario, piles=piles)


def constraint_violations(config: LayoutConfig, vector) -> tuple[float, ...]:
    """Return bound and all-pairs POI spacing violations in metres.

    Each selected POI contributes four bound violations (x lower/upper, then
    y lower/upper). Every unordered pair of selected POIs contributes one
    spacing violation, including a zero when the minimum distance is met.
    A zero in every position means the layout satisfies all configured limits.
    """
    if len(vector) != 2 * len(config.point_ids):
        raise ValueError(f"Expected {2 * len(config.point_ids)} coordinates, got {len(vector)}")
    points = [Point(float(vector[2 * i]), float(vector[2 * i + 1])) for i in range(len(config.point_ids))]
    violations = []
    for point in points:
        violations.extend(
            (max(0.0, config.x_bounds[0] - point.x), max(0.0, point.x - config.x_bounds[1]),
             max(0.0, config.y_bounds[0] - point.y), max(0.0, point.y - config.y_bounds[1]))
        )
    for first, second in combinations(points, 2):
        distance = first.distance_to(second)
        violations.append(max(0.0, config.min_spacing - distance))
    return tuple(violations)


def evaluate_layout(config: LayoutConfig, vector) -> LayoutEvaluation:
    """Run the threshold dispatch simulation and return weighted cost and violations."""
    scenario = vector_to_scenario(config, vector)
    result = run_yard(scenario, ThresholdPolicy(), record="summary")
    metrics = summary(result)
    starvation = sum(metrics["starvation"].values())
    waiting = metrics["completed_truck_wait_total"] + metrics["unfinished_truck_wait_so_far"]
    travel = metrics["loader_travel_distance_total"]
    w_starvation, w_waiting, w_travel = config.objective_weights
    objective = w_starvation * starvation + w_waiting * waiting + w_travel * travel
    return LayoutEvaluation(objective, constraint_violations(config, vector), metrics)
