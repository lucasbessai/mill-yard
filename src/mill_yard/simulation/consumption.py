"""Analytical consumption and same-time-settled infeed status transitions."""

from copy import deepcopy
from math import isclose

from simpy.events import Event

from mill_yard.model.feasibility import EPS


class Consumption:
    """Maintain inventory integrals and one effective schedule per crossing."""

    def __init__(self, engine):
        self.engine = engine
        self.scheduled = {}
        self.crossings = set()

    def advance(self):
        """Integrate every infeed to now before a discrete inventory change."""
        e = self.engine
        elapsed = e.env.now - e.state.time
        if elapsed <= 0:
            return
        consumed = []
        for spec in e.scenario.infeeds:
            inv = e.state.infeeds[spec.id]
            used = min(inv.amount, elapsed * spec.consumption_rate)
            inv.amount = max(0.0, inv.amount - used)
            e.state.consumed[spec.id] += used
            if spec.consumption_rate:
                e.state.starvation[spec.id] += max(0.0, elapsed - used / spec.consumption_rate)
            if used > EPS:
                consumed.append((spec.id, used))
        e.state.time = e.env.now
        for infeed, used in consumed:
            e._emit("consumption", infeed=infeed, quantity=used, interval=elapsed)

    def settle(self):
        """Emit only transitions that remain true after all same-time updates."""
        e = self.engine
        for spec in e.scenario.infeeds:
            inv = e.state.infeeds[spec.id]
            starved = spec.consumption_rate > 0 and inv.amount <= EPS
            if starved != inv.starved:
                inv.starved = starved
                e._emit("starvation_start" if starved else "starvation_end", infeed=spec.id)
        for infeed, level in sorted(self.crossings):
            if level > EPS and e.state.infeeds[infeed].amount <= level + EPS:
                e._emit("threshold_crossing", infeed=infeed, level=level)
        self.crossings.clear()

    def schedule(self):
        """Reuse unchanged crossing times instead of creating stale timers each tick."""
        e = self.engine
        levels = (
            e.policy.wake_levels(deepcopy(e.state), e.context)
            if e.scenario.wake_mode == "policy"
            else {}
        )
        specs = {i.id: i for i in e.scenario.infeeds}
        if set(levels) - set(specs):
            raise ValueError("Policy requested an unknown infeed")
        wanted = {}
        for spec in e.scenario.infeeds:
            inv = e.state.infeeds[spec.id].amount
            requested = levels.get(spec.id)
            if requested is not None and not 0 <= requested <= spec.capacity:
                raise ValueError("Policy wake level outside infeed capacity")
            if spec.consumption_rate <= 0 or inv <= EPS:
                continue
            points = {0.0, spec.low}
            if requested is not None:
                points.add(requested)
            for level in points:
                if inv <= level + EPS:
                    continue
                key = (spec.id, level)
                due = e.env.now + (inv - level) / spec.consumption_rate
                wake = requested is not None and abs(level - requested) <= EPS
                wanted[key] = (due, wake)
        for key in list(self.scheduled):
            if key not in wanted:
                del self.scheduled[key]
        for key, (due, wake) in wanted.items():
            previous = self.scheduled.get(key)
            if (
                previous is not None
                and isclose(previous[0], due, rel_tol=0, abs_tol=1e-7)
                and previous[1] == wake
            ):
                continue
            token = (due, wake, object())
            self.scheduled[key] = token
            event = Event(e.env)
            event._ok, event._value = True, None
            event.callbacks.append(lambda _, key=key, token=token: self._cross(key, token))
            e.env.schedule(event, delay=max(0.0, due - e.env.now))

    def _cross(self, key, token):
        if self.scheduled.get(key) != token:
            return
        del self.scheduled[key]
        self.advance()
        self.crossings.add(key)
        self.engine._wake(dispatch=token[1])
