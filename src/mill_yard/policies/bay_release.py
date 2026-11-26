"""Marginal bay-admission opportunities within a fixed observation horizon.

This is a bounded forecast, not a rollout: queued trucks occupy admitted bays
for the remainder of the window. Future dispatches/arrivals and access waits
are not predicted. Only observed and selected pickups can release a bay.
"""

from math import fsum, inf

from mill_yard.model.feasibility import EPS
from mill_yard.model.timing import handling_duration, travel_duration


class BayReleaseForecast:
    """Reuse one original observation while comparing complete assignment sets."""

    def __init__(self, state, context, parameters):
        self.state, self.context = state, context
        self.window = max(0.0, min(parameters.bay_time_reference, context.horizon - state.time))
        self.scale = parameters.bay_time_reference * parameters.bay_queue_reference
        self.areas = {
            area.id: [s for s in context.stops if s.area_id == area.id]
            for area in context.unloading_areas
            if state.queues[area.id]
        }
        self.committed = {}
        for loader in state.loaders.values():
            a = loader.assignment
            if (
                a is None
                or a.source_kind != "truck"
                or loader.carried > EPS
                or a.source_id not in state.trucks
                or loader.phase in ("travel_to_destination", "waiting_deposit", "deposit")
            ):
                continue
            spec = next(s for s in context.loaders if s.id == loader.id)
            if loader.phase == "pickup":
                end = max(0.0, (loader.phase_completes_at or state.time) - state.time)
            elif loader.phase == "waiting_pickup":
                end = handling_duration(a.quantity, spec.pickup_rate)
            elif loader.phase == "travel_to_source":
                end = max(0.0, (loader.phase_completes_at or state.time) - state.time)
                end += handling_duration(a.quantity, spec.pickup_rate)
            else:
                end = self.pickup_time(a)
            self.committed.setdefault(a.source_id, []).append((a.quantity, end))
        self.baseline = self.admissions({})

    def pickup_time(self, assignment):
        """Time until a newly selected pickup finishes, excluding access waits."""
        spec = next(s for s in self.context.loaders if s.id == assignment.loader_id)
        truck = self.state.trucks[assignment.source_id]
        stop = next(s for s in self.context.stops if s.id == truck.stop_id)
        return travel_duration(
            self.state.loaders[spec.id].location, stop.location, spec.speed
        ) + handling_duration(assignment.quantity, spec.pickup_rate)

    def admissions(self, selected):
        """First admissions to each bay; no assumed service of queued trucks."""
        result = {}
        for area_id, stops in self.areas.items():
            releases = []
            for stop in stops:
                truck_id = self.state.stops[stop.id]
                if truck_id is None:
                    releases.append(0.0)
                    continue
                truck = self.state.trucks[truck_id]
                jobs = self.committed.get(truck_id, []) + selected.get(truck_id, [])
                release = inf
                if jobs and fsum(q for q, _ in jobs) >= truck.remaining_quantity - EPS:
                    release = max(t for _, t in jobs)
                releases.append(release)
            result[area_id] = [
                min(self.window, t) for t in sorted(releases)[: len(self.state.queues[area_id])]
            ]
        return result

    def benefit(self, assignments):
        """Credit admission wait saved once per area; pair values lie in [0, 1]."""
        if not self.areas or self.window <= 0:
            return 0.0
        selected = {}
        for a in assignments:
            if a.source_kind == "truck":
                selected.setdefault(a.source_id, []).append((a.quantity, self.pickup_time(a)))
        after = self.admissions(selected)
        return fsum(
            min(
                1.0,
                max(0.0, fsum(self.baseline[area]) - fsum(after[area])) / self.scale,
            )
            for area in self.areas
        )
