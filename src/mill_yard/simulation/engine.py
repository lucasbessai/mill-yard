"""Multi-location SimPy model with phased transfers and joint dispatch."""

from copy import deepcopy
from dataclasses import asdict, dataclass

import simpy
from simpy.events import Event

from mill_yard.model.feasibility import EPS, candidates, reserve, validate_joint
from mill_yard.model.yard import (
    InventoryState,
    LoaderState,
    TruckState,
    YardScenario,
    YardState,
)
from mill_yard.records.recorder import Recorder


class _DispatchEvent(Event):
    """Dispatch after normal and urgent events at the same simulation time."""

    def __init__(self, env, callback):
        super().__init__(env)
        self._ok = True
        self._value = None
        self.callbacks.append(lambda _: callback())
        env.schedule(self, priority=2)


@dataclass
class YardResult:
    """Final physical state, completed trucks, and optional event history."""

    scenario: YardScenario
    state: YardState
    completed_trucks: list[dict]
    events: list[dict]
    policy: str
    seed: int | None
    recording_level: str
    policy_parameters: dict


class YardSimulation:
    """Orchestrate phased jobs, stop queues, and continuous consumption."""

    def __init__(self, scenario, policy, recorder=None):
        """Initialize a single-use simulation."""
        self.scenario = scenario
        self.policy = policy
        self.recorder = recorder or Recorder()
        self.env = simpy.Environment()
        self.state = YardState(
            0,
            {},
            [],
            {x.id: None for x in scenario.stops},
            {x.id: InventoryState(x.initial) for x in scenario.piles},
            {x.id: InventoryState(x.initial) for x in scenario.infeeds},
            {x.id: LoaderState(x.id, x.location) for x in scenario.loaders},
            {x.id: 0.0 for x in scenario.infeeds},
            {x.id: 0.0 for x in scenario.infeeds},
        )
        self.completed = []
        self.resources = {
            ("stop", x.id): simpy.Resource(self.env, capacity=x.max_loaders) for x in scenario.stops
        }
        self.resources.update(
            {
                ("pile", x.id): simpy.Resource(self.env, capacity=x.max_loaders)
                for x in scenario.piles
            }
        )
        self.resources.update(
            {
                ("infeed", x.id): simpy.Resource(self.env, capacity=x.max_loaders)
                for x in scenario.infeeds
            }
        )
        self.pending = False
        self.boundary_versions = {x.id: 0 for x in scenario.infeeds}
        self._used = False

    def _emit(self, event, **details):
        self.recorder.emit(self.state, event, **details)

    def _advance(self):
        elapsed = self.env.now - self.state.time
        if elapsed <= 0:
            return
        consumed = []
        for spec in self.scenario.infeeds:
            inv = self.state.infeeds[spec.id]
            used = min(inv.amount, elapsed * spec.consumption_rate)
            inv.amount = max(0.0, inv.amount - used)
            self.state.consumed[spec.id] += used
            if spec.consumption_rate:
                self.state.starvation[spec.id] += max(0.0, elapsed - used / spec.consumption_rate)
            if used > EPS:
                consumed.append((spec.id, used))
        self.state.time = self.env.now
        for infeed, used in consumed:
            self._emit("consumption", infeed=infeed, quantity=used, interval=elapsed)

    def _wake(self):
        if not self.pending:
            self.pending = True
            _DispatchEvent(self.env, self._dispatch)

    def _arrivals(self):
        for arrival in sorted(self.scenario.arrivals, key=lambda a: (a.time, a.id)):
            yield self.env.timeout(arrival.time - self.env.now)
            self._advance()
            self.state.trucks[arrival.id] = TruckState(arrival.id, arrival.time, arrival.quantity)
            self.state.queue.append(arrival.id)
            self._emit("arrival", truck=arrival.id, quantity=arrival.quantity)
            self._wake()

    def _admit(self):
        for stop in self.scenario.stops:
            if self.state.stops[stop.id] is None and self.state.queue:
                truck_id = self.state.queue.pop(0)
                truck = self.state.trucks[truck_id]
                truck.stop_id = stop.id
                truck.entered_at = self.env.now
                self.state.stops[stop.id] = truck_id
                self._emit("stop_enter", truck=truck_id, stop=stop.id)

    def _intervals(self):
        while True:
            yield self.env.timeout(self.scenario.wake_interval)
            self._advance()
            self._emit("interval_wake")
            self._wake()

    def _schedule_boundaries(self):
        levels = (
            self.policy.wake_levels(deepcopy(self.state), self.scenario)
            if self.scenario.wake_mode == "policy"
            else {}
        )
        for spec in self.scenario.infeeds:
            self.boundary_versions[spec.id] += 1
            version = self.boundary_versions[spec.id]
            inv = self.state.infeeds[spec.id].amount
            if spec.consumption_rate <= 0 or inv <= EPS:
                continue
            level = levels.get(spec.id, spec.low)
            if not 0 <= level <= spec.capacity:
                raise ValueError("Policy wake level outside infeed capacity")
            # Always report physical low and empty boundaries; only policy wakes dispatch.
            points = {0.0}
            if inv > spec.low + EPS:
                points.add(spec.low)
            if self.scenario.wake_mode == "policy" and inv > level + EPS:
                points.add(level)
            for point in points:
                self.env.process(self._boundary(spec, point, version, level))

    def _boundary(self, spec, level, version, requested):
        delay = (self.state.infeeds[spec.id].amount - level) / spec.consumption_rate
        yield self.env.timeout(delay)
        if version != self.boundary_versions[spec.id]:
            return
        self._advance()
        self._emit(
            "starvation_start" if level == 0 else "threshold_crossing", infeed=spec.id, level=level
        )
        if self.scenario.wake_mode == "policy" and abs(level - requested) <= EPS:
            self._wake()

    def _dispatch(self):
        self.pending = False
        self._advance()
        self._admit()
        for spec in self.scenario.infeeds:
            inv = self.state.infeeds[spec.id]
            if inv.amount <= spec.low + EPS:
                inv.refill_active = True
            if inv.amount + inv.incoming >= spec.target - EPS:
                inv.refill_active = False
        options = candidates(self.state, self.scenario)
        self._emit("decision_before", candidates=[asdict(a) for a in options])
        assignments = self.policy.choose(deepcopy(self.state), self.scenario)
        validate_joint(self.state, self.scenario, assignments)
        for assignment in assignments:
            reserve(self.state, self.scenario, assignment)
        for spec in self.scenario.infeeds:
            inv = self.state.infeeds[spec.id]
            if inv.amount + inv.incoming >= spec.target - EPS:
                inv.refill_active = False
        self._emit("decision", assignments=[asdict(a) for a in assignments])
        for assignment in assignments:
            self.env.process(self._job(assignment))
        self._schedule_boundaries()

    def _travel(self, loader, spec, destination, phase):
        distance = loader.location.distance_to(destination)
        duration = distance / spec.speed
        loader.phase = phase
        loader.phase_end = self.env.now + duration
        loader.travel_started = self.env.now
        loader.travel_distance = distance
        self._emit(
            "travel_start", loader=loader.id, phase=phase, distance=distance, duration=duration
        )
        yield self.env.timeout(duration)
        self._advance()
        loader.distance += distance
        loader.travel_time += duration
        loader.location = destination
        loader.phase_end = None
        loader.travel_started = None
        loader.travel_distance = 0.0
        self._emit("travel_complete", loader=loader.id, phase=phase, distance=distance)

    def _job(self, a):
        loader = self.state.loaders[a.loader_id]
        spec = next(x for x in self.scenario.loaders if x.id == a.loader_id)
        if a.source_kind == "truck":
            truck = self.state.trucks[a.source_id]
            source_id = truck.stop_id
            source = next(x for x in self.scenario.stops if x.id == source_id)
            source_kind = "stop"
        else:
            source = next(x for x in self.scenario.piles if x.id == a.source_id)
            source_id, source_kind = source.id, "pile"
        destination = next(
            x
            for x in (
                self.scenario.piles if a.destination_kind == "pile" else self.scenario.infeeds
            )
            if x.id == a.destination_id
        )
        yield from self._travel(loader, spec, source.location, "travel_to_source")
        loader.phase = "waiting_pickup"
        self._emit("pickup_wait", loader=a.loader_id, source=a.source_id)
        with self.resources[(source_kind, source_id)].request() as request:
            yield request
            self._advance()
            loader.phase = "pickup"
            loader.phase_end = self.env.now + a.quantity / spec.pickup_rate
            if a.source_kind == "truck":
                truck = self.state.trucks[a.source_id]
                if truck.first_pickup_at is None:
                    truck.first_pickup_at = self.env.now
                if truck.active_pickups == 0:
                    truck.busy_since = self.env.now
                truck.active_pickups += 1
            self._emit("pickup_start", loader=a.loader_id, source=a.source_id, quantity=a.quantity)
            yield self.env.timeout(a.quantity / spec.pickup_rate)
            self._advance()
            if a.source_kind == "truck":
                truck = self.state.trucks[a.source_id]
                truck.remaining -= a.quantity
                truck.reserved -= a.quantity
                truck.active_pickups -= 1
                if truck.active_pickups == 0:
                    truck.handling_time += self.env.now - truck.busy_since
                    truck.busy_since = None
            else:
                inv = self.state.piles[a.source_id]
                inv.amount -= a.quantity
                inv.outgoing -= a.quantity
            loader.carried = a.quantity
            loader.phase_end = None
            self._emit(
                "pickup_complete", loader=a.loader_id, source=a.source_id, quantity=a.quantity
            )
            if a.source_kind == "truck" and truck.remaining <= EPS:
                self.state.stops[truck.stop_id] = None
                self.completed.append(
                    {
                        **asdict(truck),
                        "completed_at": self.env.now,
                        "queue_wait": truck.entered_at - truck.arrival,
                        "total_time": self.env.now - truck.arrival,
                        "waiting_time": self.env.now - truck.arrival - truck.handling_time,
                    }
                )
                del self.state.trucks[truck.id]
                self._emit("truck_departure", truck=truck.id, stop=truck.stop_id)
            self._wake()
        yield from self._travel(loader, spec, destination.location, "travel_to_destination")
        loader.phase = "waiting_deposit"
        self._emit("deposit_wait", loader=a.loader_id, destination=a.destination_id)
        with self.resources[(a.destination_kind, a.destination_id)].request() as request:
            yield request
            self._advance()
            loader.phase = "deposit"
            loader.phase_end = self.env.now + a.quantity / spec.deposit_rate
            self._emit(
                "deposit_start",
                loader=a.loader_id,
                destination=a.destination_id,
                quantity=a.quantity,
            )
            yield self.env.timeout(a.quantity / spec.deposit_rate)
            self._advance()
            inv = (self.state.piles if a.destination_kind == "pile" else self.state.infeeds)[
                a.destination_id
            ]
            was_empty = inv.amount <= EPS
            inv.incoming -= a.quantity
            inv.amount += a.quantity
            loader.carried = 0
            loader.phase = "idle"
            loader.phase_end = None
            loader.assignment = None
            self._emit(
                "deposit_complete",
                loader=a.loader_id,
                destination=a.destination_id,
                quantity=a.quantity,
            )
            if a.destination_kind == "infeed" and was_empty:
                self._emit("starvation_end", infeed=a.destination_id)
            self._wake()

    def run(self):
        """Run through the inclusive horizon and preserve unfinished work."""
        if self._used:
            raise RuntimeError("A simulation instance can only be run once")
        self._used = True
        self._emit("initial")
        for spec in self.scenario.infeeds:
            if spec.initial <= EPS and spec.consumption_rate > 0:
                self._emit("starvation_start", infeed=spec.id, level=0.0)
        self.env.process(self._arrivals())
        if self.scenario.wake_mode == "interval":
            self.env.process(self._intervals())
        self._wake()
        while self.env.peek() <= self.scenario.horizon:
            self.env.step()
        if self.env.now < self.scenario.horizon:
            self.env.run(until=self.scenario.horizon)
        self._advance()
        self._emit("horizon")
        return YardResult(
            self.scenario,
            self.state,
            self.completed,
            self.recorder.events,
            type(self.policy).__name__,
            getattr(self.policy, "seed", None),
            self.recorder.level,
            self.policy.metadata() if hasattr(self.policy, "metadata") else {},
        )


def run_yard(scenario, policy, *, record="snapshots", trace=False, trace_state=False):
    """Run a fresh multi-location yard simulation."""
    return YardSimulation(scenario, policy, Recorder(record, trace, trace_state)).run()
