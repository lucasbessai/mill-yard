"""Tests for layout configuration, constraints, and optimizer interface."""

from pathlib import Path

import pytest

from mill_yard.layouts.config import load_layout_config
from mill_yard.layouts.objective import constraint_violations, vector_to_scenario
from mill_yard.layouts.optimizer import optimize_layout

LAYOUT_CONFIG = Path(__file__).parents[1] / "configs" / "layouts" / "multi_yard.yaml"


@pytest.fixture
def layout_config():
    return load_layout_config(LAYOUT_CONFIG)


def test_overlapping_pois_have_spacing_violation(layout_config):
    vector = (50, 20, 50, 20)

    violations = constraint_violations(layout_config, vector)

    assert violations[-1] == pytest.approx(layout_config.min_spacing)
    assert all(violation >= 0 for violation in violations)


def test_separated_pois_have_no_spacing_violation(layout_config):
    vector = (20, 20, 40, 20)

    assert constraint_violations(layout_config, vector) == (0.0,) * 9


def test_coordinate_bounds_are_reported_per_poi(layout_config):
    vector = (-2, 20, 40, 70)

    violations = constraint_violations(layout_config, vector)

    assert violations[:4] == (2, 0.0, 0.0, 0.0)
    assert violations[4:8] == (0.0, 0.0, 0.0, 10)


def test_vector_updates_only_selected_pile_locations(layout_config):
    original_other_pile = next(pile for pile in layout_config.scenario.piles if pile.id == "pile-2")

    scenario = vector_to_scenario(layout_config, (12, 13, 45, 46))

    locations = {pile.id: pile.location for pile in scenario.piles}
    assert (locations["pile-1"].x, locations["pile-1"].y) == (12, 13)
    assert (locations["pile-2"].x, locations["pile-2"].y) == (45, 46)
    assert original_other_pile.location == layout_config.scenario.piles[1].location


def test_optimizer_respects_evaluation_budget_and_returns_vector(layout_config):
    result = optimize_layout(layout_config, budget=1, seed=3)

    assert result.evaluations == 1
    assert len(result.vector) == 2 * len(layout_config.point_ids)
    assert len(result.evaluation.violations) == 9
    assert len(result.trajectory) == 1
    assert result.trajectory[0].evaluation_number == 1
    assert result.trajectory[0].vector == result.vector


def test_optimizer_trajectory_records_accepted_incumbents(layout_config, monkeypatch):
    from mill_yard.layouts import optimizer
    from mill_yard.layouts.objective import LayoutEvaluation

    target = (50.0, 0.0, 0.0, 0.0)

    def fake_evaluate(_config, vector):
        return LayoutEvaluation(
            sum((value - goal) ** 2 for value, goal in zip(vector, target, strict=True)),
            (0.0,),
            {},
        )

    monkeypatch.setattr(optimizer, "evaluate_layout", fake_evaluate)
    result = optimizer.optimize_layout(
        layout_config, budget=3, seed=4, initial=(0.0, 0.0, 0.0, 0.0)
    )

    assert len(result.trajectory) > 1
    assert result.trajectory[0].evaluation_number == 1
    assert result.trajectory[-1].evaluation_number <= result.evaluations
    assert result.trajectory[-1].vector == result.vector
    assert result.trajectory[-1].objective == result.evaluation.objective
    assert all(
        current.objective < previous.objective
        for previous, current in zip(result.trajectory, result.trajectory[1:], strict=False)
    )


def test_vector_length_must_match_selected_pois(layout_config):
    with pytest.raises(ValueError, match="Expected 4 coordinates"):
        constraint_violations(layout_config, (1, 2))
