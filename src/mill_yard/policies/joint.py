"""Simple policies producing sets of simultaneous assignments."""

from copy import deepcopy
from dataclasses import replace
from random import Random
from typing import Protocol

from mill_yard.model.feasibility import EPS, candidates, reserve
from mill_yard.model.yard import Assignment, YardScenario, YardState


class JointPolicy(Protocol):
    """Choose a jointly feasible set and optional infeed wake levels."""

    def choose(self, state: YardState, scenario: YardScenario) -> list[Assignment]:
        """Return zero or more assignments."""
        ...

    def wake_levels(self, state: YardState, scenario: YardScenario) -> dict[str, float]:
        """Request future inventory crossings, by infeed ID."""
        ...


class ThresholdPolicy:
    """Refill low infeeds toward target, then unload the oldest stopped truck."""

    def choose(self, state: YardState, scenario: YardScenario) -> list[Assignment]:
        """Build a deterministic joint proposal with a temporary reservation view."""
        trial = deepcopy(state)
        chosen = []
        for spec in scenario.infeeds:
            inv = trial.infeeds[spec.id]
            if inv.amount <= spec.low + EPS:
                inv.refill_active = True
            if inv.amount + inv.incoming >= spec.target - EPS:
                inv.refill_active = False
        while True:
            options = candidates(trial, scenario)
            refill = [
                a
                for a in options
                if a.destination_kind == "infeed" and trial.infeeds[a.destination_id].refill_active
            ]
            unload = [a for a in options if a.source_kind == "truck"]
            if refill:
                a = refill[0]
                spec = next(i for i in scenario.infeeds if i.id == a.destination_id)
                inv = trial.infeeds[a.destination_id]
                a = replace(a, quantity=min(a.quantity, spec.target - inv.amount - inv.incoming))
            elif unload:
                a = min(
                    unload,
                    key=lambda x: (
                        trial.trucks[x.source_id].arrival,
                        x.source_id,
                        x.loader_id,
                        x.destination_id,
                    ),
                )
            else:
                break
            reserve(trial, scenario, a)
            chosen.append(a)
            for spec in scenario.infeeds:
                if (
                    trial.infeeds[spec.id].amount + trial.infeeds[spec.id].incoming
                    >= spec.target - EPS
                ):
                    trial.infeeds[spec.id].refill_active = False
        return chosen

    def wake_levels(self, state: YardState, scenario: YardScenario) -> dict[str, float]:
        """Request each infeed's low threshold."""
        return {i.id: i.low for i in scenario.infeeds}


class RandomPolicy:
    """Uniform sequential choices from a temporary reservation view."""

    def __init__(self, seed: int | None = None) -> None:
        """Initialize the policy's independent random stream."""
        self.random = Random(seed)
        self.seed = seed

    def choose(self, state: YardState, scenario: YardScenario) -> list[Assignment]:
        """Return a feasible set, choosing each candidate uniformly."""
        trial = deepcopy(state)
        chosen = []
        while options := candidates(trial, scenario):
            a = self.random.choice(options)
            reserve(trial, scenario, a)
            chosen.append(a)
        return chosen

    def wake_levels(self, state: YardState, scenario: YardScenario) -> dict[str, float]:
        """Request the configured low levels for policy-driven wake-ups."""
        return {i.id: i.low for i in scenario.infeeds}
