"""Pure performance metrics derived from completed and unfinished yard work."""

from dataclasses import asdict
from math import floor

from mill_yard.model.queues import queue_work_counts
from mill_yard.model.timing import remaining_duration


def summary(result):
    """Build machine-readable metrics, including unfinished work."""
    state = result.state
    loaders = {}
    for key, loader in state.loaders.items():
        data = asdict(loader)
        data["total_travel_distance"] = loader.completed_travel_distance
        data["total_travel_time"] = loader.completed_travel_time
        if loader.travel_started_at is not None and loader.phase_completes_at is not None:
            duration = loader.phase_completes_at - loader.travel_started_at
            fraction = (
                min(1.0, max(0.0, (state.time - loader.travel_started_at) / duration))
                if duration
                else 1.0
            )
            data["total_travel_distance"] += loader.current_leg_distance * fraction
            data["total_travel_time"] += duration * fraction
        loaders[key] = data
    unfinished = []
    for truck in state.trucks.values():
        data = asdict(truck)
        handling = truck.pickup_service_time + (
            state.time - truck.busy_since if truck.busy_since is not None else 0
        )
        data["time_in_yard"] = state.time - truck.arrived_at
        data["waiting_time_so_far"] = state.time - truck.arrived_at - handling
        unfinished.append(data)

    def tails(waits):
        values = sorted(waits)
        if not values:
            return {"truck_wait_so_far_max": 0.0, "truck_wait_so_far_p95": 0.0}
        index = (len(values) - 1) * 0.95
        low = floor(index)
        percentile = values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (
            index - low
        )
        return {"truck_wait_so_far_max": values[-1], "truck_wait_so_far_p95": percentile}

    counts = queue_work_counts(state)
    areas = {}
    for area in result.scenario.unloading_areas:
        completed = [t for t in result.completed_trucks if t.get("area_id", "main") == area.id]
        present = [t for t in unfinished if t["area_id"] == area.id]
        waits = [t["waiting_time"] for t in completed] + [t["waiting_time_so_far"] for t in present]
        areas[area.id] = {
            "waiting_for_bay": len(state.queues[area.id]),
            "unassigned_trucks": counts[area.id],
            "stopped_trucks": sum(t["stop_id"] is not None for t in present),
            "completed_trucks": len(completed),
            "unfinished_trucks": len(present),
            "truck_wait_total": sum(waits),
            **tails(waits),
        }
    return {
        "time": state.time,
        "completed_trucks": result.completed_trucks,
        "unfinished_trucks": unfinished,
        "completed_truck_wait_total": sum(t["waiting_time"] for t in result.completed_trucks),
        "unfinished_truck_wait_so_far": sum(t["waiting_time_so_far"] for t in unfinished),
        "unfinished_truck_quantity": sum(t.remaining_quantity for t in state.trucks.values()),
        "onboard_quantity": sum(loader.carried for loader in state.loaders.values()),
        "active_loader_jobs": sum(
            loader.assignment is not None for loader in state.loaders.values()
        ),
        "estimated_outstanding_loader_minutes": sum(
            remaining_duration(loader, state, result.scenario) for loader in state.loaders.values()
        ),
        "incomplete_job_estimates_exclude_access_wait": True,
        "loader_travel_distance_total": sum(x["total_travel_distance"] for x in loaders.values()),
        "loader_travel_time_total": sum(x["total_travel_time"] for x in loaders.values()),
        "queue": state.queue,
        "queues": {key: list(queue) for key, queue in state.queues.items()},
        "unloading_areas": areas,
        **tails(
            [t["waiting_time"] for t in result.completed_trucks]
            + [t["waiting_time_so_far"] for t in unfinished]
        ),
        "consumed": state.consumed,
        "starvation": state.starvation,
        "loaders": loaders,
        "piles": {key: asdict(value) for key, value in state.piles.items()},
        "infeeds": {key: asdict(value) for key, value in state.infeeds.items()},
    }
