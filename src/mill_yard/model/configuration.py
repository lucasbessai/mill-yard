"""Independent, immutable yard, supply, and initial-condition definitions.

Composition is pure Python: optimizers can reuse these objects without YAML or
file access. The existing YardScenario remains the fully resolved engine input.
"""

from dataclasses import dataclass
from math import isfinite

from mill_yard.model.scenario import (
    InfeedSpec,
    LoaderSpec,
    PileSpec,
    StopSpec,
    UnloadingAreaSpec,
    YardScenario,
)
from mill_yard.model.types import Arrival, Point


def _number(value, *, positive=False):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and (value > 0 if positive else value >= 0)
    )


def _named_unique(items, label):
    if any(not isinstance(x.id, str) or not x.id for x in items) or len(
        {x.id for x in items}
    ) != len(items):
        raise ValueError(f"{label} IDs must be nonempty and unique")


@dataclass(frozen=True)
class PileDesign:
    id: str
    location: Point
    capacity: float
    max_loaders: int = 4


@dataclass(frozen=True)
class InfeedDesign:
    id: str
    location: Point
    capacity: float
    consumption_rate: float
    low: float
    target: float
    max_loaders: int = 1


@dataclass(frozen=True)
class LoaderDesign:
    id: str
    capacity: float
    speed: float
    pickup_rate: float
    deposit_rate: float


@dataclass(frozen=True)
class YardDesign:
    stops: tuple[StopSpec, ...]
    piles: tuple[PileDesign, ...]
    infeeds: tuple[InfeedDesign, ...]
    loaders: tuple[LoaderDesign, ...]
    unloading_areas: tuple[UnloadingAreaSpec, ...] = ()

    def __post_init__(self):
        # Reuse the engine specification's physical validation, without supply
        # or initial stock. This also resolves legacy main/area display anchors.
        for key in ("stops", "piles", "infeeds", "loaders", "unloading_areas"):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        validated = _resolved(self, 1, (), {}, {}, {x.id: Point(0, 0) for x in self.loaders})
        object.__setattr__(self, "unloading_areas", validated.unloading_areas)


@dataclass(frozen=True)
class ScheduledArrival:
    time: float
    quantity: float
    id: str
    area_id: str | None = None


@dataclass(frozen=True)
class TruckSchedule:
    horizon: float
    arrivals: tuple[ScheduledArrival, ...]

    def __post_init__(self):
        object.__setattr__(self, "arrivals", tuple(self.arrivals))
        if not _number(self.horizon, positive=True):
            raise ValueError("Schedule horizon must be finite and positive")
        _named_unique(self.arrivals, "Truck")
        for a in self.arrivals:
            if not _number(a.time) or not _number(a.quantity, positive=True):
                raise ValueError(f"Invalid time or quantity for truck '{a.id}'")
            if a.area_id is not None and (not isinstance(a.area_id, str) or not a.area_id):
                raise ValueError(f"Invalid area_id for truck '{a.id}'")


@dataclass(frozen=True)
class InitialStock:
    id: str
    amount: float


@dataclass(frozen=True)
class LoaderStart:
    id: str
    location: Point


@dataclass(frozen=True)
class InitialConditions:
    piles: tuple[InitialStock, ...] = ()
    infeeds: tuple[InitialStock, ...] = ()
    loaders: tuple[LoaderStart, ...] = ()

    def __post_init__(self):
        for key in ("piles", "infeeds", "loaders"):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        for group, label in (
            (self.piles, "Initial pile"),
            (self.infeeds, "Initial infeed"),
            (self.loaders, "Loader start"),
        ):
            _named_unique(group, label)
        if any(not _number(x.amount) for x in (*self.piles, *self.infeeds)):
            raise ValueError("Initial inventory must be finite and nonnegative")
        if any(
            not isinstance(v, (int, float)) or isinstance(v, bool) or not isfinite(v)
            for x in self.loaders
            for v in (x.location.x, x.location.y)
        ):
            raise ValueError("Initial loader coordinates must be finite")


def _resolved(yard, horizon, arrivals, pile_stock, infeed_stock, positions, **settings):
    return YardScenario(
        horizon=horizon,
        arrivals=tuple(arrivals),
        stops=yard.stops,
        piles=tuple(
            PileSpec(p.id, p.location, p.capacity, pile_stock.get(p.id, 0), p.max_loaders)
            for p in yard.piles
        ),
        infeeds=tuple(
            InfeedSpec(
                i.id,
                i.location,
                i.capacity,
                infeed_stock.get(i.id, 0),
                i.consumption_rate,
                i.low,
                i.target,
                i.max_loaders,
            )
            for i in yard.infeeds
        ),
        loaders=tuple(
            LoaderSpec(x.id, positions[x.id], x.capacity, x.speed, x.pickup_rate, x.deposit_rate)
            for x in yard.loaders
        ),
        unloading_areas=yard.unloading_areas,
        **settings,
    )


def compose_scenario(
    yard: YardDesign,
    schedule: TruckSchedule,
    initial: InitialConditions,
    *,
    horizon=None,
    wake_mode="interval",
    wake_interval=5.0,
) -> YardScenario:
    """Validate a combination, then create one resolved simulation definition.

    Missing pile/infeed stocks default to zero. Every loader needs an explicit
    starting position. Future arrivals remain in the schedule when horizon is
    shortened; only arrivals at/before the effective horizon enter the run.
    """
    for label, entries, specs in (
        ("pile", initial.piles, yard.piles),
        ("infeed", initial.infeeds, yard.infeeds),
        ("loader", initial.loaders, yard.loaders),
    ):
        unknown = {x.id for x in entries} - {x.id for x in specs}
        if unknown:
            raise ValueError(
                f"Initial conditions reference unknown {label} IDs: {', '.join(sorted(unknown))}"
            )
    positions = {x.id: x.location for x in initial.loaders}
    missing = {x.id for x in yard.loaders} - positions.keys()
    if missing:
        raise ValueError(
            f"Initial conditions require starting positions for loaders: {', '.join(sorted(missing))}"
        )
    for label, entries, specs in (
        ("pile", initial.piles, yard.piles),
        ("infeed", initial.infeeds, yard.infeeds),
    ):
        capacities = {s.id: s.capacity for s in specs}
        for item in entries:
            if item.amount > capacities[item.id]:
                raise ValueError(
                    f"Initial {label} '{item.id}' amount {item.amount} exceeds capacity {capacities[item.id]}"
                )
    areas = {a.id for a in yard.unloading_areas}
    arrivals = []
    for a in schedule.arrivals:
        area = a.area_id
        if area is None:
            if len(areas) != 1:
                raise ValueError(
                    f"Truck '{a.id}' must specify area_id for this multi-area yard ({', '.join(sorted(areas))})"
                )
            area = next(iter(areas))
        if area not in areas:
            raise ValueError(f"Truck '{a.id}' references unknown unloading area '{area}'")
        arrivals.append(Arrival(a.time, a.quantity, a.id, area))
    return _resolved(
        yard,
        schedule.horizon if horizon is None else horizon,
        arrivals,
        {x.id: x.amount for x in initial.piles},
        {x.id: x.amount for x in initial.infeeds},
        positions,
        wake_mode=wake_mode,
        wake_interval=wake_interval,
    )
