"""Configurable scored dispatch for small fleets of loaders."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from math import isfinite

from mill_yard.model.feasibility import EPS, candidates, reserve
from mill_yard.model.yard import Assignment, YardScenario, YardState


@dataclass(frozen=True)
class ScoredParameters:
    """Dimensionless weights and reference times for the scored policy."""

    truck_weight: float = 2.0
    queue_weight: float = 0.2
    refill_weight: float = 0.8
    starvation_weight: float = 100.0
    travel_weight: float = 0.2
    wait_reference: float = 30.0
    travel_reference: float = 5.0
    min_score: float = 0.05

    def __post_init__(self) -> None:
        """Reject invalid weights and reference scales."""
        for f in fields(self):
            value = getattr(self, f.name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{f.name} must be finite and nonnegative")
        if self.wait_reference <= 0 or self.travel_reference <= 0:
            raise ValueError("Reference times must be positive")


class ScoredPolicy:
    """Choose the highest-scoring joint assignment, including idle choices.

    Enumerates assignments for idle loaders in configured order. This is intended
    for small fleets; each candidate moves the maximum feasible amount, capped
    at the refill target. Handling-slot queues are not included in travel or
    delivery estimates, so shortage risk is an approximation.
    """

    def __init__(self, parameters: ScoredParameters | None = None) -> None:
        """Set scoring weights for this policy instance."""
        self.parameters = parameters or ScoredParameters()

    def metadata(self) -> dict:
        """Return serializable policy parameters for run metadata."""
        return asdict(self.parameters)

    def _duration(
        self, assignment: Assignment, state: YardState, scenario: YardScenario
    ) -> tuple[float, float]:
        spec = next(x for x in scenario.loaders if x.id == assignment.loader_id)
        loader = state.loaders[spec.id]
        if assignment.source_kind == "truck":
            stop = state.trucks[assignment.source_id].stop_id
            source = next(x.location for x in scenario.stops if x.id == stop)
        else:
            source = next(x.location for x in scenario.piles if x.id == assignment.source_id)
        destinations = scenario.piles if assignment.destination_kind == "pile" else scenario.infeeds
        destination = next(x.location for x in destinations if x.id == assignment.destination_id)
        travel = (
            loader.location.distance_to(source) + source.distance_to(destination)
        ) / spec.speed
        return (
            travel,
            travel
            + assignment.quantity / spec.pickup_rate
            + assignment.quantity / spec.deposit_rate,
        )

    def _incoming_before(
        self, state: YardState, scenario: YardScenario, infeed_id: str, deadline: float
    ) -> float:
        """Count only committed deliveries with a known deposit completion by deadline.

        Proposed jobs use their estimated completion; active jobs are counted only
        when already depositing with a known completion time. Earlier-phase active
        jobs are not assumed to avert an imminent shortage.
        """
        total = 0.0
        for loader in state.loaders.values():
            a = loader.assignment
            if a is None or a.destination_kind != "infeed" or a.destination_id != infeed_id:
                continue
            completion = loader.phase_end if loader.phase == "deposit" else None
            if loader.phase == "idle":
                completion = state.time + self._duration(a, state, scenario)[1]
            if completion is not None and completion <= deadline:
                total += a.quantity
        return total

    def _score(self, a: Assignment, state: YardState, scenario: YardScenario) -> float:
        p = self.parameters
        travel, delivery = self._duration(a, state, scenario)
        travel_cost = p.travel_weight * travel / p.travel_reference
        if a.source_kind == "truck":
            truck = state.trucks[a.source_id]
            # Account for actual pickup time without double-counting concurrent loaders.
            handling = truck.handling_time + (
                state.time - truck.busy_since if truck.busy_since is not None else 0
            )
            wait = max(0.0, state.time - truck.arrival - handling)
            fraction = min(1.0, a.quantity / max(EPS, truck.remaining))
            queue = len(state.queue) / max(1, len(scenario.stops))
            # Fractional credit avoids counting the same truck twice in a joint set.
            return (
                p.truck_weight * (1 + wait / p.wait_reference) + p.queue_weight * queue
            ) * fraction - travel_cost
        spec = next(x for x in scenario.infeeds if x.id == a.destination_id)
        inv = state.infeeds[spec.id]
        deficit = max(0.0, spec.target - inv.amount - inv.incoming)
        if deficit <= EPS:
            return -travel_cost
        fraction = min(a.quantity, deficit) / spec.target
        deadline = state.time + delivery
        available = inv.amount + self._incoming_before(state, scenario, spec.id, deadline)
        # Begin raising urgency before the latest safe start; the factor of two
        # provides time for an intervening loader job or a handling-slot delay.
        coverage = available / spec.consumption_rate if spec.consumption_rate else float("inf")
        urgency = (
            p.starvation_weight * min(1.0, max(0.0, 2 - coverage / max(delivery, EPS))) * fraction
        )
        return p.refill_weight * fraction + urgency - travel_cost

    def choose(self, state: YardState, scenario: YardScenario) -> list[Assignment]:
        """Search jointly over feasible loader assignments and optional idling."""
        loader_ids = [x.id for x in scenario.loaders if state.loaders[x.id].assignment is None]
        best_score = 0.0
        best: list[Assignment] = []

        def search(index: int, trial: YardState, chosen: list[Assignment], score: float) -> None:
            nonlocal best_score, best
            if index == len(loader_ids):
                if score > best_score + EPS:
                    best_score, best = score, list(chosen)
                return
            search(index + 1, trial, chosen, score)
            for original in candidates(trial, scenario):
                if original.loader_id != loader_ids[index]:
                    continue
                a = original
                if a.destination_kind == "infeed":
                    spec = next(x for x in scenario.infeeds if x.id == a.destination_id)
                    inv = trial.infeeds[a.destination_id]
                    amount = min(a.quantity, max(0.0, spec.target - inv.amount - inv.incoming))
                    if amount <= EPS:
                        continue
                    a = replace(a, quantity=amount)
                increment = self._score(a, trial, scenario) - self.parameters.min_score
                next_state = deepcopy(trial)
                reserve(next_state, scenario, a)
                search(index + 1, next_state, [*chosen, a], score + increment)

        search(0, deepcopy(state), [], 0.0)
        return best

    def wake_levels(self, state: YardState, scenario: YardScenario) -> dict[str, float]:
        """Wake when an infeed falls below its configured low threshold."""
        return {spec.id: spec.low for spec in scenario.infeeds}
