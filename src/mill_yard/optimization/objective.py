"""Evaluate a completed simulation; lower objective scores are better.

These example scales and preferences are illustrative, not calibrated costs.
Keep them fixed when comparing policies on the same scenario and horizon.
"""

from dataclasses import dataclass
from math import isfinite

from mill_yard.records.persistence import summary
from mill_yard.simulation.engine import YardResult


@dataclass(frozen=True)
class ObjectiveParameters:
    """Reference totals in truck-minutes, metres, and tonnes; relative preferences.

    The default references represent 60 total truck waiting minutes, 10 km of
    fleet travel, and 100 tonnes of unmet demand. Equal default preferences give
    equal importance to one reference unit of each outcome. Scores can exceed 1.
    """

    reference_wait: float = 60.0
    reference_distance: float = 5_000.0
    reference_shortfall: float = 10.0
    wait_weight: float = 1.0
    distance_weight: float = 1.0
    shortfall_weight: float = 1.0

    def __post_init__(self) -> None:
        for name in ("reference_wait", "reference_distance", "reference_shortfall"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        weights = (self.wait_weight, self.distance_weight, self.shortfall_weight)
        if any(not isfinite(w) or w < 0 for w in weights):
            raise ValueError("Objective weights must be finite and nonnegative")
        if not isfinite(sum(weights)) or sum(weights) <= 0:
            raise ValueError("Objective weights must have a finite positive sum")


@dataclass(frozen=True)
class ObjectiveScore:
    """Raw totals and weighted contributions, retained to explain the score."""

    truck_wait_minutes: float
    travel_metres: float
    shortfall_tonnes: float
    wait_contribution: float
    distance_contribution: float
    shortfall_contribution: float

    @property
    def total(self) -> float:
        return self.wait_contribution + self.distance_contribution + self.shortfall_contribution


def evaluate_objective(
    result: YardResult, parameters: ObjectiveParameters | None = None
) -> ObjectiveScore:
    """Score outcomes at the run horizon, including unfinished trucks' wait so far.

    Shortfall uses each infeed's constant demand rate times its starvation time.
    Travel includes partial journeys at the horizon. No overtime or eventual
    waiting after the horizon is estimated. Travel is a yard-cost proxy, not dollars.
    """
    p = parameters or ObjectiveParameters()
    metrics = summary(result)
    wait = metrics["completed_truck_wait_total"] + metrics["unfinished_truck_wait_so_far"]
    distance = metrics["loader_travel_distance_total"]
    shortfall = sum(
        spec.consumption_rate * metrics["starvation"][spec.id]
        for spec in result.scenario.infeeds
    )
    weight_total = p.wait_weight + p.distance_weight + p.shortfall_weight
    return ObjectiveScore(
        truck_wait_minutes=wait,
        travel_metres=distance,
        shortfall_tonnes=shortfall,
        wait_contribution=(p.wait_weight / weight_total) * wait / p.reference_wait,
        distance_contribution=(p.distance_weight / weight_total) * distance / p.reference_distance,
        shortfall_contribution=(p.shortfall_weight / weight_total) * shortfall / p.reference_shortfall,
    )
