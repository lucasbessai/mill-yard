"""Operator-style FIFO dispatch with infeed and full-pile priorities."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from math import isfinite

from mill_yard.model.feasibility import EPS, candidates, reserve
from mill_yard.model.timing import route
from mill_yard.policies.truck_dispatch import FifoTruckRule


@dataclass(frozen=True)
class FifoParameters:
    """Fixed rules for the operator baseline, separate from optimized weights."""

    low_fraction: float = 0.25
    target_fraction: float = 0.65
    max_refill_fraction: float = 0.75
    buffer_cycles: float = 2.0
    full_pile_fraction: float = 1.0

    def __post_init__(self):
        for item in fields(self):
            if not isfinite(getattr(self, item.name)):
                raise ValueError(f"{item.name} must be finite")
        if not 0 <= self.low_fraction < self.target_fraction <= self.max_refill_fraction <= 1:
            raise ValueError("Require 0 <= low < target <= max_refill <= 1")
        if self.buffer_cycles < 0:
            raise ValueError("buffer_cycles must be nonnegative")
        if not 0 < self.full_pile_fraction <= 1:
            raise ValueError("full_pile_fraction must lie in (0, 1]")


class FifoPolicy:
    """Low infeeds first, then full-pile relief, then oldest unreserved trucks.

    For each service request, choose the nearest eligible idle loader. For that
    loader, maximize transferable quantity before minimizing pile travel. FIFO
    advances when all older truck material is reserved, not only after departure.
    """

    def __init__(self, parameters=None, *, truck_rule=None):
        self.parameters = parameters if parameters is not None else FifoParameters()
        if not isinstance(self.parameters, FifoParameters):
            raise ValueError("FifoPolicy requires FifoParameters")
        self.refill_active = {}
        self.targets = {}
        self.truck_rule = truck_rule if truck_rule is not None else FifoTruckRule()

    def metadata(self):
        """Record the exact baseline assumptions used in a comparison."""
        return {
            "heuristic_version": 1,
            "dispatch_rule": "infeed_full_pile_" + self.truck_rule.name,
            "queue_rule": self.truck_rule.name,
            **asdict(self.parameters),
        }

    def get_state(self):
        """Expose detached hysteresis/target information for decision snapshots."""
        return {
            "refill_active": dict(self.refill_active),
            "targets": dict(self.targets),
            "truck_selection": self.truck_rule.get_state(),
        }

    @staticmethod
    def _coverage(infeed, state):
        return state.infeeds[infeed.id].amount / infeed.consumption_rate

    @staticmethod
    def _select_loader(options, destination, state):
        return min(
            {a.loader_id for a in options},
            key=lambda key: (state.loaders[key].location.distance_to(destination), key),
        )

    @staticmethod
    def _select_pile(options, state, context):
        """Options belong to one loader/request: quantity outranks pile proximity."""

        def key(a):
            source, destination = route(a, state, context)
            return (
                -a.quantity,
                source.distance_to(destination),
                state.loaders[a.loader_id].location.distance_to(source),
                a.source_id,
                a.destination_id,
            )

        return min(options, key=key)

    def refill_targets(self, state, context, options=None):
        """Cover two repeat delivery cycles when possible, with a 75% default cap."""
        options = candidates(state, context) if options is None else options
        targets = {}
        for infeed in context.infeeds:
            target = self.parameters.target_fraction * infeed.capacity
            choices = [
                a
                for a in options
                if a.destination_kind == "infeed" and a.destination_id == infeed.id
            ]
            if choices and infeed.consumption_rate > 0:
                loader_id = self._select_loader(choices, infeed.location, state)
                task = self._select_pile(
                    [a for a in choices if a.loader_id == loader_id], state, context
                )
                loader = next(s for s in context.loaders if s.id == loader_id)
                source, destination = route(task, state, context)
                cycle = (
                    2 * source.distance_to(destination) / loader.speed
                    + task.quantity / loader.pickup_rate
                    + task.quantity / loader.deposit_rate
                )
                target = max(
                    target, self.parameters.buffer_cycles * cycle * infeed.consumption_rate
                )
            targets[infeed.id] = min(target, self.parameters.max_refill_fraction * infeed.capacity)
        return targets

    def _refill(self, options, state, context, active):
        requests = []
        for infeed in context.infeeds:
            inv = state.infeeds[infeed.id]
            planned = inv.amount + inv.reserved_incoming
            if (
                infeed.consumption_rate <= 0
                or not active.get(infeed.id, False)
                or planned >= self.targets[infeed.id] - EPS
            ):
                continue
            limit = max(0.0, self.parameters.max_refill_fraction * infeed.capacity - planned)
            choices = [
                replace(a, quantity=min(a.quantity, limit))
                for a in options
                if a.destination_kind == "infeed"
                and a.destination_id == infeed.id
                and min(a.quantity, limit) > EPS
            ]
            if choices:
                requests.append((infeed, choices))
        if not requests:
            return None
        infeed, choices = min(
            requests, key=lambda pair: (self._coverage(pair[0], state), pair[0].id)
        )
        loader = self._select_loader(choices, infeed.location, state)
        return self._select_pile([a for a in choices if a.loader_id == loader], state, context)

    def _relieve_full_pile(self, options, state, context):
        full = {
            spec.id
            for spec in context.piles
            if state.piles[spec.id].amount
            + state.piles[spec.id].reserved_incoming
            - state.piles[spec.id].reserved_outgoing
            >= self.parameters.full_pile_fraction * spec.capacity - EPS
        }
        choices = [a for a in options if a.source_kind == "pile" and a.source_id in full]
        requests = [
            (i, [a for a in choices if a.destination_id == i.id])
            for i in context.infeeds
            if i.consumption_rate > 0
        ]
        requests = [(i, jobs) for i, jobs in requests if jobs]
        if not requests:
            return None
        infeed, choices = min(
            requests, key=lambda pair: (self._coverage(pair[0], state), pair[0].id)
        )
        loader = self._select_loader(choices, infeed.location, state)
        return self._select_pile([a for a in choices if a.loader_id == loader], state, context)

    def _unload(self, options, state, context):
        pair = self.truck_rule.select_pair(options, state, context)
        if pair is None:
            return None
        loader, truck_id = pair
        choices = [
            a
            for a in options
            if a.source_kind == "truck" and a.source_id == truck_id and a.loader_id == loader
        ]
        return self._select_pile(choices, state, context)

    def choose(self, state, context):
        """Assemble assignments on a reservation copy; never preempt busy loaders."""
        trial, chosen = deepcopy(state), []
        self.targets = self.refill_targets(state, context)
        active = {i.id: self.refill_active.get(i.id, False) for i in context.infeeds}
        for infeed in context.infeeds:
            inv = trial.infeeds[infeed.id]
            if (
                infeed.consumption_rate <= 0
                or inv.amount + inv.reserved_incoming >= self.targets[infeed.id] - EPS
            ):
                active[infeed.id] = False
            elif inv.amount <= self.parameters.low_fraction * infeed.capacity + EPS:
                active[infeed.id] = True
        while options := candidates(trial, context):
            assignment = (
                self._refill(options, trial, context, active)
                or self._relieve_full_pile(options, trial, context)
                or self._unload(options, trial, context)
            )
            if assignment is None:
                break
            reserve(trial, context, assignment)
            self.truck_rule.on_reserved(assignment, trial)
            chosen.append(assignment)
            for infeed in context.infeeds:
                inv = trial.infeeds[infeed.id]
                if inv.amount + inv.reserved_incoming >= self.targets[infeed.id] - EPS:
                    active[infeed.id] = False
        self.refill_active = active
        return chosen

    def wake_levels(self, state, context):
        """Request this policy's 25% crossing, independently of other policy settings."""
        return {
            i.id: self.parameters.low_fraction * i.capacity
            for i in context.infeeds
            if i.consumption_rate > 0
        }
