"""Scenario boundary checks before any simulation starts."""

from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from mill_yard.config.loader import load_yard_scenario


@pytest.mark.parametrize("mistake", ["unknown", "boolean_coordinate", "boolean_threshold"])
def test_invalid_yaml_settings_are_rejected(tmp_path, mistake):
    source = (
        Path(__file__).resolve().parents[2]
        / "configs"
        / "archive"
        / "legacy_single_bay_14h"
        / "experiment.yaml"
    )
    data = asdict(load_yard_scenario(source))
    if mistake == "unknown":
        data["unknown_setting"] = 1
    elif mistake == "boolean_coordinate":
        data["stops"][0]["location"]["x"] = True
    else:
        data["infeeds"][0]["low"] = True
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match=r"Invalid|Coordinates"):
        load_yard_scenario(path)


def test_unloading_area_yaml_roundtrip(multi_area_yard, tmp_path):
    from dataclasses import asdict

    import yaml

    from mill_yard.config.loader import load_yard_scenario

    path = tmp_path / "areas.yaml"
    path.write_text(yaml.safe_dump(asdict(multi_area_yard)), encoding="utf-8")
    assert load_yard_scenario(path) == multi_area_yard
