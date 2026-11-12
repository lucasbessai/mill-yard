"""Pure drawing coordinates and interpolated views of unchanged model state."""

from dataclasses import dataclass

from mill_yard.model.types import Point


@dataclass(frozen=True)
class Viewport:
    """Map world coordinates to a fitted canvas with equal axis scales."""

    min_x: float
    min_y: float
    scale: float
    offset_x: float
    offset_y: float
    height: float

    @classmethod
    def fit(cls, scenario, width, height, margin=80):
        points = [
            x.location
            for x in (*scenario.stops, *scenario.piles, *scenario.infeeds, *scenario.loaders)
        ]
        points += [area.queue_location for area in scenario.unloading_areas]
        min_x, max_x = min(p.x for p in points), max(p.x for p in points)
        min_y, max_y = min(p.y for p in points), max(p.y for p in points)
        dx, dy = max(20, max_x - min_x), max(20, max_y - min_y)
        scale = max(0.01, min(max(1, width - 2 * margin) / dx, max(1, height - 2 * margin) / dy))
        return cls(
            min_x,
            min_y,
            scale,
            (width - (max_x - min_x) * scale) / 2,
            (height - (max_y - min_y) * scale) / 2,
            height,
        )

    def point(self, point):
        """Use mathematical world axes, with positive y upward."""
        return (
            self.offset_x + (point.x - self.min_x) * self.scale,
            self.height - self.offset_y - (point.y - self.min_y) * self.scale,
        )


def loader_position(loader, state, scenario, display_time):
    """Interpolate travel for rendering only; never change the engine state."""
    a = loader.assignment
    if (
        a is None
        or loader.phase not in ("travel_to_source", "travel_to_destination")
        or loader.travel_started_at is None
        or loader.phase_completes_at is None
    ):
        return loader.location
    if loader.phase == "travel_to_source":
        if a.source_kind == "truck":
            stop_id = state.trucks[a.source_id].stop_id
            destination = next(x.location for x in scenario.stops if x.id == stop_id)
        else:
            destination = next(x.location for x in scenario.piles if x.id == a.source_id)
    else:
        specs = scenario.piles if a.destination_kind == "pile" else scenario.infeeds
        destination = next(x.location for x in specs if x.id == a.destination_id)
    duration = loader.phase_completes_at - loader.travel_started_at
    fraction = (
        min(1.0, max(0.0, (display_time - loader.travel_started_at) / duration))
        if duration
        else 1.0
    )
    return Point(
        loader.location.x + (destination.x - loader.location.x) * fraction,
        loader.location.y + (destination.y - loader.location.y) * fraction,
    )


def infeed_amount(inventory, rate, state_time, display_time):
    """Project consumption between events strictly for the on-screen view."""
    return max(0.0, inventory.amount - rate * max(0.0, display_time - state_time))


def truck_wait(truck, display_time):
    """Cumulative non-service time, including waiting after partial pickups."""
    service = truck.pickup_service_time
    if truck.busy_since is not None:
        service += max(0.0, display_time - truck.busy_since)
    return max(0.0, display_time - truck.arrived_at - service)


def mill_starvation(state, spec, display_time):
    """Cumulative starvation for one mill, including time since the last event."""
    elapsed = max(0.0, display_time - state.time)
    additional = (
        max(0.0, elapsed - state.infeeds[spec.id].amount / spec.consumption_rate)
        if spec.consumption_rate > 0
        else 0.0
    )
    return state.starvation.get(spec.id, 0.0) + additional


def loader_travel(loader, display_time):
    """Distance and minutes travelled, including a partially completed leg."""
    distance, minutes = loader.completed_travel_distance, loader.completed_travel_time
    if loader.travel_started_at is not None and loader.phase_completes_at is not None:
        duration = loader.phase_completes_at - loader.travel_started_at
        progress = min(duration, max(0.0, display_time - loader.travel_started_at))
        distance += loader.current_leg_distance * progress / duration if duration else 0.0
        minutes += progress
    return distance, minutes


def live_metrics(simulation, display_time):
    """Project cumulative counters to display time without mutating the model."""
    state = simulation.state
    travel = [loader_travel(loader, display_time) for loader in state.loaders.values()]
    return {
        "truck_wait": sum(t["waiting_time"] for t in simulation.completed_trucks)
        + sum(truck_wait(t, display_time) for t in state.trucks.values()),
        "travel_distance": sum(distance for distance, _ in travel),
        "travel_time": sum(minutes for _, minutes in travel),
        "starvation": sum(
            mill_starvation(state, spec, display_time) for spec in simulation.scenario.infeeds
        ),
    }


def metric_breakdown(simulation, display_time):
    """Detached per-asset counters, using exactly the same projection as totals."""
    state = simulation.state
    trucks = [
        {
            "id": t["id"],
            "area_id": t.get("area_id", "main"),
            "status": "completed",
            "waiting_time": t["waiting_time"],
        }
        for t in simulation.completed_trucks
    ]
    trucks.extend(
        {
            "id": t.id,
            "area_id": t.area_id,
            "status": "unloading"
            if t.active_pickups
            else "queued"
            if t.stop_id is None
            else "at stop",
            "waiting_time": truck_wait(t, display_time),
        }
        for t in state.trucks.values()
    )
    loaders = []
    for loader in state.loaders.values():
        distance, minutes = loader_travel(loader, display_time)
        loaders.append(
            {
                "id": loader.id,
                "phase": loader.phase,
                "travel_distance": distance,
                "travel_time": minutes,
            }
        )
    mills = []
    for spec in simulation.scenario.infeeds:
        amount = infeed_amount(
            state.infeeds[spec.id], spec.consumption_rate, state.time, display_time
        )
        status = (
            "inactive" if not spec.consumption_rate else "starved" if amount <= 1e-9 else "running"
        )
        mills.append(
            {
                "id": spec.id,
                "status": status,
                "starvation": mill_starvation(state, spec, display_time),
            }
        )
    return {"trucks": trucks, "loaders": loaders, "mills": mills}
