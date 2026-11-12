"""Four-weight dispatch policy adapted from proposal sections 4.1-4.5.

Only theta is optimized. Feature scales stay fixed across optimization trials.
The current model has one material, constant demand, and no direct truck-to-infeed
route. Quantities follow the existing maximal-feasible-transfer convention.
"""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from math import isfinite

from mill_yard.model.feasibility import EPS, candidates, reserve
from mill_yard.model.yard import Assignment, YardScenario, YardState
from mill_yard.policies.scored import ScoredPolicy


@dataclass(frozen=True)
class FeatureParameters:
    """Fixed, illustrative feature scales in minutes, metres, and tonnes.

    Cost defaults to a distance proxy. Optional active-time and holding coefficients
    must use the same cost units as cost_reference. Pile demand is allocated in
    proportion to capacity because the model has no species-to-pile mapping.
    """

    wait_reference: float = 30.0
    queue_reference: float = 5.0
    stockout_reference: float = 30.0
    pile_lead_time: float = 30.0
    pile_safety_stock: float = 0.0
    cost_reference: float = 1000.0
    distance_cost: float = 1.0
    active_time_cost: float = 0.0
    holding_cost: float = 0.0
    residence_time: float = 30.0

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{f.name} must be finite and nonnegative")
        for name in ("wait_reference", "queue_reference", "stockout_reference", "cost_reference"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


class OptimizablePolicy:
    """Score tasks with theta=(truck, mill, inventory, cost), normalized to sum 1.

    Theta is required: no hand-tuned dispatch weights are silently supplied.
    Select the highest positive score, reserve it, and recompute until no loader
    can be assigned. Ties prefer shorter travel, then scenario loader order.
    Delivery estimates exclude handling queues; only depositing active jobs and
    newly proposed jobs have known/estimated completion times.
    """

    def __init__(self, theta: Sequence[float], features: FeatureParameters | None = None) -> None:
        values = tuple(float(value) for value in theta)
        if len(values) != 4 or any(not isfinite(v) or v < 0 for v in values):
            raise ValueError("theta must contain four finite nonnegative weights")
        total = sum(values)
        if not isfinite(total) or total <= 0:
            raise ValueError("theta must have a finite positive sum")
        self.theta = tuple(v / total for v in values)
        self.features = features or FeatureParameters()
        # Reuse the existing route-duration estimate without its scoring weights.
        self._estimator = ScoredPolicy()

    def metadata(self) -> dict:
        """Retain normalized weights, their order, and fixed feature definitions."""
        return {
            "theta": list(self.theta),
            "theta_order": ["truck", "mill", "inventory", "cost"],
            "features": asdict(self.features),
        }

    def _stockout(self, infeed_id: str, state: YardState, scenario: YardScenario) -> float:
        spec = next(i for i in scenario.infeeds if i.id == infeed_id)
        if spec.consumption_rate == 0:
            return float("inf")
        available = state.infeeds[infeed_id].amount
        deliveries = []
        for loader in state.loaders.values():
            a = loader.assignment
            if a is None or a.destination_kind != "infeed" or a.destination_id != infeed_id:
                continue
            completion = loader.phase_end if loader.phase == "deposit" else None
            if loader.phase == "idle":
                completion = state.time + self._estimator._duration(a, state, scenario)[1]
            if completion is not None:
                deliveries.append((max(0.0, completion - state.time), a.quantity))
        for delay, quantity in sorted(deliveries):
            # Deliveries after the first depletion cannot avert that stockout.
            if available / spec.consumption_rate <= delay:
                break
            available += quantity
        return available / spec.consumption_rate

    def task_features(
        self, a: Assignment, state: YardState, scenario: YardScenario
    ) -> tuple[float, float, float, float]:
        """Return interpretable (truck, mill, inventory, cost) features.

        Truck urgency uses elapsed arrival time, as in the proposal (rather than
        the objective's wait excluding handling). Mill lateness can exceed one.
        Inventory uses unreserved stock so sequential choices share the benefit.
        """
        p = self.features
        travel, duration = self._estimator._duration(a, state, scenario)
        loader = next(x for x in scenario.loaders if x.id == a.loader_id)
        truck = mill = inventory = 0.0
        if a.source_kind == "truck":
            elapsed = max(0.0, state.time - state.trucks[a.source_id].arrival)
            truck = 0.5 * min(1.0, elapsed / p.wait_reference) + 0.5 * min(
                1.0, len(state.queue) / p.queue_reference
            )
        if a.destination_kind == "infeed":
            stockout = self._stockout(a.destination_id, state, scenario)
            mill = max(0.0, 1 - stockout / p.stockout_reference) + max(
                0.0, (duration - stockout) / p.stockout_reference
            )
        pile_id = a.destination_id if a.destination_kind == "pile" else a.source_id
        spec = next(x for x in scenario.piles if x.id == pile_id)
        inv = state.piles[pile_id]
        capacity = sum(x.capacity for x in scenario.piles)
        demand = sum(x.consumption_rate for x in scenario.infeeds)
        target = max(
            0.0,
            min(
                spec.capacity - inv.incoming,
                (demand * p.pile_lead_time + p.pile_safety_stock) * spec.capacity / capacity,
            ),
        )
        amount = inv.amount - inv.outgoing
        change = a.quantity if a.destination_kind == "pile" else -a.quantity
        inventory = min(
            1.0, max(0.0, (abs(amount - target) - abs(amount + change - target)) / spec.capacity)
        )
        cost = p.distance_cost * travel * loader.speed + p.active_time_cost * duration
        if a.destination_kind == "pile":
            cost += p.holding_cost * a.quantity * p.residence_time
        return truck, mill, inventory, min(1.0, cost / p.cost_reference)

    def choose(self, state: YardState, scenario: YardScenario) -> list[Assignment]:
        """Return a feasible sequential proposal without mutating the observation."""
        trial = deepcopy(state)
        chosen = []
        loader_order = {loader.id: index for index, loader in enumerate(scenario.loaders)}
        while options := candidates(trial, scenario):
            ranked = []
            for a in options:
                truck, mill, inventory, cost = self.task_features(a, trial, scenario)
                t, m, i, c = self.theta
                score = t * truck + m * mill + i * inventory - c * cost
                travel = self._estimator._duration(a, trial, scenario)[0]
                speed = next(x.speed for x in scenario.loaders if x.id == a.loader_id)
                ranked.append((score, -travel * speed, -loader_order[a.loader_id], a))
            score, _, _, best = max(ranked, key=lambda row: row[:3])
            if score <= EPS:
                break
            reserve(trial, scenario, best)
            chosen.append(best)
        return chosen

    def wake_levels(self, state: YardState, scenario: YardScenario) -> dict[str, float]:
        """Use existing low crossings; interval mode also reviews changing urgency."""
        return {spec.id: spec.low for spec in scenario.infeeds}
