"""Proposal-based features with versioned queue and fairness extensions.

All quantities are tonnes and all times are minutes. Feature values are bounded
in [0, 1]. See docs/feature_scored_policy.md for equations and deliberate fixes.
"""

from dataclasses import dataclass, fields
from math import fsum, inf, isclose, isfinite

from mill_yard.model.queues import area_max_wait, queue_work_counts, truck_wait_time
from mill_yard.model.timing import job_duration, remaining_duration, route
from mill_yard.policies.bay_release import BayReleaseForecast
from mill_yard.policies.scored import predicted_starvation


def clip(value):
    """Clamp a normalized feature to its declared range."""
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class FeatureWeights:
    """Named simplex weights; bay release defaults to zero for four-weight runs."""

    truck: float = 0.25
    mill: float = 0.40
    inventory: float = 0.15
    cost: float = 0.20
    bay_release: float = 0.0

    def __post_init__(self):
        values = [getattr(self, f.name) for f in fields(self)]
        if any(not isfinite(v) or v < 0 for v in values):
            raise ValueError("Feature weights must be finite and nonnegative")
        if not isclose(sum(values), 1.0, rel_tol=0, abs_tol=1e-9):
            raise ValueError("Feature weights must sum to one")


@dataclass(frozen=True)
class FeatureParameters:
    """Fixed feature scales and cost assumptions, separate from policy weights.

    Safety stock and demand coverage are yard totals allocated to piles by their
    capacity shares. Monetary coefficients are illustrative currency units.
    In version 4, truck_area_wait_share allocates area urgency first; the
    remaining share uses truck_wait_share to mix individual wait and backlog.
    """

    wait_reference: float = 30.0
    queue_reference: float = 5.0
    mill_time_reference: float = 30.0
    inventory_reference: float = 20.0
    cost_reference: float = 30.0
    truck_wait_share: float = 0.75
    mill_stockout_share: float = 0.75
    pile_lead_time: float = 30.0
    yard_safety_stock: float = 20.0
    residence_time: float = 60.0
    distance_cost: float = 0.01
    active_time_cost: float = 1.0
    holding_cost: float = 0.001
    bay_time_reference: float = 30.0
    bay_queue_reference: float = 5.0
    truck_area_wait_share: float = 0.20
    area_wait_reference: float = 30.0

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{f.name} must be finite and nonnegative")
            if f.name.endswith("_reference") and value <= 0:
                raise ValueError(f"{f.name} must be positive")
        if any(
            share > 1
            for share in (
                self.truck_wait_share,
                self.mill_stockout_share,
                self.truck_area_wait_share,
            )
        ):
            raise ValueError("Feature mixing shares must lie in [0, 1]")


@dataclass(frozen=True)
class DispatchFeatures:
    """Interpretable values for one feasible loader-task pair."""

    truck: float
    mill: float
    inventory: float
    cost: float
    bay_release: float = 0.0

    def __post_init__(self):
        if any(
            not isfinite(getattr(self, f.name)) or not 0 <= getattr(self, f.name) <= 1
            for f in fields(self)
        ):
            raise ValueError("Dispatch features must be finite and lie in [0, 1]")

    def score(self, weights):
        """Apply equation 46; cost has a negative sign."""
        return (
            weights.truck * self.truck
            + weights.mill * self.mill
            + weights.inventory * self.inventory
            - weights.cost * self.cost
            + weights.bay_release * self.bay_release
        )

    def as_vector(self):
        """Return T, M, I, C, R; cost remains positive and R is appended."""
        return self.truck, self.mill, self.inventory, self.cost, self.bay_release


def committed_deliveries(state, context, infeed_id):
    """Estimate infeed arrival times for both running and newly reserved jobs."""
    return [
        (remaining_duration(loader, state, context), loader.assignment.quantity)
        for loader in state.loaders.values()
        if loader.assignment is not None
        and loader.assignment.destination_kind == "infeed"
        and loader.assignment.destination_id == infeed_id
    ]


def time_to_stockout(inventory, rate, deliveries):
    """First depletion with constant demand and discrete committed arrivals.

    A delivery exactly at depletion prevents a positive starvation interval,
    matching the engine's same-time settlement rule. Already empty stock has
    stockout time zero unless a delivery is available immediately. No demand
    means infinite stockout time, even for an empty infeed.
    """
    if rate <= 0:
        return inf
    previous, amount = 0.0, inventory
    for time, quantity in sorted(deliveries):
        time = max(previous, time)
        if previous + amount / rate < time:
            return previous + amount / rate
        amount = max(0.0, amount - rate * (time - previous)) + quantity
        previous = time
    return previous + amount / rate


