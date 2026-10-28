"""Configuration and state for the multi-location yard model.

Units are minutes, metres, and tonnes. Inventory is a single material type.
"""

from dataclasses import dataclass, field
from math import isfinite

from mill_yard.model.types import Arrival, Point


@dataclass(frozen=True)
class StopSpec:
    """Truck stop and maximum simultaneous pickup operations."""

    id: str
    location: Point
    max_loaders: int = 1


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
            if any(not isfinite(v) for x in group for v in (x.location.x, x.location.y)):
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
            if not nonnegative(x.consumption_rate) or not (0 <= x.low < x.target <= x.capacity):
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


@dataclass
class TruckState:
    """Active truck, including pickup reservations and timing."""

    id: str
    arrival: float
    remaining: float
    reserved: float = 0.0
    stop_id: str | None = None
    entered_at: float | None = None
    first_pickup_at: float | None = None
    active_pickups: int = 0
    busy_since: float | None = None
    handling_time: float = 0.0


@dataclass
class InventoryState:
    """Inventory and commitments for a pile or infeed."""

    amount: float
    incoming: float = 0.0
    outgoing: float = 0.0
    refill_active: bool = False


@dataclass(frozen=True)
class Assignment:
    """One loader's complete source-to-destination job."""

    loader_id: str
    source_kind: str
    source_id: str
    destination_kind: str
    destination_id: str
    quantity: float


@dataclass
class LoaderState:
    """Loader's location, committed job, phase, and onboard material."""

    id: str
    location: Point
    assignment: Assignment | None = None
    phase: str = "idle"
    phase_end: float | None = None
    carried: float = 0.0
    distance: float = 0.0
    travel_time: float = 0.0
    travel_started: float | None = None
    travel_distance: float = 0.0


@dataclass
class YardState:
    """Mutable simulation state; snapshot copies are stored by the recorder."""

    time: float
    trucks: dict[str, TruckState]
    queue: list[str]
    stops: dict[str, str | None]
    piles: dict[str, InventoryState]
    infeeds: dict[str, InventoryState]
    loaders: dict[str, LoaderState]
    consumed: dict[str, float] = field(default_factory=dict)
    starvation: dict[str, float] = field(default_factory=dict)
