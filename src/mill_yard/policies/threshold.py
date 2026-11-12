"""Threshold replenishment and FIFO stopped-truck dispatch."""

from copy import deepcopy
from dataclasses import replace

from mill_yard.model.feasibility import EPS, candidates, reserve
from mill_yard.model.state import Assignment, YardState
from mill_yard.policies.base import PolicyContext


class ThresholdPolicy:
    """Refill low infeeds toward target, then unload the oldest stopped truck."""

    def __init__(self):
        self.refill_active = {}

    def get_state(self):
        """Return policy memory needed for reproducible decisions."""
        return dict(self.refill_active)

    def choose(self, state: YardState, context: PolicyContext) -> list[Assignment]:
        """Build a deterministic joint proposal with a temporary reservation view."""
        trial = deepcopy(state)
        chosen = []
        active = dict(self.refill_active)
        for spec in context.infeeds:
            inv = trial.infeeds[spec.id]
            if inv.amount <= spec.low + EPS:
                active[spec.id] = True
            if inv.amount + inv.reserved_incoming >= spec.target - EPS:
                active[spec.id] = False
        while True:
            options = candidates(trial, context)
            refill = [
                a
                for a in options
                if a.destination_kind == "infeed" and active.get(a.destination_id, False)
            ]
            unload = [a for a in options if a.source_kind == "truck"]
            if refill:
                a = refill[0]
                spec = next(i for i in context.infeeds if i.id == a.destination_id)
                inv = trial.infeeds[a.destination_id]
                a = replace(
                    a, quantity=min(a.quantity, spec.target - inv.amount - inv.reserved_incoming)
                )
            elif unload:
                a = min(
                    unload,
                    key=lambda x: (
                        trial.trucks[x.source_id].arrived_at,
                        x.source_id,
                        x.loader_id,
                        x.destination_id,
                    ),
                )
            else:
                break
            reserve(trial, context, a)
            chosen.append(a)
            for spec in context.infeeds:
                if (
                    trial.infeeds[spec.id].amount + trial.infeeds[spec.id].reserved_incoming
                    >= spec.target - EPS
                ):
                    active[spec.id] = False
        self.refill_active = active
        return chosen

    def wake_levels(self, state: YardState, context: PolicyContext) -> dict[str, float]:
        """Request each infeed's low threshold."""
        return {i.id: i.low for i in context.infeeds}