def pile_target(spec, context, parameters):
    """Allocate homogeneous yard demand/safety stock without duplicating coverage."""
    share = spec.capacity / sum(p.capacity for p in context.piles)
    demand = sum(i.consumption_rate for i in context.infeeds)
    return min(
        spec.capacity, share * (demand * parameters.pile_lead_time + parameters.yard_safety_stock)
    )


def extract_features(
    assignment, state, context, parameters=None, *, feature_version=4, include_bay_release=True
):
    """Score a feasible pair's observed state, never looking at future arrivals."""
    p = parameters or FeatureParameters()
    a = assignment
    truck_feature = 0.0
    if a.source_kind == "truck":
        truck = state.trucks[a.source_id]
        wait = truck_wait_time(truck, state.time)
        if feature_version == 2:
            wait_value = clip(wait / p.wait_reference)
            pressure = clip(state.waiting_truck_count / p.queue_reference)
        else:
            # Smooth normalization preserves distinctions above the old cutoffs.
            wait_value = wait / (wait + p.wait_reference)
            backlog = queue_work_counts(state)[truck.area_id]
            pressure = backlog / (backlog + p.queue_reference)
        truck_feature = p.truck_wait_share * wait_value + (1 - p.truck_wait_share) * pressure
        if feature_version == 4:
            longest = area_max_wait(state, truck.area_id)
            area_value = longest / (longest + p.area_wait_reference)
            truck_feature = (
                1 - p.truck_area_wait_share
            ) * truck_feature + p.truck_area_wait_share * area_value
        truck_feature *= min(1.0, a.quantity / truck.remaining_quantity)

    _, duration = job_duration(a, state, context)
    source, destination = route(a, state, context)
    distance = state.loaders[a.loader_id].location.distance_to(source) + source.distance_to(
        destination
    )
    mill_feature = 0.0
    if a.destination_kind == "infeed":
        infeed = next(i for i in context.infeeds if i.id == a.destination_id)
        rate = infeed.consumption_rate
        inventory = state.infeeds[infeed.id].amount
        deliveries = committed_deliveries(state, context, infeed.id)
        stockout = time_to_stockout(inventory, rate, deliveries)
        urgency = clip(1 - stockout / p.mill_time_reference)
        lateness = clip((duration - stockout) / p.mill_time_reference)
        window = max(0.0, min(p.mill_time_reference, context.horizon - state.time))
        before = predicted_starvation(inventory, rate, deliveries, window)
        after = predicted_starvation(inventory, rate, [*deliveries, (duration, a.quantity)], window)
        benefit = clip((before - after) / p.mill_time_reference)
        mill_feature = benefit * (
            p.mill_stockout_share * urgency + (1 - p.mill_stockout_share) * (1 - lateness)
        )

    inventory_feature = 0.0
    if a.destination_kind == "pile" or a.source_kind == "pile":
        pile_id = a.destination_id if a.destination_kind == "pile" else a.source_id
        pile = next(s for s in context.piles if s.id == pile_id)
        inv = state.piles[pile_id]
        projected = inv.amount + inv.reserved_incoming - inv.reserved_outgoing
        target = pile_target(pile, context, p)
        delta = a.quantity if a.destination_kind == "pile" else -a.quantity
        inventory_feature = clip(
            (abs(projected - target) - abs(projected + delta - target)) / p.inventory_reference
        )
    holding = (
        p.holding_cost * a.quantity * p.residence_time if a.destination_kind == "pile" else 0.0
    )
    cost = p.distance_cost * distance + p.active_time_cost * duration + holding
    return DispatchFeatures(
        clip(truck_feature),
        clip(mill_feature),
        inventory_feature,
        clip(cost / p.cost_reference),
        BayReleaseForecast(state, context, p).benefit([a])
        if feature_version in (3, 4) and include_bay_release
        else 0.0,
    )


def aggregate_features(
    assignments,
    state,
    context,
    parameters=None,
    *,
    feature_version=4,
    pair_features=None,
    bay_forecast=None,
):
    """Score a whole feasible set against one original observation.

    Pair features remain normalized; joint contributions sum over trucks, piles,
    and infeeds and can exceed one. Delivery benefits are credited chronologically,
    grouping simultaneous arrivals, rather than in loader-assignment order.
    """
    p = parameters or FeatureParameters()
    pairs = [
        pair_features(a)
        if pair_features is not None
        else extract_features(
            a, state, context, p, feature_version=feature_version, include_bay_release=False
        )
        for a in assignments
    ]
    changes = _pile_changes(assignments, context)
    plan = tuple(
        (a.destination_id, job_duration(a, state, context)[1], a.quantity)
        for a in assignments
        if a.destination_kind == "infeed"
    )
    return {
        "truck": fsum(pair.truck for pair in pairs),
        "mill": _mill_total(plan, state, context, p),
        "inventory": _inventory_total(changes, state, context, p),
        "cost": fsum(pair.cost for pair in pairs),
        "bay_release": (bay_forecast or BayReleaseForecast(state, context, p)).benefit(assignments)
        if feature_version in (3, 4)
        else 0.0,
    }


