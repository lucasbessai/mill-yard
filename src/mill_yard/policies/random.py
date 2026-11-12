"""Seeded random feasible dispatch baseline."""

from copy import deepcopy
from random import Random
from secrets import randbits

from mill_yard.model.feasibility import candidates, reserve
from mill_yard.model.state import Assignment, YardState
from mill_yard.policies.base import PolicyContext


class RandomPolicy:
    """Uniform sequential choices from a temporary reservation view."""

    def __init__(self, seed: int | None = None) -> None:
        """Initialize the policy's independent random stream."""
        seed = randbits(64) if seed is None else seed
        self.random = Random(seed)
        self.seed = seed

    def get_state(self):
        """Expose the RNG state for decision records."""
        return self.random.getstate()

    def choose(self, state: YardState, context: PolicyContext) -> list[Assignment]:
        """Return a feasible set, choosing each candidate uniformly."""
        trial = deepcopy(state)
        chosen = []
        while options := candidates(trial, context):
            a = self.random.choice(options)
            reserve(trial, context, a)
            chosen.append(a)
        return chosen

    def wake_levels(self, state: YardState, context: PolicyContext) -> dict[str, float]:
        """Request the configured low levels for policy-driven wake-ups."""
        return {i.id: i.low for i in context.infeeds}
