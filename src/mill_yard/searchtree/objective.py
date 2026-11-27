"""Terminal objective functions for search over encoded yard decisions."""

from dataclasses import dataclass
from math import isfinite

from mill_yard.records.persistence import summary
from mill_yard.simulation.engine import YardResult


@dataclass(frozen=True)
class ObjectiveWeights:
    """Weights for the three terminal operating costs.

    The default weights are all one, as a starting point. Since the metrics
    use different units (minutes and metres), they should be calibrated before
    interpreting the combined score as an operational preference.
    """

    starvation: float = 1.0
    truck_waiting: float = 1.0
    loader_travel: float = 1.0

    def __post_init__(self) -> None:
        for name, value in (
            ("starvation", self.starvation),
            ("truck_waiting", self.truck_waiting),
            ("loader_travel", self.loader_travel),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"{name} weight must be numeric")
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} weight must be finite and nonnegative")


@dataclass(frozen=True)
class MetricStatistics:
    """Observed statistics for one objective component across random paths."""

    mean: float
    maximum: float
    standard_deviation: float
    minimum: float

    def __post_init__(self) -> None:
        values = (self.mean, self.maximum, self.standard_deviation, self.minimum)
        if any(not isfinite(value) for value in values):
            raise ValueError("metric statistics must be finite")
        if self.standard_deviation < 0:
            raise ValueError("standard_deviation must be nonnegative")


@dataclass(frozen=True)
class ObjectiveStatistics:
    """Random-path reference statistics for the objective components."""

    starvation: MetricStatistics
    truck_waiting: MetricStatistics
    loader_travel: MetricStatistics


def objective_components(result: YardResult) -> dict[str, float]:
    """Return the three unweighted terminal costs for one simulation run."""
    metrics = summary(result)
    return {
        "starvation": sum(metrics["starvation"].values()),
        "truck_waiting": (
            metrics["completed_truck_wait_total"] + metrics["unfinished_truck_wait_so_far"]
        ),
        "loader_travel": metrics["loader_travel_distance_total"],
    }


def objective_value(
    result: YardResult, weights: ObjectiveWeights | None = None
) -> float:
    """Return a weighted cost to minimize for a completed simulation run.

    Truck waiting includes completed trucks' total wait and the wait accrued so
    far by trucks still in the yard at the horizon. Starvation is summed across
    infeeds, and loader travel is total distance across loaders.
    """
    weights = weights or ObjectiveWeights()
    components = objective_components(result)
    return (
        weights.starvation * components["starvation"]
        + weights.truck_waiting * components["truck_waiting"]
        + weights.loader_travel * components["loader_travel"]
    )


def normalized_objective_value(
    result: YardResult,
    statistics: ObjectiveStatistics,
    weights: ObjectiveWeights | None = None,
) -> float:
    """Return a weighted sum of z-scored costs relative to random paths.

    A component's random-path mean is subtracted and it is divided by its
    sample standard deviation. A zero standard deviation is rejected because
    that component cannot be standardized from the supplied calibration.
    """
    weights = weights or ObjectiveWeights()
    components = objective_components(result)
    references = {
        "starvation": (statistics.starvation, weights.starvation),
        "truck_waiting": (statistics.truck_waiting, weights.truck_waiting),
        "loader_travel": (statistics.loader_travel, weights.loader_travel),
    }
    total = 0.0
    for name, (reference, weight) in references.items():
        if reference.standard_deviation == 0:
            raise ValueError(f"Cannot normalize {name}: calibration standard deviation is zero")
        total += weight * (components[name] - reference.mean) / reference.standard_deviation
    return total


__all__ = [
    "MetricStatistics",
    "ObjectiveStatistics",
    "ObjectiveWeights",
    "normalized_objective_value",
    "objective_components",
    "objective_value",
]
