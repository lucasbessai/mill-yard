"""Validated specifications and scenario inputs for a yard run.

Units are minutes, metres, and tonnes. Inventory is a single material type.
"""

from dataclasses import dataclass, field
from math import isfinite
from typing import Protocol

from mill_yard.model.types import Arrival, ConfigurationSource, Point


@dataclass(frozen=True)
class UnloadingAreaSpec:
    """Independent truck queue and its display anchor; travel still uses stops."""

    id: str
    queue_location: Point | None = None


@dataclass(frozen=True)
class StopSpec:
    """Truck stop and maximum simultaneous pickup operations."""

    id: str
    location: Point
    max_loaders: int = 1
    area_id: str = "main"


@dataclass(frozen=True)
class PileSpec:
    """Pile capacity, starting inventory, and handling concurrency."""

    id: str
    location: Point
    capacity: float
    initial: float
    max_loaders: int = 4


@dataclass(frozen=True)
class InfeedSpec:
    """Infeed inventory, consumption rate, and policy thresholds."""

    id: str
    location: Point
    capacity: float
    initial: float
    consumption_rate: float
    low: float
    target: float
    max_loaders: int = 1


@dataclass(frozen=True)
class LoaderSpec:
    """Individual loader characteristics."""

    id: str
    location: Point
    capacity: float
    speed: float
    pickup_rate: float
    deposit_rate: float


class YardLayout(Protocol):
    """Read-only asset view shared by scenarios and restricted policy contexts."""

    @property
    def stops(self) -> tuple[StopSpec, ...]: ...

    @property
    def piles(self) -> tuple[PileSpec, ...]: ...

    @property
    def infeeds(self) -> tuple[InfeedSpec, ...]: ...

    @property
    def loaders(self) -> tuple[LoaderSpec, ...]: ...

    @property
    def unloading_areas(self) -> tuple[UnloadingAreaSpec, ...]: ...


@dataclass(frozen=True)
class YardScenario:
    """Resolved configuration of the multi-location simulation."""

    horizon: float
    arrivals: tuple[Arrival, ...]
    stops: tuple[StopSpec, ...]
    piles: tuple[PileSpec, ...]
    infeeds: tuple[InfeedSpec, ...]
    loaders: tuple[LoaderSpec, ...]
    wake_mode: str = "interval"
    wake_interval: float = 5.0
    unloading_areas: tuple[UnloadingAreaSpec, ...] = ()
    configuration_sources: tuple[ConfigurationSource, ...] = field(
        default=(), compare=False, repr=False
    )

    def __post_init__(self) -> None:
        """Validate identifiers, dimensions, and physical parameters."""

        def positive(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) and v > 0

        def nonnegative(v):
            return (
                isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) and v >= 0
            )

        if not positive(self.horizon) or not positive(self.wake_interval):
            raise ValueError("Horizon and wake interval must be finite and positive")
        if self.wake_mode not in ("interval", "policy"):
            raise ValueError("wake_mode must be interval or policy")
        for group in (self.stops, self.piles, self.infeeds, self.loaders):
            if not group or any(not isinstance(x.id, str) or not x.id for x in group):
                raise ValueError("Every asset group needs at least one named asset")
            if len({x.id for x in group}) != len(group):
                raise ValueError("Asset IDs must be unique within their group")
            if any(
                not isinstance(v, (int, float)) or isinstance(v, bool) or not isfinite(v)
                for x in group
                for v in (x.location.x, x.location.y)
            ):
                raise ValueError("Coordinates must be finite")
        for x in (*self.stops, *self.piles, *self.infeeds):
            if (
                not isinstance(x.max_loaders, int)
                or isinstance(x.max_loaders, bool)
                or x.max_loaders < 1
            ):
                raise ValueError("Handling concurrency must be a positive integer")
        for x in (*self.piles, *self.infeeds):
            if not positive(x.capacity) or not nonnegative(x.initial) or x.initial > x.capacity:
                raise ValueError("Invalid inventory or capacity")
        for x in self.infeeds:
            if (
                not nonnegative(x.consumption_rate)
                or not nonnegative(x.low)
                or not nonnegative(x.target)
                or not (0 <= x.low < x.target <= x.capacity)
            ):
                raise ValueError("Invalid consumption or infeed thresholds")
        for x in self.loaders:
            if not all(positive(v) for v in (x.capacity, x.speed, x.pickup_rate, x.deposit_rate)):
                raise ValueError("Loader capacity, speed and rates must be positive")
        if any(
            not isinstance(a.id, str)
            or not a.id
            or not nonnegative(a.time)
            or not positive(a.quantity)
            for a in self.arrivals
        ):
            raise ValueError("Invalid truck arrival")
        if len({a.id for a in self.arrivals}) != len(self.arrivals):
            raise ValueError("Truck IDs must be unique")
        areas = self.unloading_areas or (UnloadingAreaSpec("main"),)
        if any(not isinstance(a.id, str) or not a.id for a in areas) or len(
            {a.id for a in areas}
        ) != len(areas):
            raise ValueError("Unloading area IDs must be named and unique")
        area_ids = {a.id for a in areas}
        if any(not isinstance(s.area_id, str) or s.area_id not in area_ids for s in self.stops):
            raise ValueError("Every stop must reference a known unloading area")
        if any(not isinstance(a.area_id, str) or a.area_id not in area_ids for a in self.arrivals):
            raise ValueError("Every arrival must reference a known unloading area")
        normalized = []
        for area in areas:
            stops = [s for s in self.stops if s.area_id == area.id]
            if not stops:
                raise ValueError("Every unloading area needs at least one truck stop")
            anchor = area.queue_location
            if anchor is None:
                anchor = Point(
                    sum(s.location.x for s in stops) / len(stops),
                    sum(s.location.y for s in stops) / len(stops),
                )
            if any(
                not isinstance(v, (int, float)) or isinstance(v, bool) or not isfinite(v)
                for v in (anchor.x, anchor.y)
            ):
                raise ValueError("Queue coordinates must be finite")
            normalized.append(UnloadingAreaSpec(area.id, anchor))
        object.__setattr__(self, "unloading_areas", tuple(normalized))
