"""Reusable accounting and occupancy checks for simulation transitions."""

from math import isclose, isfinite


def check_invariants(state, scenario, tolerance=1e-7):
    """Raise AssertionError for invalid material, reservations or occupancy."""

    def nonnegative(value):
        assert isfinite(value), f"Non-finite quantity: {value}"
        assert value >= -tolerance, f"Negative quantity: {value}"

    material = sum(t.remaining_quantity for t in state.trucks.values())
    material += sum(i.amount for i in (*state.piles.values(), *state.infeeds.values()))
    material += sum(loader.carried for loader in state.loaders.values()) + sum(
        state.consumed.values()
    )
    expected = sum(x.initial for x in (*scenario.piles, *scenario.infeeds)) + state.arrived_quantity
    assert isclose(material, expected, rel_tol=1e-9, abs_tol=tolerance), (
        "Material conservation failed"
    )
    for specs, inventories in ((scenario.piles, state.piles), (scenario.infeeds, state.infeeds)):
        for spec in specs:
            inv = inventories[spec.id]
            for value in (inv.amount, inv.reserved_incoming, inv.reserved_outgoing):
                nonnegative(value)
            assert inv.amount + inv.reserved_incoming <= spec.capacity + tolerance, (
                "Capacity exceeded"
            )
            assert inv.reserved_outgoing <= inv.amount + tolerance, (
                "Reserved source exceeds inventory"
            )
    for truck in state.trucks.values():
        nonnegative(truck.remaining_quantity)
        nonnegative(truck.reserved_quantity)
        assert truck.reserved_quantity <= truck.remaining_quantity + tolerance, (
            "Truck over-reserved"
        )
    for spec in scenario.loaders:
        loader = state.loaders[spec.id]
        nonnegative(loader.carried)
        assert loader.carried <= spec.capacity + tolerance, "Payload capacity exceeded"
        assert loader.assignment is not None or loader.carried <= tolerance, "Unassigned payload"
    assert set(state.queues) == {a.id for a in scenario.unloading_areas}, "Queue area mismatch"
    all_queued = [key for queue in state.queues.values() for key in queue]
    queued = set(all_queued)
    assert len(queued) == len(all_queued), "Duplicate queued truck"
    for area_id, queue in state.queues.items():
        assert all(state.trucks[key].area_id == area_id for key in queue), (
            "Truck in wrong area queue"
        )
        assert queue == sorted(queue, key=lambda key: (state.trucks[key].arrived_at, key)), (
            "Queue FIFO order violated"
        )
    occupied = [truck for truck in state.stops.values() if truck is not None]
    assert len(set(occupied)) == len(occupied), "Truck occupies multiple stops"
    assert queued.isdisjoint(occupied), "Stopped truck remains queued"
    assert queued | set(occupied) == set(state.trucks), "Truck lost from queue or stops"
    for stop, truck_id in state.stops.items():
        if truck_id is not None:
            assert state.trucks[truck_id].stop_id == stop, "Stop occupancy mismatch"
            spec = next(s for s in scenario.stops if s.id == stop)
            assert state.trucks[truck_id].area_id == spec.area_id, "Truck at wrong area's stop"
    for kind, specs, inventories in (
        ("pile", scenario.piles, state.piles),
        ("infeed", scenario.infeeds, state.infeeds),
    ):
        for spec in specs:
            incoming = sum(
                loader.assignment.quantity
                for loader in state.loaders.values()
                if loader.assignment is not None
                and loader.assignment.destination_kind == kind
                and loader.assignment.destination_id == spec.id
            )
            assert isclose(incoming, inventories[spec.id].reserved_incoming, abs_tol=tolerance), (
                "Destination reservation mismatch"
            )
    for kind, sources in (("truck", state.trucks), ("pile", state.piles)):
        for source_id, source in sources.items():
            outgoing = sum(
                loader.assignment.quantity
                for loader in state.loaders.values()
                if loader.assignment is not None
                and loader.carried <= tolerance
                and loader.assignment.source_kind == kind
                and loader.assignment.source_id == source_id
            )
            recorded = source.reserved_quantity if kind == "truck" else source.reserved_outgoing
            assert isclose(outgoing, recorded, abs_tol=tolerance), "Source reservation mismatch"
