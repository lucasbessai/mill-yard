"""Load and validate a YAML scenario for the initial yard model."""

from pathlib import Path

import yaml

from mill_yard.model.types import Arrival, Point
from mill_yard.model.yard import InfeedSpec, LoaderSpec, PileSpec, StopSpec, YardScenario


def load_yard_scenario(path: str | Path) -> YardScenario:
    """Load a multi-location YAML scenario and reject unknown fields."""
    with Path(path).open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError("Scenario must be a YAML mapping")
    try:
        data["arrivals"] = tuple(Arrival(**item) for item in data["arrivals"])
        for key, cls in (
            ("stops", StopSpec),
            ("piles", PileSpec),
            ("infeeds", InfeedSpec),
            ("loaders", LoaderSpec),
        ):
            entries = []
            for item in data[key]:
                fields = dict(item)
                fields["location"] = Point(**fields["location"])
                entries.append(cls(**fields))
            data[key] = tuple(entries)
        return YardScenario(**data)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Invalid scenario: {exc}") from exc
