"""Result data independent of SimPy execution machinery."""

from dataclasses import dataclass

from mill_yard.model.scenario import YardScenario
from mill_yard.model.state import YardState


@dataclass
class YardResult:
    """Final physical state, completed trucks, and optional event history."""

    scenario: YardScenario
    state: YardState
    completed_trucks: list[dict]
    events: list[dict]
    policy: str
    seed: int | None
    recording_level: str
    policy_parameters: dict
    events_path: str | None = None
