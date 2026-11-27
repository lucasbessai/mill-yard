"""Policies that apply encoded decision sequences to yard dispatches."""

from collections.abc import Iterable
from dataclasses import dataclass
from operator import index

from mill_yard.model.feasibility import candidates
from mill_yard.model.yard import Assignment, YardScenario, YardState


@dataclass(frozen=True)
class DecisionAction:
    """Stable action-table entry, independent of current feasibility."""

    loader_id: str
    source_kind: str
    source_id: str
    destination_kind: str
    destination_id: str

    def matches(self, assignment: Assignment) -> bool:
        """Return whether a feasible assignment has this route and loader."""
        return (
            self.loader_id == assignment.loader_id
            and self.source_kind == assignment.source_kind
            and self.source_id == assignment.source_id
            and self.destination_kind == assignment.destination_kind
            and self.destination_id == assignment.destination_id
        )


def action_table(scenario: YardScenario) -> tuple[DecisionAction | None, ...]:
    """Build a fixed action-code table for all assignments in a scenario.

    Code zero is reserved for idling. Remaining entries enumerate loader,
    truck-to-pile, then pile-to-infeed combinations in scenario order. Truck
    IDs come from all scheduled arrivals, including trucks that have not yet
    arrived; those codes simply remain infeasible until the truck is available.
    """
    actions: list[DecisionAction | None] = [None]
    for loader in scenario.loaders:
        for arrival in scenario.arrivals:
            for pile in scenario.piles:
                actions.append(
                    DecisionAction(loader.id, "truck", arrival.id, "pile", pile.id)
                )
        for pile in scenario.piles:
            for infeed in scenario.infeeds:
                actions.append(
                    DecisionAction(loader.id, "pile", pile.id, "infeed", infeed.id)
                )
    return tuple(actions)


class DecisionSequencePolicy:
    """Apply one scenario-specific action code at each policy dispatch.

    Code ``0`` means idle. Each positive code refers to a fixed
    loader/source/destination combination in :func:`action_table`, not a
    position in the changing feasible-candidate list. If the selected action
    is unavailable, the policy idles and increments ``invalid_decisions``.

    The base is the number of entries in the scenario's action table, so codes
    range from zero to ``base - 1``. The table is scenario-specific because
    asset and scheduled truck IDs vary between scenarios.
    """

    def __init__(self, decisions: Iterable[int], scenario: YardScenario) -> None:
        """Create a policy from action-code coordinates and a scenario.

        The decision vector length is its number of policy dispatches. Every
        coordinate must be an integer action code in the scenario-specific
        range ``[0, base)``.
        """
        self.actions = action_table(scenario)
        self.base = len(self.actions)
        self.decisions = tuple(self._validate_code(value) for value in decisions)
        self.cursor = 0
        self.invalid_decisions = 0

    def _validate_code(self, value: int) -> int:
        """Validate and normalize one optimizer-vector coordinate."""
        if isinstance(value, bool):
            raise TypeError("decision codes must be integers, not booleans")
        try:
            code = index(value)
        except TypeError as error:
            raise TypeError("decision codes must be integers") from error
        if not 0 <= code < self.base:
            raise ValueError(f"decision codes must be in the range [0, {self.base})")
        return code

    @property
    def exhausted(self) -> bool:
        """Whether all encoded decisions have been consumed."""
        return self.cursor >= len(self.decisions)

    def choose(self, state: YardState, scenario: YardScenario) -> list[Assignment]:
        """Use the next code to choose idle or one feasible assignment."""
        if self.exhausted:
            return []

        action_code = self.decisions[self.cursor]
        self.cursor += 1
        action = self.actions[action_code]
        if action is None:
            return []

        assignment = next(
            (candidate for candidate in candidates(state, scenario) if action.matches(candidate)),
            None,
        )
        if assignment is None:
            self.invalid_decisions += 1
            return []
        # TODO: Consider encoding and returning a jointly feasible set of
        # assignments so multiple idle loaders can be dispatched in one call.
        return [assignment]

    def wake_levels(self, state: YardState, scenario: YardScenario) -> dict[str, float]:
        """Request the configured low inventory crossing for each infeed."""
        return {infeed.id: infeed.low for infeed in scenario.infeeds}

    def metadata(self) -> dict:
        """Return sequence and consumption status for run metadata."""
        return {
            "base": self.base,
            "action_code_count": len(self.actions),
            "sequence_length": len(self.decisions),
            "decision_codes": list(self.decisions),
            "decisions_used": self.cursor,
            "exhausted": self.exhausted,
            "invalid_decisions": self.invalid_decisions,
        }


__all__ = ["DecisionAction", "DecisionSequencePolicy", "action_table"]
