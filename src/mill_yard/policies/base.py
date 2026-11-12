"""Policy contracts and deliberately restricted scenario information."""

from dataclasses import dataclass
from typing import Protocol

from mill_yard.model.scenario import (
    InfeedSpec,
    LoaderSpec,
    PileSpec,
    StopSpec,
    UnloadingAreaSpec,
    YardScenario,
)
from mill_yard.model.state import Assignment, YardState


@dataclass(frozen=True)
class PolicyContext:
    """Known equipment and operating settings; future arrivals are excluded."""

    horizon: float
    stops: tuple[StopSpec, ...]
    piles: tuple[PileSpec, ...]
    infeeds: tuple[InfeedSpec, ...]
    loaders: tuple[LoaderSpec, ...]
    wake_mode: str
    wake_interval: float
    unloading_areas: tuple[UnloadingAreaSpec, ...]

    @classmethod
    def from_scenario(cls, scenario: YardScenario) -> "PolicyContext":
        """Copy only fields a policy is permitted to observe."""
        return cls(**{name: getattr(scenario, name) for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class DecisionRequest:
    """A paused dispatch observation awaiting an operator's assignment set."""

    state: YardState
    context: PolicyContext


class DispatchPolicy(Protocol):
    """Choose assignments using a copied state and public operating context."""

    def choose(
        self, state: YardState, context: PolicyContext
    ) -> list[Assignment] | DecisionRequest:
        """Return assignments or request operator input without advancing time."""
        ...

    def wake_levels(self, state: YardState, context: PolicyContext) -> dict[str, float]:
        """Request inventory crossings; an omitted infeed requests no wake-up."""
        ...
