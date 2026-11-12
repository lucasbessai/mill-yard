"""Small shared scenarios for interaction and desktop integration tests."""

import pytest

from mill_yard.model.scenario import (
    InfeedSpec,
    LoaderSpec,
    PileSpec,
    StopSpec,
    UnloadingAreaSpec,
    YardScenario,
)
from mill_yard.model.types import Arrival, Point


@pytest.fixture
def tiny_yard():
    """Two loaders can unload one truck; a full, inactive infeed prevents refills."""
    return YardScenario(
        horizon=6,
        arrivals=(Arrival(0, 10, "truck"),),
        stops=(StopSpec("bay", Point(0, 0), 2),),
        piles=(PileSpec("pile", Point(10, 0), 20, 0),),
        infeeds=(InfeedSpec("mill", Point(20, 0), 10, 10, 0, 2, 8),),
        loaders=(
            LoaderSpec("l1", Point(0, 0), 5, 10, 5, 5),
            LoaderSpec("l2", Point(0, 0), 5, 10, 5, 5),
        ),
        wake_interval=2,
    )


@pytest.fixture
def multi_area_yard():
    """Two North bays, one South bay; material only goes to storage."""
    return YardScenario(
        horizon=120,
        unloading_areas=(
            UnloadingAreaSpec("north", Point(-15, 10)),
            UnloadingAreaSpec("south", Point(115, 0)),
        ),
        arrivals=(
            Arrival(0, 10, "n0", "north"),
            Arrival(0, 10, "n1", "north"),
            Arrival(0.1, 10, "s0", "south"),
            Arrival(1, 10, "n2", "north"),
            Arrival(2, 10, "s1", "south"),
            Arrival(3, 10, "n3", "north"),
        ),
        stops=(
            StopSpec("north-1", Point(0, 0), 2, "north"),
            StopSpec("north-2", Point(0, 10), 2, "north"),
            StopSpec("south-1", Point(100, 0), 2, "south"),
        ),
        piles=(PileSpec("p", Point(50, 0), 500, 0),),
        infeeds=(InfeedSpec("mill", Point(60, 0), 100, 100, 0, 25, 65),),
        loaders=(
            LoaderSpec("l1", Point(0, 0), 10, 10, 10, 10),
            LoaderSpec("l2", Point(100, 0), 10, 10, 10, 10),
        ),
    )
