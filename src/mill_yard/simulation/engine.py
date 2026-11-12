"""Multi-location SimPy model with phased transfers and joint dispatch."""

from copy import deepcopy
from dataclasses import asdict

import simpy
from simpy.events import Event

from mill_yard.model.feasibility import candidates, reserve, validate_joint
from mill_yard.model.invariants import check_invariants
from mill_yard.model.state import HandlingState, InventoryState, LoaderState, YardState
from mill_yard.policies.base import DecisionRequest, PolicyContext
from mill_yard.records.recorder import Recorder
from mill_yard.simulation.consumption import Consumption
from mill_yard.simulation.loaders import LoaderProcesses
from mill_yard.simulation.result import YardResult
from mill_yard.simulation.trucks import TruckAdmission


class _CoordinationEvent(Event):
    """Settle physical state and optionally dispatch after same-time events."""

    def __init__(self, env, callback):
        super().__init__(env)
        self._ok = True
        self._value = None
        self.callbacks.append(lambda _: callback())
        env.schedule(self, priority=2)


class YardSimulation:
    """Orchestrate phased jobs, stop queues, and continuous consumption."""

    def __init__(self, scenario, policy, recorder=None, *, validate=False):
        """Initialize a single-use simulation."""
        self.validate = validate
        self.scenario = scenario
        self.policy = policy
        self.context = PolicyContext.from_scenario(scenario)
        self.loader_processes = LoaderProcesses(self)
        self.truck_admission = TruckAdmission(self)
        self.recorder = recorder or Recorder()
        self.env = simpy.Environment()
        self.state = YardState(
            0,
            {},
            {area.id: [] for area in scenario.unloading_areas},
            {x.id: None for x in scenario.stops},
            {x.id: InventoryState(x.initial) for x in scenario.piles},
            {x.id: InventoryState(x.initial) for x in scenario.infeeds},
            {x.id: LoaderState(x.id, x.location) for x in scenario.loaders},
            {x.id: 0.0 for x in scenario.infeeds},
            {x.id: 0.0 for x in scenario.infeeds},
        )
        self.completed_trucks = []
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
        self.coordination_pending = False
        self.consumption = Consumption(self)
        self.dispatch_requested = False
        self._used = False
        self.finished = False
        self.pending_decision = None
        if scenario.wake_mode == "interval":
            self.state.next_interval_at = scenario.wake_interval

    def _emit(self, event, **details):
        self.state.scheduled_crossings = sorted(
            (infeed, level, token[0])
            for (infeed, level), token in self.consumption.scheduled.items()
        )
        self.state.handling = {
            f"{kind}:{key}": HandlingState(
                [getattr(request, "loader_id", "") for request in resource.users],
                [getattr(request, "loader_id", "") for request in resource.queue],
            )
            for (kind, key), resource in self.resources.items()
        }
        if self.validate:
            check_invariants(self.state, self.scenario)
        self.recorder.emit(self.state, event, **details)

    def _advance(self):
        self.consumption.advance()

    def _wake(self, dispatch=True):
        self.dispatch_requested |= dispatch
        if not self.coordination_pending:
            self.coordination_pending = True
            _CoordinationEvent(self.env, self._settle_and_dispatch)

    def _intervals(self):
        while True:
            yield self.env.timeout(self.scenario.wake_interval)
            self._advance()
            self.state.next_interval_at = self.env.now + self.scenario.wake_interval
            self._emit("interval_wake")
            self._wake()

    def _settle_and_dispatch(self):
        self.coordination_pending = False
        self._advance()
        self.truck_admission.admit()
        self.consumption.settle()
        requested, self.dispatch_requested = self.dispatch_requested, False
        if self.env.now >= self.scenario.horizon:
            return
        if not requested:
            self.consumption.schedule()
            return
        options = candidates(self.state, self.scenario)
        self._emit(
            "decision_before",
            candidates=[asdict(a) for a in options]
            if self.recorder.level != "summary" or self.recorder.trace
            else None,
            policy_state=self.policy.get_state()
            if hasattr(self.policy, "get_state") and self.recorder.level == "snapshots"
            else None,
        )
        assignments = self.policy.choose(deepcopy(self.state), self.context)
        if isinstance(assignments, DecisionRequest):
            self.pending_decision = assignments
            self.consumption.schedule()
            self._emit("decision_requested")
            return
        self._commit_assignments(list(assignments))

    def _commit_assignments(self, assignments):
        """Validate an entire decision before mutating live reservations."""
        validate_joint(self.state, self.scenario, assignments)
        for assignment in assignments:
            reserve(self.state, self.scenario, assignment)
        self.consumption.schedule()
        self._emit("decision", assignments=[asdict(a) for a in assignments])
        for assignment in assignments:
            self.env.process(self.loader_processes.job(assignment))

    def submit_decision(self, assignments):
        """Resume an operator request; invalid choices leave the request pending."""
        if self.pending_decision is None:
            raise RuntimeError("No operator decision is pending")
        self._commit_assignments(list(assignments))
        self.pending_decision = None

    def start(self):
        """Initialize once without consuming the events at simulation time zero."""
        if self._used:
            raise RuntimeError("A simulation instance can only be run once")
        self._used = True
        self._emit("initial")
        self.consumption.settle()
        self.env.process(self.truck_admission.arrivals())
        if self.scenario.wake_mode == "interval":
            self.env.process(self._intervals())
        self._wake()

    @property
    def next_time(self):
        """Return the next timestamp, bounded by the configured horizon."""
        return min(self.env.peek(), self.scenario.horizon)

    def step(self):
        """Process one timestamp including all same-time events, or pause for input.

        After a human request the clock remains fixed until submit_decision is
        called. This method never waits for keyboard or graphical input.
        """
        if not self._used:
            self.start()
        if self.finished or self.pending_decision is not None:
            return self.state
        target = self.next_time
        while self.env.peek() <= target and self.pending_decision is None:
            self.env.step()
        if self.env.now < target and self.pending_decision is None:
            self.env.run(until=target)
        self._advance()
        if self.env.now >= self.scenario.horizon:
            self.finished = True
            self._emit("horizon")
        return self.state

    def result(self):
        """Return current metrics and records; unfinished sessions remain partial."""
        return YardResult(
            self.scenario,
            self.state,
            self.completed_trucks,
            self.recorder.events,
            type(self.policy).__name__,
            getattr(self.policy, "seed", None),
            self.recorder.level,
            self.policy.metadata() if hasattr(self.policy, "metadata") else {},
            self.recorder.events_path,
        )

    def run(self):
        """Run to the horizon; nonblocking operator policies require step/submit."""
        if self.finished:
            raise RuntimeError("A simulation instance can only be run once")
        if not self._used:
            self.start()
        while not self.finished:
            self.step()
            if self.pending_decision is not None:
                raise RuntimeError(
                    "Human input is required; use step/submit_decision or TerminalHumanPolicy"
                )
        return self.result()


def run_yard(
    scenario,
    policy,
    *,
    record="snapshots",
    trace=False,
    trace_state=False,
    events_path=None,
    retain_events=True,
    validate=False,
):
    """Run a fresh multi-location yard simulation."""
    recorder = Recorder(
        record, trace, trace_state, events_path=events_path, retain_events=retain_events
    )
    try:
        return YardSimulation(scenario, policy, recorder, validate=validate).run()
    finally:
        recorder.close()
