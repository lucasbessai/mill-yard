"""Structured event records and optional live output."""

from dataclasses import asdict


class Recorder:
    """Collect immutable event data, with optional snapshots and live trace."""

    def __init__(self, level: str = "snapshots", trace: bool = False, trace_state: bool = False):
        """Choose summary, events, or snapshots recording."""
        if level not in ("summary", "events", "snapshots"):
            raise ValueError("Recording level must be summary, events or snapshots")
        self.level = level
        self.trace = trace or trace_state
        self.trace_state = trace_state
        self.events = []
        self.sequence = 0

    def emit(self, state, event: str, **details) -> None:
        """Capture an event and, if requested, a copy of the current state."""
        self.sequence += 1
        item = {"sequence": self.sequence, "time": state.time, "event": event, **details}
        if self.level == "snapshots" or self.trace_state:
            item["state"] = asdict(state)
        if self.level != "summary":
            self.events.append(item)
        if self.trace:
            print(f"[{state.time:9.3f}] {event} {details}")
            if self.trace_state:
                print(item["state"])