def _pile_changes(assignments, context):
    return tuple(
        fsum(
            (a.quantity if a.destination_kind == "pile" and a.destination_id == pile.id else 0)
            - (a.quantity if a.source_kind == "pile" and a.source_id == pile.id else 0)
            for a in assignments
        )
        for pile in context.piles
    )


def _inventory_total(changes, state, context, p):
    inventory_terms = []
    for pile, delta in zip(context.piles, changes, strict=True):
        inv = state.piles[pile.id]
        projected = inv.amount + inv.reserved_incoming - inv.reserved_outgoing
        target = pile_target(pile, context, p)
        inventory_terms.append(
            clip(
                (abs(projected - target) - abs(projected + delta - target)) / p.inventory_reference
            )
        )
    return fsum(inventory_terms)


def _mill_total(plan, state, context, p):
    mill_terms = []
    window = max(0.0, min(p.mill_time_reference, context.horizon - state.time))
    for infeed in context.infeeds:
        deliveries = committed_deliveries(state, context, infeed.id)
        inventory, rate = state.infeeds[infeed.id].amount, infeed.consumption_rate
        stockout = time_to_stockout(inventory, rate, deliveries)
        urgency = clip(1 - stockout / p.mill_time_reference)
        before = predicted_starvation(inventory, rate, deliveries, window)
        groups = {}
        for infeed_id, duration, quantity in plan:
            if infeed_id == infeed.id:
                groups.setdefault(duration, []).append(quantity)
        for duration in sorted(groups):
            deliveries.append((duration, fsum(groups[duration])))
            after = predicted_starvation(inventory, rate, deliveries, window)
            benefit = clip((before - after) / p.mill_time_reference)
            lateness = clip((duration - stockout) / p.mill_time_reference)
            mill_terms.append(
                benefit
                * (p.mill_stockout_share * urgency + (1 - p.mill_stockout_share) * (1 - lateness))
            )
            before = after
    return fsum(mill_terms)


class ObservationFeatures:
    """Decision-local caches; never reuse predictions across state transitions."""

    def __init__(self, state, context, parameters, feature_version):
        self.state, self.context = state, context
        self.parameters, self.feature_version = parameters, feature_version
        self.pairs = {}
        self.durations, self.inventories, self.mills, self.releases = {}, {}, {}, {}
        self.bay = (
            BayReleaseForecast(state, context, parameters) if feature_version in (3, 4) else None
        )

    def pair(self, assignment):
        if assignment not in self.pairs:
            self.pairs[assignment] = extract_features(
                assignment,
                self.state,
                self.context,
                self.parameters,
                feature_version=self.feature_version,
                include_bay_release=False,
            )
        return self.pairs[assignment]

    def joint(self, assignments):
        pairs = [self.pair(a) for a in assignments]
        changes = _pile_changes(assignments, self.context)
        if changes not in self.inventories:
            self.inventories[changes] = _inventory_total(
                changes, self.state, self.context, self.parameters
            )
        plan = []
        for a in assignments:
            if a.destination_kind == "infeed":
                if a not in self.durations:
                    self.durations[a] = job_duration(a, self.state, self.context)[1]
                plan.append((a.destination_id, self.durations[a], a.quantity))
        plan = tuple(sorted(plan))
        if plan not in self.mills:
            self.mills[plan] = _mill_total(plan, self.state, self.context, self.parameters)
        # Deposit destinations do not affect a truck's pickup completion.
        pickups = tuple(
            sorted(
                (a.loader_id, a.source_id, a.quantity)
                for a in assignments
                if a.source_kind == "truck"
            )
        )
        if pickups not in self.releases:
            self.releases[pickups] = self.bay.benefit(assignments) if self.bay is not None else 0.0
        return {
            "truck": fsum(pair.truck for pair in pairs),
            "mill": self.mills[plan],
            "inventory": self.inventories[changes],
            "cost": fsum(pair.cost for pair in pairs),
            "bay_release": self.releases[pickups],
        }
