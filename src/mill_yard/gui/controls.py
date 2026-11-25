"""Toolkit-independent map navigation and scroll positions."""

from dataclasses import dataclass

from mill_yard.model.types import Point


@dataclass(frozen=True)
class ScreenRect:
    """A rectangle in bottom-left screen coordinates."""

    x: float
    y: float
    width: float
    height: float

    def contains(self, x, y):
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height


@dataclass
class YardCamera:
    """An equal-scale map transform with cursor-anchored zoom and panning."""

    center: Point
    base_scale: float
    zoom: float = 1.0

    @classmethod
    def fit(cls, scenario, rect):
        points = [
            spec.location
            for spec in (*scenario.stops, *scenario.piles, *scenario.infeeds, *scenario.loaders)
        ]
        points += [area.queue_location for area in scenario.unloading_areas]
        left, right = min(p.x for p in points), max(p.x for p in points)
        bottom, top = min(p.y for p in points), max(p.y for p in points)
        scale = max(
            0.01,
            min(
                max(1, rect.width - 240) / max(20, right - left),
                max(1, rect.height - 220) / max(20, top - bottom),
            ),
        )
        return cls(Point((left + right) / 2, (bottom + top) / 2), scale)

    @property
    def scale(self):
        return self.base_scale * self.zoom

    def point(self, point, rect):
        return (
            rect.x + rect.width / 2 + (point.x - self.center.x) * self.scale,
            rect.y + rect.height / 2 + (point.y - self.center.y) * self.scale,
        )

    def world(self, x, y, rect):
        return Point(
            self.center.x + (x - rect.x - rect.width / 2) / self.scale,
            self.center.y + (y - rect.y - rect.height / 2) / self.scale,
        )

    def zoom_at(self, factor, x, y, rect):
        before = self.world(x, y, rect)
        self.zoom = min(8.0, max(0.25, self.zoom * factor))
        after = self.world(x, y, rect)
        self.center = Point(self.center.x + before.x - after.x, self.center.y + before.y - after.y)

    def pan(self, dx, dy):
        self.center = Point(self.center.x - dx / self.scale, self.center.y - dy / self.scale)


@dataclass
class ScrollList:
    """Scroll a full collection; follow new rows only while already at its end."""

    count: int = 0
    capacity: int = 1
    offset: int = 0
    follow: bool = True

    @property
    def maximum(self):
        return max(0, self.count - self.capacity)

    def sync(self, count, capacity):
        following = self.follow and self.offset == self.maximum
        self.count, self.capacity = count, max(1, capacity)
        self.offset = self.maximum if following else min(self.offset, self.maximum)

    def scroll(self, rows):
        self.offset = min(self.maximum, max(0, self.offset + int(rows)))

    def seek(self, fraction):
        self.offset = round(min(1, max(0, fraction)) * self.maximum)

    @property
    def visible(self):
        return range(self.offset, min(self.count, self.offset + self.capacity))
