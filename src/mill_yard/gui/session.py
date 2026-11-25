"""Display-independent playback and operator-session controls."""

from math import isfinite

from mill_yard.policies.human_controlled import HumanDecision
from mill_yard.records.recorder import Recorder
from mill_yard.simulation.engine import YardSimulation


class SimulationSession:
    """Control a live engine without depending on a desktop toolkit."""

    def __init__(self, scenario, policy_factory, *, speed=5.0, record="events", validate=False):
        self.scenario = scenario
        self.policy_factory = policy_factory
        self.speed = speed
        self.record = record
        self.validate = validate
        self.playing = False
        self.decision = None
        self.simulation = None
        self.restart()

    def restart(self):
        """Start a new run using a fresh policy and the same scenario."""
        if self.simulation is not None:
            self.simulation.recorder.close()
        self.simulation = YardSimulation(
            self.scenario, self.policy_factory(), Recorder(self.record), validate=self.validate
        )
        self.decision = None
        self.playing = False
        self.simulation.start()
        self.simulation.step()
        self.display_time = self.simulation.state.time
        self._sync_decision()

    @property
    def speed(self):
        """Playback rate in simulation minutes per real second."""
        return self._speed

    @speed.setter
    def speed(self, value):
        if not isfinite(value) or value <= 0:
            raise ValueError("Playback speed must be positive and finite")
        self._speed = value

    def _sync_decision(self):
        request = self.simulation.pending_decision
        if request is not None:
            if self.decision is None or self.decision.request is not request:
                self.decision = HumanDecision(request)
            self.playing = False
        else:
            self.decision = None

    def step(self):
        """Pause playback and advance one settled timestamp if input is not pending."""
        self.playing = False
        self.simulation.step()
        self.display_time = self.simulation.state.time
        self._sync_decision()

    def play(self):
        """Resume only when no decision needs operator input."""
        self.playing = self.decision is None and not self.simulation.finished

    def tick(self, elapsed_seconds):
        """Advance playback; cap work per frame to keep controls responsive."""
        if not self.playing:
            return
        target = min(
            self.scenario.horizon, self.display_time + max(0.0, elapsed_seconds) * self.speed
        )
        budget = 40
        while (
            self.simulation.next_time <= target
            and not self.simulation.finished
            and self.simulation.pending_decision is None
            and budget
        ):
            self.simulation.step()
            budget -= 1
        self._sync_decision()
        if self.decision is not None or self.simulation.finished:
            self.display_time = self.simulation.state.time
            self.playing = False
        else:
            self.display_time = max(
                self.simulation.state.time, min(target, self.simulation.next_time)
            )

    def submit(self, *, wait=False, resume=True):
        """Commit the draft or choose to idle, then resume if requested."""
        if self.decision is None:
            raise RuntimeError("No operator decision is pending")
        assignments = [] if wait else list(self.decision.assignments)
        self.simulation.submit_decision(assignments)
        self.decision = None
        # Start the assigned processes without jumping to their future completion.
        if self.simulation.next_time <= self.simulation.env.now:
            self.simulation.step()
        self.display_time = self.simulation.state.time
        self._sync_decision()
        if resume:
            self.play()

    def close(self):
        """Release recording resources when a window closes."""
        self.playing = False
        self.simulation.recorder.close()
