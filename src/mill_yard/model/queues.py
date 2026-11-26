"""Area queue workload, distinct from bay waiting lists and loader access queues."""

from mill_yard.model.feasibility import EPS


def unassigned_trucks(state, area_id=None):
    """Include stopped/queued trucks with wood left to allocate; count each once."""
    return [
        t
        for t in state.trucks.values()
        if (area_id is None or t.area_id == area_id)
        and t.remaining_quantity - t.reserved_quantity > EPS
    ]


def queue_work_counts(state):
    counts = dict.fromkeys(state.queues, 0)
    for truck in unassigned_trucks(state):
        counts[truck.area_id] += 1
    return counts


def truck_wait_time(truck, now):
    """Accumulated nonservice wait, excluding ongoing pickup service once."""
    service = truck.pickup_service_time + (
        now - truck.busy_since if truck.busy_since is not None else 0
    )
    return max(0.0, now - truck.arrived_at - service)


def area_max_wait(state, area_id):
    """Longest nonservice wait among queued/stopped trucks with unreserved work."""
    return max(
        (truck_wait_time(t, state.time) for t in unassigned_trucks(state, area_id)),
        default=0.0,
    )


def area_wait_key(area_id, state):
    """Longest accumulated non-service wait, then oldest arrival, then area ID."""
    trucks = unassigned_trucks(state, area_id)
    waits = [truck_wait_time(t, state.time) for t in trucks]
    return (
        -max(waits, default=0),
        min((t.arrived_at for t in trucks), default=float("inf")),
        area_id,
    )
