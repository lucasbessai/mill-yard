"""Structured event records and optional live output."""

import json
from dataclasses import asdict
from pathlib import Path

from mill_yard.records.events import EventRecord


class Recorder:
    """Collect immutable event data, with optional snapshots and live trace."""

    def __init__(
        self,
        level: str = "snapshots",
        trace: bool = False,
        trace_state: bool = False,
        *,
        events_path=None,
        retain_events=True,
    ):
        """Choose summary, events, or snapshots recording."""
        if level not in ("summary", "events", "snapshots"):
            raise ValueError("Recording level must be summary, events or snapshots")
        self.level = level
        self.trace = trace or trace_state
        self.trace_state = trace_state
        self.events = []
        self.sequence = 0
        self.retain_events = retain_events
        self.events_path = str(events_path) if events_path is not None else None
        self.stream = None
        if events_path is not None:
            path = Path(events_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = path.open("w", encoding="utf-8")

    def emit(self, state, event: str, **details) -> None:
        """Capture an event and, if requested, a copy of the current state."""
        self.sequence += 1
        snapshot = asdict(state) if self.level == "snapshots" or self.trace_state else None
        item = EventRecord(self.sequence, state.time, event, details, snapshot).to_dict()
        if self.level != "summary":
            if self.retain_events:
                self.events.append(item)
            if self.stream is not None:
                self.stream.write(json.dumps(item, allow_nan=False) + "\n")
                self.stream.flush()
        if self.trace:
            print(f"[{state.time:9.3f}] {event} {details}")
            if self.trace_state:
                print(item["state"])

    def close(self):
        """Flush and close an optional streaming output."""
        if self.stream is not None:
            self.stream.close()
