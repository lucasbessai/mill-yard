"""Toolkit-independent, staged configuration selection for both frontends."""

from dataclasses import dataclass
from pathlib import Path

from mill_yard.config.loader import (
    ScenarioFiles,
    load_initial_conditions,
    load_truck_schedule,
    load_yard_design,
)

COMPONENT_LABELS = {
    "yard": "Yard definition",
    "truck_schedule": "Truck schedule",
    "initial_conditions": "Initial conditions",
}
COMPONENT_DIRECTORIES = {
    "yard": "yards",
    "truck_schedule": "truck_schedules",
    "initial_conditions": "initial_conditions",
}
READERS = {
    "yard": load_yard_design,
    "truck_schedule": load_truck_schedule,
    "initial_conditions": load_initial_conditions,
}


@dataclass
class ConfigurationDraft:
    """Editing a selection never mutates or restarts the current simulation."""

    base: ScenarioFiles
    yard: str = ""
    truck_schedule: str = ""
    initial_conditions: str = ""
    horizon: str = ""

    @classmethod
    def from_files(cls, files):
        return cls(
            files,
            *(str(getattr(files, key) or "") for key in COMPONENT_LABELS),
            "" if files.horizon is None else str(files.horizon),
        )

    def choose(self, kind, path):
        path = Path(path).expanduser().resolve()
        READERS[kind](path)  # Reject the wrong file kind before accepting the field.
        setattr(self, kind, str(path))

    def files(self):
        if any(not getattr(self, key).strip() for key in COMPONENT_LABELS):
            raise ValueError("Select a yard, truck schedule, and initial conditions")
        paths = {key: Path(getattr(self, key)).expanduser().resolve() for key in COMPONENT_LABELS}
        try:
            horizon = float(self.horizon) if self.horizon.strip() else None
        except ValueError as exc:
            raise ValueError("Horizon override must be a positive number or blank") from exc
        unchanged = all(paths[key] == getattr(self.base, key) for key in paths)
        return ScenarioFiles(
            **paths,
            horizon=horizon,
            wake_mode=self.base.wake_mode,
            wake_interval=self.base.wake_interval,
            experiment=self.base.experiment if unchanged else None,
            experiment_source=self.base.experiment_source if unchanged else None,
        )

    def validate(self):
        try:
            scenario = self.files().load()
            entering = sum(a.time <= scenario.horizon for a in scenario.arrivals)
            return (
                True,
                f"Ready: {scenario.horizon:g} min; {entering}/{len(scenario.arrivals)} scheduled trucks enter within the horizon.",
            )
        except (OSError, ValueError) as exc:
            return False, str(exc)

    def directory(self, kind):
        value = getattr(self, kind)
        if value:
            return Path(value).expanduser().resolve().parent
        return Path(__file__).resolve().parents[3] / "configs" / COMPONENT_DIRECTORIES[kind]
