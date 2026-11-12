"""Shared deterministic timing rules and approximate remaining job durations."""


def handling_duration(quantity, rate):
    """Return minutes for a fixed-rate pickup or deposit."""
    return quantity / rate


def travel_duration(origin, destination, speed):
    """Return straight-line travel time in minutes."""
    return origin.distance_to(destination) / speed


def route(assignment, state, context):
    """Resolve source and destination coordinates for a feasible assignment."""
    if assignment.source_kind == "truck":
        stop_id = state.trucks[assignment.source_id].stop_id
        source = next(x.location for x in context.stops if x.id == stop_id)
    else:
        source = next(x.location for x in context.piles if x.id == assignment.source_id)
    destinations = context.piles if assignment.destination_kind == "pile" else context.infeeds
    destination = next(x.location for x in destinations if x.id == assignment.destination_id)
    return source, destination


def job_duration(assignment, state, context):
    """Return travel and total minutes, excluding handling-slot contention."""
    spec = next(x for x in context.loaders if x.id == assignment.loader_id)
    source, destination = route(assignment, state, context)
    travel = travel_duration(state.loaders[spec.id].location, source, spec.speed)
    travel += travel_duration(source, destination, spec.speed)
    return travel, travel + handling_duration(
        assignment.quantity, spec.pickup_rate
    ) + handling_duration(assignment.quantity, spec.deposit_rate)


def remaining_duration(loader, state, context):
    """Estimate remaining job minutes; queued access time is not predictable here."""
    a = loader.assignment
    if a is None:
        return 0.0
    spec = next(x for x in context.loaders if x.id == loader.id)
    remaining = max(0.0, (loader.phase_completes_at or state.time) - state.time)
    deposit = handling_duration(a.quantity, spec.deposit_rate)
    if loader.phase == "deposit":
        return remaining
    if loader.phase == "waiting_deposit":
        return deposit
    if loader.phase == "travel_to_destination":
        return remaining + deposit
    source, destination = route(a, state, context)
    loaded_travel = travel_duration(source, destination, spec.speed)
    if loader.phase == "pickup":
        return remaining + loaded_travel + deposit
    pickup = handling_duration(a.quantity, spec.pickup_rate)
    if loader.phase == "waiting_pickup":
        return pickup + loaded_travel + deposit
    if loader.phase == "travel_to_source":
        return remaining + pickup + loaded_travel + deposit
    return job_duration(a, state, context)[1]
