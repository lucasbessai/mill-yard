"""Shared value types for the yard scenario and state."""

from dataclasses import dataclass
from math import hypot


@dataclass(frozen=True)
class Point:
    """A location in metres on a two-dimensional plane."""

    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        """Return the straight-line distance to another location."""
        return hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Arrival:
    """A scheduled truck delivery."""

    time: float
    quantity: float
    id: str
