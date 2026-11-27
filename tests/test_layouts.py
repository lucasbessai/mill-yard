"""Tests for layout configuration, constraints, and optimizer interface."""

from pathlib import Path

import pytest

from mill_yard.layouts.config import load_layout_config
from mill_yard.layouts.objective import constraint_violations, vector_to_scenario
from mill_yard.layouts.optimizer import optimize_layout

LAYOUT_CONFIG = Path(__file__).parents[1] / "configs" / "layout_optimization" / "two_pile.yaml"


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
    assert result.vector == layout_config.initial == (100.0, 0.0, 100.0, 40.0)
    assert len(result.evaluation.violations) == 9
    assert len(result.trajectory) == 1
    assert result.trajectory[0].evaluation_number == 1
    assert result.trajectory[0].vector == result.vector


def test_optimizer_trajectory_records_accepted_incumbents(layout_config, monkeypatch):
    from mill_yard.layouts import optimizer
    from mill_yard.layouts.objective import LayoutEvaluation

    target = (50.0, 30.0, 0.0, 0.0)
    evaluated_vectors = []

    def fake_evaluate(_config, vector):
        evaluated_vectors.append(tuple(vector))
        return LayoutEvaluation(
            sum((value - goal) ** 2 for value, goal in zip(vector, target, strict=True)),
            (0.0,),
            {},
        )

    monkeypatch.setattr(optimizer, "evaluate_layout", fake_evaluate)
    result = optimizer.optimize_layout(
        layout_config, budget=9, seed=4, initial=(0.0, 0.0, 0.0, 0.0)
    )

    assert len(result.trajectory) > 1
    assert result.evaluations == 9  # initial point plus a complete 2 * 4 poll
    assert result.vector == (50.0, 0.0, 0.0, 0.0)  # best candidate in the full poll
    assert all(
        sum(first != second for first, second in zip(candidate, (0, 0, 0, 0), strict=True)) == 1
        for candidate in evaluated_vectors[1:]
    )
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


def test_optimizer_uses_explicit_config_initial(layout_config):
    from dataclasses import replace

    config = replace(layout_config, initial=(10, 11, 30, 31))
    result = optimize_layout(config, budget=1)

    assert result.vector == (10.0, 11.0, 30.0, 31.0)


def test_opportunistic_algorithm_can_be_selected(layout_config, monkeypatch):
    from mill_yard.layouts import optimizer
    from mill_yard.layouts.objective import LayoutEvaluation

    target = (50.0, 0.0, 0.0, 0.0)
    monkeypatch.setattr(
        optimizer,
        "evaluate_layout",
        lambda _config, vector: LayoutEvaluation(
            sum((value - goal) ** 2 for value, goal in zip(vector, target, strict=True)),
            (0.0,),
            {},
        ),
    )

    result = optimize_layout(
        layout_config,
        budget=3,
        initial=(0.0, 0.0, 0.0, 0.0),
        algorithm="opportunistic",
    )

    assert result.evaluations == 3
    assert result.vector == (50.0, 0.0, 0.0, 0.0)
    assert len(result.trajectory) == 2


def test_optimizer_rejects_unknown_algorithm(layout_config):
    with pytest.raises(ValueError, match="algorithm must be"):
        optimize_layout(layout_config, budget=1, algorithm="unknown")


def test_optimizer_reports_grid_refinement_and_incumbent_count(layout_config, monkeypatch, capsys):
    from mill_yard.layouts import optimizer
    from mill_yard.layouts.objective import LayoutEvaluation

    monkeypatch.setattr(
        optimizer,
        "evaluate_layout",
        lambda _config, _vector: LayoutEvaluation(1.0, (0.0,), {}),
    )

    optimizer.optimize_layout(layout_config, budget=17)

    output = capsys.readouterr().out
    assert "Initial incumbent after 1 evaluation: objective=1" in output
    assert "Grid refined after 9 evaluations" in output
    assert "dx=25, dy=7.5" in output
    assert "incumbents=1; objective=1" in output


def test_plot_trajectory_saves_a_figure(layout_config, tmp_path):
    from mill_yard.layouts.optimizer import LayoutTrajectoryPoint
    from mill_yard.layouts.visualization import plot_layout_trajectory

    target = tmp_path / "plots" / "trajectory.png"
    trajectory = (
        LayoutTrajectoryPoint(1, (100, 0, 100, 40), 12.0, (0.0,) * 9),
        LayoutTrajectoryPoint(4, (110, 0, 90, 40), 8.0, (0.0,) * 9),
    )

    figure, axes = plot_layout_trajectory(layout_config, trajectory, output=target)

    assert target.exists()
    assert tuple(figure.get_size_inches()) == (14.0, 9.0)
    assert axes.get_title().startswith("Layout search: objective 8")
    legend = axes.get_legend()
    assert legend is not None
    assert legend.get_bbox_to_anchor().transformed(axes.transAxes.inverted()).x0 > 1
    annotations = [annotation.get_text() for annotation in axes.texts]
    assert "pile-1 start" in annotations
    assert "pile-1 final" in annotations
    assert "pile-2 start" in annotations
    assert "pile-2 final" in annotations
    assert "stop-1" in annotations
    assert "loader-1" in annotations
    assert "mill-1" in annotations
    assert "main" in annotations
    import matplotlib.pyplot as plt

    plt.close(figure)


def test_three_area_six_bay_layout_config_loads_all_four_piles():
    path = Path(__file__).parents[1] / "configs" / "layout_optimization" / "four_piles.yaml"

    config = load_layout_config(path)

    assert config.point_ids == ("p1", "p2", "p3", "p4")
    assert config.initial == (-200, 140, 200, 140, -200, -140, 200, -140)
    assert len(config.scenario.stops) == 6
    assert len(config.scenario.piles) == 4


