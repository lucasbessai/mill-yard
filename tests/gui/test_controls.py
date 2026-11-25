"""Map transforms and history navigation need no graphics dependencies."""

import pytest

from mill_yard.gui.controls import ScreenRect, ScrollList, YardCamera
from mill_yard.model.types import Point


def test_zoom_preserves_world_point_under_cursor_and_pan_roundtrips(tiny_yard):
    rect = ScreenRect(14, 194, 850, 590)
    camera = YardCamera.fit(tiny_yard, rect)
    cursor = (200, 480)
    before = camera.world(*cursor, rect)
    camera.zoom_at(3, *cursor, rect)
    assert camera.world(*cursor, rect).x == pytest.approx(before.x)
    assert camera.world(*cursor, rect).y == pytest.approx(before.y)
    position = camera.point(Point(-10, -20), rect)
    camera.pan(50, -30)
    after = camera.point(Point(-10, -20), rect)
    assert after == pytest.approx((position[0] + 50, position[1] - 30))
    camera.zoom_at(1e6, *cursor, rect)
    assert camera.zoom == 8
    camera.zoom_at(1e-6, *cursor, rect)
    assert camera.zoom == 0.25


def test_full_history_follows_tail_but_preserves_reading_position():
    scroll = ScrollList()
    scroll.sync(100, 5)
    assert list(scroll.visible) == list(range(95, 100))
    scroll.scroll(-1000)
    assert scroll.offset == 0
    scroll.sync(300, 7)
    assert list(scroll.visible) == list(range(7))
    scroll.seek(1)
    scroll.sync(310, 7)
    assert scroll.offset == 303
    scroll.seek(-10)
    assert scroll.offset == 0
    scroll.sync(0, 0)
    assert not list(scroll.visible)


def test_task_and_queue_lists_start_at_first_row():
    scroll = ScrollList(follow=False)
    scroll.sync(20, 3)
    assert scroll.offset == 0
    scroll.scroll(100)
    assert scroll.offset == 17
    scroll.sync(30, 3)
    assert scroll.offset == 17
