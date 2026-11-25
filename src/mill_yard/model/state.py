"""Mutable yard state and source-to-destination assignments."""

from dataclasses import dataclass, field
from typing import Literal

from mill_yard.model.types import Point


@dataclass
class TruckState:
    """Active truck, pickup reservations, and absolute arrival/stop timestamps.

    pickup_service_time is the union of finished pickup-service intervals; an
    interval still in progress is accounted for using busy_since.
    """

    id: str
    arrived_at: float
    remaining_quantity: float
    reserved_quantity: float = 0.0
    stop_id: str | None = None
    entered_stop_at: float | None = None
    first_pickup_at: float | None = None
    active_pickups: int = 0
    busy_since: float | None = None
    pickup_service_time: float = 0.0
    area_id: str = "main"


@dataclass
class InventoryState:
    """Inventory and commitments for a pile or infeed."""

    amount: float
    reserved_incoming: float = 0.0
    reserved_outgoing: float = 0.0
    starved: bool = False


@dataclass(frozen=True)
class Assignment:
    """One loader's complete source-to-destination job."""

    loader_id: str
    source_kind: Literal["truck", "pile"]
    source_id: str
    destination_kind: Literal["pile", "infeed"]
    destination_id: str
    quantity: float


@dataclass
class LoaderState:
    """Location, job, phase and payload, with completed-leg travel counters.

    current_leg_distance is the full length of an ongoing leg. Summary metrics
    separately add its prorated progress to produce total travel to the horizon.
    """

    id: str
    location: Point
    assignment: Assignment | None = None
    phase: Literal[
        "idle",
        "travel_to_source",
        "waiting_pickup",
        "pickup",
        "travel_to_destination",
        "waiting_deposit",
        "deposit",
    ] = "idle"
    phase_completes_at: float | None = None
    carried: float = 0.0
    completed_travel_distance: float = 0.0
    completed_travel_time: float = 0.0
    travel_started_at: float | None = None
    current_leg_distance: float = 0.0


@dataclass
class HandlingState:
    """Current owners and FIFO access queue, identified by loader ID."""

    users: list[str] = field(default_factory=list)
    queue: list[str] = field(default_factory=list)


@dataclass
class YardState:
    """Mutable simulation state; snapshot copies are stored by the recorder."""

    time: float
    trucks: dict[str, TruckState]
    queues: dict[str, list[str]]
    stops: dict[str, str | None]
    piles: dict[str, InventoryState]
    infeeds: dict[str, InventoryState]
    loaders: dict[str, LoaderState]
    consumed: dict[str, float] = field(default_factory=dict)
    starvation: dict[str, float] = field(default_factory=dict)
    arrived_quantity: float = 0.0
    next_arrival_index: int = 0
    handling: dict[str, HandlingState] = field(default_factory=dict)
    next_interval_at: float | None = None
    scheduled_crossings: list[tuple[str, float, float]] = field(default_factory=list)

    @property
    def queue(self):
        """Derived global arrival-order view; admission uses only area queues."""
        return sorted(
            (truck_id for queue in self.queues.values() for truck_id in queue),
            key=lambda key: (self.trucks[key].arrived_at, key),
        )

    @property
    def waiting_truck_count(self):
        return sum(len(queue) for queue in self.queues.values())
