"""Generate candidates and validate joint material reservations."""

from copy import deepcopy

from mill_yard.model.yard import Assignment, YardScenario, YardState

EPS = 1e-9


def candidates(state: YardState, scenario: YardScenario) -> list[Assignment]:
    """Return maximal currently feasible quantities for each loader and route."""
    result = []
    for loader in scenario.loaders:
        if state.loaders[loader.id].assignment is not None:
            continue
        for truck in state.trucks.values():
            if truck.stop_id is None:
                continue
            for pile in scenario.piles:
                dest = state.piles[pile.id]
                quantity = min(
                    loader.capacity,
                    truck.remaining - truck.reserved,
                    pile.capacity - dest.amount - dest.incoming,
                )
                if quantity > EPS:
                    result.append(
                        Assignment(loader.id, "truck", truck.id, "pile", pile.id, quantity)
                    )
        for pile in scenario.piles:
            source = state.piles[pile.id]
            for infeed in scenario.infeeds:
                dest = state.infeeds[infeed.id]
                quantity = min(
                    loader.capacity,
                    source.amount - source.outgoing,
                    infeed.capacity - dest.amount - dest.incoming,
                )
                if quantity > EPS:
                    result.append(
                        Assignment(loader.id, "pile", pile.id, "infeed", infeed.id, quantity)
                    )
    return result


def reserve(state: YardState, scenario: YardScenario, assignment: Assignment) -> None:
    """Validate and reserve one job in a state, allowing a smaller quantity."""
    if not any(
        a.loader_id == assignment.loader_id
        and a.source_kind == assignment.source_kind
        and a.source_id == assignment.source_id
        and a.destination_kind == assignment.destination_kind
        and a.destination_id == assignment.destination_id
        and EPS < assignment.quantity <= a.quantity + EPS
        for a in candidates(state, scenario)
    ):
        raise ValueError("Assignment is infeasible")
    state.loaders[assignment.loader_id].assignment = assignment
    if assignment.source_kind == "truck":
        state.trucks[assignment.source_id].reserved += assignment.quantity
    else:
        state.piles[assignment.source_id].outgoing += assignment.quantity
    destination = state.piles if assignment.destination_kind == "pile" else state.infeeds
    destination[assignment.destination_id].incoming += assignment.quantity


def validate_joint(state: YardState, scenario: YardScenario, assignments: list[Assignment]) -> None:
    """Validate a complete proposal without changing the live state."""
    trial = deepcopy(state)
    for assignment in assignments:
        reserve(trial, scenario, assignment)
