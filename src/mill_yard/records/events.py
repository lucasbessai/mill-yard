"""Versioned event envelope shared by in-memory, file and live outputs."""

from dataclasses import dataclass

SCHEMA_VERSION = 4


@dataclass(frozen=True)
class EventRecord:
    """One model event; state is a detached snapshot when requested."""

    sequence: int
    time: float
    event: str
    details: dict
    state: dict | None = None

    def to_dict(self):
        """Preserve the flat JSON event interface used by existing consumers."""
        item = {
            "schema_version": SCHEMA_VERSION,
            "sequence": self.sequence,
            "time": self.time,
            "event": self.event,
            **self.details,
        }
        if self.state is not None:
            item["state"] = self.state
        return item
