"""Load a simulation scenario together with layout search settings."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from mill_yard.config.loader import load_yard_scenario
from mill_yard.model.yard import YardScenario


@dataclass(frozen=True)
class LayoutConfig:
    """Layout variables and simple geometric feasibility limits."""

    scenario: YardScenario
    point_ids: tuple[str, ...]
    x_bounds: tuple[float, float]
    y_bounds: tuple[float, float]
    min_spacing: float = 0.0
    objective_weights: tuple[float, float, float] = (1.0, 1.0, 1.0)


def load_layout_config(path: str | Path) -> LayoutConfig:
    """Load a YAML layout definition; scenario paths are relative to that YAML file."""
    path = Path(path)
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError("Layout config must be a YAML mapping")
    try:
        scenario_path = (path.parent / data["scenario"]).resolve()
        bounds = data["bounds"]
        ids = tuple(data["point_ids"])
        weights = data.get("objective_weights", {})
        config = LayoutConfig(
            scenario=load_yard_scenario(scenario_path),
            point_ids=ids,
            x_bounds=tuple(bounds["x"]),
            y_bounds=tuple(bounds["y"]),
            min_spacing=float(data.get("min_spacing", 0.0)),
            objective_weights=(
                float(weights.get("starvation", 1.0)),
                float(weights.get("truck_waiting", 1.0)),
                float(weights.get("loader_travel", 1.0)),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid layout config: {exc}") from exc
    ids_in_scenario = {pile.id for pile in config.scenario.piles}
    if not config.point_ids or len(set(config.point_ids)) != len(config.point_ids):
        raise ValueError("point_ids must be non-empty and unique")
    if not set(config.point_ids) <= ids_in_scenario:
        raise ValueError("Every point_id must name a pile in the scenario")
    for name, limits in (("x", config.x_bounds), ("y", config.y_bounds)):
        if len(limits) != 2 or limits[0] >= limits[1]:
            raise ValueError(f"{name} bounds must be an increasing pair")
    if config.min_spacing < 0:
        raise ValueError("min_spacing must be non-negative")
    return config
