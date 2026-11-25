"""Playback and human session logic works without a graphical display."""

from dataclasses import asdict

from mill_yard.gui.presentation import Viewport, loader_position
from mill_yard.gui.session import SimulationSession
from mill_yard.model.types import Point
from mill_yard.policies.human_controlled import HumanControlledPolicy
from mill_yard.policies.threshold import ThresholdPolicy


def test_playback_speed_and_restart(tiny_yard):
    session = SimulationSession(tiny_yard, ThresholdPolicy, speed=2, validate=True)
    session.play()
    session.tick(1)
    assert session.display_time == 2
    session.tick(10)
    assert session.simulation.finished
    session.restart()
    assert session.display_time == 0
    assert not session.playing
    session.close()


def test_human_playback_pauses_until_joint_submission(tiny_yard):
    session = SimulationSession(tiny_yard, HumanControlledPolicy, validate=True)
    assert session.decision is not None
    session.play()
    session.tick(10)
    assert session.display_time == 0
    session.decision.add(session.decision.options[0])
    session.decision.add(session.decision.options[0])
    session.submit()
    assert session.playing
    session.tick(10)
    assert session.simulation.finished
    assert len(session.simulation.completed_trucks) == 1


def test_visual_interpolation_does_not_change_model_state(tiny_yard):
    session = SimulationSession(tiny_yard, ThresholdPolicy)
    session.step()  # Pickups complete at one, loaded travel ends at two.
    state = session.simulation.state
    before = asdict(state)
    loader = state.loaders["l1"]
    assert loader_position(loader, state, tiny_yard, 1.5) == Point(5, 0)
    assert asdict(state) == before


def test_viewport_handles_coincident_or_negative_coordinates(tiny_yard):
    view = Viewport.fit(tiny_yard, 800, 600)
    x, y = view.point(Point(0, 0))
    assert 0 <= x <= 800
    assert 0 <= y <= 600
