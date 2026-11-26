"""Configurable scored dispatch for small fleets of loaders."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from math import isfinite

from mill_yard.model.feasibility import EPS, candidates, reserve
from mill_yard.model.state import Assignment, YardState
from mill_yard.model.timing import job_duration, remaining_duration
from mill_yard.policies.base import PolicyContext


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
    assignment_penalty: float = 0.05
    lookahead: float = 30.0
    delivery_weight: float = 0.02

    def __post_init__(self) -> None:
        """Reject invalid weights and reference scales."""
        for f in fields(self):
            value = getattr(self, f.name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{f.name} must be finite and nonnegative")
        if self.wait_reference <= 0 or self.travel_reference <= 0 or self.lookahead <= 0:
            raise ValueError("Reference times must be positive")


def predicted_starvation(inventory, rate, deliveries, horizon):
    """Integrate shortage minutes over a fixed horizon and delivery timeline."""
    if rate == 0:
        return 0.0
    previous = 0.0
    shortage = 0.0
    for time, quantity in sorted([*deliveries, (horizon, 0.0)]):
        if time > horizon:
            break
        time = max(previous, time)
        elapsed = time - previous
        shortage += max(0.0, elapsed - inventory / rate)
        inventory = max(0.0, inventory - rate * elapsed) + quantity
        previous = time
    return shortage


class ScoredPolicy:
    """Evaluate complete assignment sets against one original observation.

    Shortage benefit is measured on a common planning horizon. Slower jobs do not
    receive extra urgency. Estimates use deterministic phase durations and exclude
    future queue delays; this is a heuristic, not an optimal scheduling algorithm.
    """

    def __init__(self, parameters: ScoredParameters | None = None):
        self.parameters = parameters or ScoredParameters()

    def metadata(self):
        """Return parameters needed to reproduce the heuristic."""
        return asdict(self.parameters)

    def _deliveries(self, state, context):
        deliveries = {i.id: [] for i in context.infeeds}
        for loader in state.loaders.values():
            a = loader.assignment
            if a is not None and a.destination_kind == "infeed":
                deliveries[a.destination_id].append(
                    (remaining_duration(loader, state, context), a.quantity)
                )
        return deliveries

    def score_joint(self, assignments, state, context):
        """Score the final set without prefix-order dependence or duplicate credit."""
        p = self.parameters
        baseline = self._deliveries(state, context)
        future = {key: list(value) for key, value in baseline.items()}
        truck_quantities = {}
        score = 0.0
        for a in assignments:
            travel, duration = job_duration(a, state, context)
            score -= (
                p.travel_weight * travel / p.travel_reference
                + p.delivery_weight * duration / p.travel_reference
                + p.assignment_penalty
            )
            if a.source_kind == "truck":
                truck_quantities[a.source_id] = truck_quantities.get(a.source_id, 0.0) + a.quantity
            else:
                future[a.destination_id].append((duration, a.quantity))
        for truck_id, quantity in truck_quantities.items():
            truck = state.trucks[truck_id]
            handling = truck.pickup_service_time + (
                state.time - truck.busy_since if truck.busy_since is not None else 0
            )
            wait = max(0.0, state.time - truck.arrived_at - handling)
            score += min(1.0, quantity / truck.remaining_quantity) * (
                p.truck_weight * (1 + wait / p.wait_reference)
                + p.queue_weight * state.waiting_truck_count / len(context.stops)
            )
        for spec in context.infeeds:
            inv = state.infeeds[spec.id]
            before = predicted_starvation(
                inv.amount, spec.consumption_rate, baseline[spec.id], p.lookahead
            )
            after = predicted_starvation(
                inv.amount, spec.consumption_rate, future[spec.id], p.lookahead
            )
            score += p.starvation_weight * (before - after) / p.lookahead
            quantity = sum(
                a.quantity
                for a in assignments
                if a.destination_kind == "infeed" and a.destination_id == spec.id
            )
            deficit = max(0.0, spec.target - inv.amount - inv.reserved_incoming)
            score += p.refill_weight * min(quantity, deficit) / spec.target
        return score

    def choose(self, state: YardState, context: PolicyContext) -> list[Assignment]:
        """Enumerate feasible sets in canonical loader order, including idling."""
        ids = sorted(x.id for x in context.loaders if state.loaders[x.id].assignment is None)
        best_score, best = 0.0, []

        def search(index, trial, chosen):
            nonlocal best_score, best
            if index == len(ids):
                value = self.score_joint(chosen, state, context)
                if value > best_score + EPS:
                    best_score, best = value, list(chosen)
                return
            search(index + 1, trial, chosen)
            options = sorted(
                candidates(trial, context),
                key=lambda a: (
                    a.loader_id,
                    a.source_kind,
                    a.source_id,
                    a.destination_kind,
                    a.destination_id,
                ),
            )
            for a in options:
                if a.loader_id != ids[index]:
                    continue
                if a.destination_kind == "infeed":
                    spec = next(i for i in context.infeeds if i.id == a.destination_id)
                    inv = trial.infeeds[spec.id]
                    quantity = min(
                        a.quantity, max(0.0, spec.target - inv.amount - inv.reserved_incoming)
                    )
                    if quantity <= EPS:
                        continue
                    a = replace(a, quantity=quantity)
                next_state = deepcopy(trial)
                reserve(next_state, context, a)
                search(index + 1, next_state, [*chosen, a])

        search(0, deepcopy(state), [])
        return best

    def wake_levels(self, state, context):
        """Request a low-inventory decision opportunity for each infeed."""
        return {spec.id: spec.low for spec in context.infeeds}
