"""Strict YAML adapters and composition of reusable scenario inputs."""

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

import yaml

from mill_yard.model.configuration import (
    InfeedDesign,
    InitialConditions,
    InitialStock,
    LoaderDesign,
    LoaderStart,
    PileDesign,
    ScheduledArrival,
    TruckSchedule,
    YardDesign,
    compose_scenario,
)
from mill_yard.model.scenario import (
    InfeedSpec,
    LoaderSpec,
    PileSpec,
    StopSpec,
    UnloadingAreaSpec,
    YardScenario,
)
from mill_yard.model.types import Arrival, ConfigurationSource, Point


class _StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ValueError(f"Duplicate YAML key: {key}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _read(path):
    path = Path(path).expanduser().resolve()
    raw = path.read_bytes()
    try:
        data = yaml.load(raw.decode("utf-8-sig"), Loader=_StrictLoader)
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a YAML mapping")
        if any(not isinstance(key, str) for key in data):
            raise ValueError("Configuration keys must be strings")
    except (yaml.YAMLError, UnicodeError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    return data, ConfigurationSource(
        str(data.get("kind", "legacy_scenario")), str(path), sha256(raw).hexdigest()
    )


def _typed(path, expected):
    data, source = _read(path)
    kind = data.pop("kind", None)
    version = data.pop("schema_version", None)
    if kind != expected:
        raise ValueError(f"{path}: expected kind: {expected}, got {kind!r}")
    if type(version) is not int or version != 1:
        raise ValueError(f"{path}: configuration schema_version must be 1")
    return data, source


def _located(items, cls):
    result = []
    for item in items:
        values = dict(item)
        values["location"] = Point(**values["location"])
        result.append(cls(**values))
    return tuple(result)


def _areas(items):
    result = []
    for item in items:
        values = dict(item)
        if values.get("queue_location") is not None:
            values["queue_location"] = Point(**values["queue_location"])
        result.append(UnloadingAreaSpec(**values))
    return tuple(result)


def _yard(path):
    data, source = _typed(path, "yard")
    try:
        data["unloading_areas"] = _areas(data.get("unloading_areas", []))
        for key, cls in (("stops", StopSpec), ("piles", PileDesign), ("infeeds", InfeedDesign)):
            data[key] = _located(data[key], cls)
        data["loaders"] = tuple(LoaderDesign(**x) for x in data["loaders"])
        return YardDesign(**data), source
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid yard {path}: {exc}") from exc


def _schedule(path):
    data, source = _typed(path, "truck_schedule")
    try:
        data["arrivals"] = tuple(ScheduledArrival(**x) for x in data["arrivals"])
        return TruckSchedule(**data), source
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid truck schedule {path}: {exc}") from exc


def _initial(path):
    data, source = _typed(path, "initial_conditions")
    try:
        for key in ("piles", "infeeds"):
            data[key] = tuple(InitialStock(**x) for x in data.get(key, []))
        data["loaders"] = _located(data.get("loaders", []), LoaderStart)
        return InitialConditions(**data), source
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid initial conditions {path}: {exc}") from exc


def load_yard_design(path):
    return _yard(path)[0]


def load_truck_schedule(path):
    return _schedule(path)[0]


def load_initial_conditions(path):
    return _initial(path)[0]


def _legacy(path):
    data, source = _read(path)
    try:
        data["unloading_areas"] = _areas(data.get("unloading_areas", []))
        data["arrivals"] = tuple(Arrival(**item) for item in data["arrivals"])
        for key, cls in (
            ("stops", StopSpec),
            ("piles", PileSpec),
            ("infeeds", InfeedSpec),
            ("loaders", LoaderSpec),
        ):
            data[key] = _located(data[key], cls)
        data["configuration_sources"] = tuple(
            ConfigurationSource(**x) for x in data.get("configuration_sources", [])
        )
        return replace(YardScenario(**data), configuration_sources=(source,))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid scenario {path}: {exc}") from exc


@dataclass(frozen=True)
class ScenarioFiles:
    """A reusable selection of input files, plus explicit run-setting overrides."""

    yard: Path | None = None
    truck_schedule: Path | None = None
    initial_conditions: Path | None = None
    legacy: Path | None = None
    experiment: Path | None = None
    experiment_source: ConfigurationSource | None = None
    horizon: float | None = None
    wake_mode: str | None = None
    wake_interval: float | None = None

    def __post_init__(self):
        for key in ("yard", "truck_schedule", "initial_conditions", "legacy", "experiment"):
            value = getattr(self, key)
            if value is not None:
                if not isinstance(value, (str, Path)) or not str(value):
                    raise ValueError(f"{key} must be a nonempty file path")
                object.__setattr__(self, key, Path(value).expanduser().resolve())

    @property
    def label(self):
        if self.experiment is not None:
            return str(self.experiment)
        if self.legacy is not None:
            return str(self.legacy)
        return "Custom inputs"

    def load(self, *, horizon=None, wake_mode=None, wake_interval=None):
        effective_horizon = self.horizon if horizon is None else horizon
        mode = self.wake_mode if wake_mode is None else wake_mode
        interval = self.wake_interval if wake_interval is None else wake_interval
        parts = (self.yard, self.truck_schedule, self.initial_conditions)
        if self.legacy is not None:
            if any(x is not None for x in parts):
                raise ValueError("Choose a combined legacy scenario or three component files")
            scenario = _legacy(self.legacy)
            return replace(
                scenario,
                horizon=scenario.horizon if effective_horizon is None else effective_horizon,
                wake_mode=scenario.wake_mode if mode is None else mode,
                wake_interval=scenario.wake_interval if interval is None else interval,
            )
        if any(x is None for x in parts):
            raise ValueError("Select a yard, truck schedule, and initial conditions")
        yard, yard_source = _yard(self.yard)
        schedule, schedule_source = _schedule(self.truck_schedule)
        initial, initial_source = _initial(self.initial_conditions)
        try:
            scenario = compose_scenario(
                yard,
                schedule,
                initial,
                horizon=effective_horizon,
                wake_mode="interval" if mode is None else mode,
                wake_interval=5.0 if interval is None else interval,
            )
        except ValueError as exc:
            raise ValueError(
                f"Incompatible configuration ({self.yard.name}, {self.truck_schedule.name}, {self.initial_conditions.name}): {exc}"
            ) from exc
        sources = (yard_source, schedule_source, initial_source)
        if self.experiment_source is not None:
            sources = (self.experiment_source, *sources)
        return replace(scenario, configuration_sources=sources)


def load_scenario_files(path) -> ScenarioFiles:
    """Read an experiment descriptor; preserve combined legacy file support."""
    path = Path(path).expanduser().resolve()
    data, source = _read(path)
    if "kind" not in data:
        return ScenarioFiles(legacy=path)
    if data.pop("kind") != "experiment":
        raise ValueError(f"{path}: choose kind: experiment or provide all three component files")
    if type(data.get("schema_version")) is not int or data.pop("schema_version") != 1:
        raise ValueError(f"{path}: configuration schema_version must be 1")
    allowed = {
        "yard",
        "truck_schedule",
        "initial_conditions",
        "horizon",
        "wake_mode",
        "wake_interval",
    }
    if data.keys() - allowed:
        raise ValueError(
            f"{path}: unknown experiment fields: {', '.join(sorted(data.keys() - allowed))}"
        )
    try:
        for key in ("yard", "truck_schedule", "initial_conditions"):
            value = data[key]
            if not isinstance(value, str) or not value:
                raise ValueError(f"{key} must be a nonempty file path")
            data[key] = (path.parent / Path(value).expanduser()).resolve()
        return ScenarioFiles(**data, experiment=path, experiment_source=source)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid experiment {path}: {exc}") from exc


def select_scenario_files(
    path=None,
    *,
    yard=None,
    truck_schedule=None,
    initial_conditions=None,
    horizon=None,
    wake_mode=None,
    wake_interval=None,
):
    """Shared CLI/GUI adapter for presets or three independently selected files."""
    parts = (yard, truck_schedule, initial_conditions)
    if path is not None:
        if any(x is not None for x in parts):
            raise ValueError(
                "Choose an experiment path or --yard/--truck-schedule/--initial-conditions"
            )
        files = load_scenario_files(path)
    else:
        if any(x is None for x in parts):
            raise ValueError("Provide an experiment path or all three component files")
        files = ScenarioFiles(*(Path(x).expanduser().resolve() for x in parts))
    changes = {
        key: value
        for key, value in (
            ("horizon", horizon),
            ("wake_mode", wake_mode),
            ("wake_interval", wake_interval),
        )
        if value is not None
    }
    return replace(files, **changes)


def load_yard_scenario(path: str | Path, **overrides) -> YardScenario:
    """Load a preset or legacy scenario into the unchanged resolved engine API."""
    return select_scenario_files(path, **overrides).load()
