"""Loader travel, pickup and deposition processes."""

from dataclasses import asdict

from mill_yard.model.feasibility import EPS
from mill_yard.model.timing import handling_duration, travel_duration


class LoaderProcesses:
    """Execute jobs through engine-owned state, resources and event callbacks."""

    def __init__(self, engine):
        self.engine = engine

    def travel(self, loader, spec, destination, phase):
        distance = loader.location.distance_to(destination)
        duration = travel_duration(loader.location, destination, spec.speed)
        loader.phase = phase
        loader.phase_completes_at = self.engine.env.now + duration
        loader.travel_started_at = self.engine.env.now
        loader.current_leg_distance = distance
        self.engine._emit(
            "travel_start", loader=loader.id, phase=phase, distance=distance, duration=duration
        )
        yield self.engine.env.timeout(duration)
        self.engine._advance()
        loader.completed_travel_distance += distance
        loader.completed_travel_time += duration
        loader.location = destination
        loader.phase_completes_at = None
        loader.travel_started_at = None
        loader.current_leg_distance = 0.0
        self.engine._emit("travel_complete", loader=loader.id, phase=phase, distance=distance)

    def job(self, a):
        loader = self.engine.state.loaders[a.loader_id]
        spec = next(x for x in self.engine.scenario.loaders if x.id == a.loader_id)
        if a.source_kind == "truck":
            truck = self.engine.state.trucks[a.source_id]
            source_id = truck.stop_id
            source = next(x for x in self.engine.scenario.stops if x.id == source_id)
            source_kind = "stop"
        else:
            source = next(x for x in self.engine.scenario.piles if x.id == a.source_id)
            source_id, source_kind = source.id, "pile"
        destination = next(
            x
            for x in (
                self.engine.scenario.piles
                if a.destination_kind == "pile"
                else self.engine.scenario.infeeds
            )
            if x.id == a.destination_id
        )
        yield from self.travel(loader, spec, source.location, "travel_to_source")
        loader.phase = "waiting_pickup"
        self.engine._emit("pickup_wait", loader=a.loader_id, source=a.source_id)
        with self.engine.resources[(source_kind, source_id)].request() as request:
            request.loader_id = a.loader_id
            self.engine._emit("pickup_queued", loader=a.loader_id)
            yield request
            self.engine._advance()
            loader.phase = "pickup"
            loader.phase_completes_at = self.engine.env.now + handling_duration(
                a.quantity, spec.pickup_rate
            )
            if a.source_kind == "truck":
                truck = self.engine.state.trucks[a.source_id]
                if truck.first_pickup_at is None:
                    truck.first_pickup_at = self.engine.env.now
                if truck.active_pickups == 0:
                    truck.busy_since = self.engine.env.now
                truck.active_pickups += 1
            self.engine._emit(
                "pickup_start", loader=a.loader_id, source=a.source_id, quantity=a.quantity
            )
            yield self.engine.env.timeout(handling_duration(a.quantity, spec.pickup_rate))
            self.engine._advance()
            if a.source_kind == "truck":
                truck = self.engine.state.trucks[a.source_id]
                truck.remaining_quantity -= a.quantity
                truck.reserved_quantity -= a.quantity
                truck.active_pickups -= 1
                if truck.active_pickups == 0:
                    truck.pickup_service_time += self.engine.env.now - truck.busy_since
                    truck.busy_since = None
            else:
                inv = self.engine.state.piles[a.source_id]
                inv.amount -= a.quantity
                inv.reserved_outgoing -= a.quantity
            loader.carried = a.quantity
            loader.phase_completes_at = None
            self.engine._emit(
                "pickup_complete", loader=a.loader_id, source=a.source_id, quantity=a.quantity
            )
            if a.source_kind == "truck" and truck.remaining_quantity <= EPS:
                self.engine.state.stops[truck.stop_id] = None
                self.engine.completed_trucks.append(
                    {
                        **asdict(truck),
                        "completed_at": self.engine.env.now,
                        "queue_wait": truck.entered_stop_at - truck.arrived_at,
                        "total_time": self.engine.env.now - truck.arrived_at,
                        "waiting_time": self.engine.env.now
                        - truck.arrived_at
                        - truck.pickup_service_time,
                    }
                )
                del self.engine.state.trucks[truck.id]
                self.engine._emit(
                    "truck_departure", truck=truck.id, stop=truck.stop_id, area_id=truck.area_id
                )
            self.engine._wake()
        yield from self.travel(loader, spec, destination.location, "travel_to_destination")
        loader.phase = "waiting_deposit"
        self.engine._emit("deposit_wait", loader=a.loader_id, destination=a.destination_id)
        with self.engine.resources[(a.destination_kind, a.destination_id)].request() as request:
            request.loader_id = a.loader_id
            self.engine._emit("deposit_queued", loader=a.loader_id)
            yield request
            self.engine._advance()
            loader.phase = "deposit"
            loader.phase_completes_at = self.engine.env.now + handling_duration(
                a.quantity, spec.deposit_rate
            )
            self.engine._emit(
                "deposit_start",
                loader=a.loader_id,
                destination=a.destination_id,
                quantity=a.quantity,
            )
            yield self.engine.env.timeout(handling_duration(a.quantity, spec.deposit_rate))
            self.engine._advance()
            inv = (
                self.engine.state.piles
                if a.destination_kind == "pile"
                else self.engine.state.infeeds
            )[a.destination_id]
            inv.reserved_incoming -= a.quantity
            inv.amount += a.quantity
            loader.carried = 0
            loader.phase = "idle"
            loader.phase_completes_at = None
            loader.assignment = None
            self.engine._emit(
                "deposit_complete",
                loader=a.loader_id,
                destination=a.destination_id,
                quantity=a.quantity,
            )
            self.engine._wake()
