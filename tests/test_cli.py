"""CLI validation and integration with streamed run artifacts."""

import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from mill_yard.cli import main


def test_scored_options_cannot_be_silently_ignored(capsys):
    with pytest.raises(SystemExit) as error:
        main(["run", "unused.yaml", "--policy", "threshold", "--truck-weight", "3"])
    assert error.value.code == 2
    assert "require --policy scored" in capsys.readouterr().err


def test_cli_streams_saved_scored_run(tmp_path, monkeypatch, capsys):
    scenario = (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "archive"
        / "legacy_single_bay_14h"
        / "experiment.yaml"
    )
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "run",
                str(scenario),
                "--policy",
                "scored",
                "--record",
                "events",
                "--save",
                "--validate",
                "--lookahead",
                "25",
                "--assignment-penalty",
                "0.2",
            ]
        )
        == 0
    )
    directory = next((tmp_path / "runs").iterdir())
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["policy_parameters"]["lookahead"] == 25
    assert metadata["policy_parameters"]["assignment_penalty"] == 0.2
    assert metadata["schema_version"] == 4
    assert (directory / "events.jsonl").stat().st_size > 0
    assert "Saved run" in capsys.readouterr().out


def test_gui_command_routes_to_the_optional_desktop_adapter(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "mill_yard.gui.app",
        SimpleNamespace(launch=lambda path, **options: calls.append((path, options))),
    )
    assert main(["gui", "example.yaml", "--human", "--speed", "2"]) == 0
    assert calls[0][1]["policy"] == "human"
    assert calls[0][1]["speed"] == 2


def test_gui_command_can_select_arcade_without_importing_tk(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "mill_yard.gui.app", None)
    monkeypatch.setitem(
        sys.modules,
        "mill_yard.gui.arcade_app",
        SimpleNamespace(launch=lambda path, **options: calls.append((path, options))),
    )
    assert main(["gui", "example.yaml", "--backend", "arcade", "--human"]) == 0
    assert calls[0][0] == "example.yaml"
    assert calls[0][1]["policy"] == "human"


def test_missing_arcade_extra_has_an_actionable_error(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mill_yard.gui.arcade_app", None)
    with pytest.raises(SystemExit) as error:
        main(["gui", "example.yaml", "--backend", "arcade"])
    assert error.value.code == 2
    assert "uv sync --extra gui" in capsys.readouterr().err


def test_cli_three_inputs_record_effective_horizon_and_source_hashes(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "run",
                "--yard",
                str(root / "configs/yards/2areas_uneven__3bays_2piles_1mill_2loaders_mixed.yaml"),
                "--truck-schedule",
                str(
                    root
                    / "configs/truck_schedules/2areas_uneven__012trucks_north_heavy_180min.yaml"
                ),
                "--initial-conditions",
                str(
                    root
                    / "configs/initial_conditions/2areas_uneven__3bays_2piles_1mill_2loaders_mixed__partial_stock.yaml"
                ),
                "--horizon",
                "5",
                "--policy",
                "fifo",
                "--save",
                "--validate",
            ]
        )
        == 0
    )
    metadata = json.loads(next((tmp_path / "runs").glob("*/metadata.json")).read_text())
    assert metadata["scenario"]["horizon"] == 5
    assert "configuration_sources" not in metadata["scenario"]
    assert {s["kind"] for s in metadata["configuration_sources"]} == {
        "yard",
        "truck_schedule",
        "initial_conditions",
    }
    assert all(len(s["sha256"]) == 64 for s in metadata["configuration_sources"])


def test_gui_cli_passes_independent_inputs_to_desktop_adapter(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "mill_yard.gui.app",
        SimpleNamespace(launch=lambda path, **options: calls.append((path, options))),
    )
    assert (
        main(
            [
                "gui",
                "--yard",
                "yard.yaml",
                "--truck-schedule",
                "trucks.yaml",
                "--initial-conditions",
                "start.yaml",
                "--horizon",
                "20",
            ]
        )
        == 0
    )
    assert calls[0][0] is None
    assert calls[0][1]["yard"] == "yard.yaml"
    assert calls[0][1]["truck_schedule"] == "trucks.yaml"
    assert calls[0][1]["initial_conditions"] == "start.yaml"
    assert calls[0][1]["horizon"] == 20


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--yard", "yard.yaml"],
        ["preset.yaml", "--yard", "yard.yaml"],
        ["preset.yaml", "--horizon", "nan"],
        ["preset.yaml", "--horizon", "-1"],
    ],
)
def test_cli_rejects_incomplete_or_ambiguous_input_selections(arguments):
    with pytest.raises(SystemExit) as error:
        main(["run", *arguments])
    assert error.value.code == 2


def test_cli_human_mode_accepts_terminal_decisions(tiny_yard, tmp_path, monkeypatch):
    path = tmp_path / "human.yaml"
    path.write_text(yaml.safe_dump(asdict(tiny_yard)), encoding="utf-8")
    commands = iter(["1", "1", "done"])
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    assert main(["run", str(path), "--policy", "human", "--record", "summary", "--validate"]) == 0


def test_cli_feature_policy_records_config_and_explicit_weight_overrides(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "run",
                str(root / "configs/archive/legacy_single_bay_14h/experiment.yaml"),
                "--policy",
                "feature-scored",
                "--save",
                "--feature-config",
                str(root / "configs/policies/feature_scored__v4_4weights.yaml"),
                "--feature-weights",
                "0.2",
                "0.5",
                "0.1",
                "0.2",
                "--validate",
            ]
        )
        == 0
    )
    metadata = json.loads(next((tmp_path / "runs").glob("*/metadata.json")).read_text())
    assert metadata["policy"] == "FeatureScoredPolicy"
    assert metadata["policy_parameters"]["weights"] == {
        "truck": 0.2,
        "mill": 0.5,
        "inventory": 0.1,
        "cost": 0.2,
        "bay_release": 0.0,
    }
    assert metadata["policy_parameters"]["features"]["wait_reference"] == 30


def test_cli_five_feature_weights_and_snapshot_metadata(tmp_path, monkeypatch, tiny_yard):
    path = tmp_path / "yard.yaml"
    path.write_text(yaml.safe_dump(asdict(tiny_yard)), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "run",
                str(path),
                "--policy",
                "feature-scored",
                "--feature-weights",
                "0.2",
                "0.35",
                "0.1",
                "0.15",
                "0.2",
                "--save",
                "--validate",
            ]
        )
        == 0
    )
    run = next((tmp_path / "runs").iterdir())
    parameters = json.loads((run / "metadata.json").read_text())["policy_parameters"]
    assert parameters["feature_version"] == 4
    assert parameters["weights"]["bay_release"] == 0.2
    assert parameters["features"]["bay_time_reference"] == 30
    assert parameters["features"]["truck_area_wait_share"] == 0.2
    assert parameters["features"]["area_wait_reference"] == 30
    assert any(
        json.loads(row)["event"] == "decision"
        for row in (run / "events.jsonl").read_text().splitlines()
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["--feature-version", "2", "--feature-weights", "0", "0", "0", "0", "1"],
        ["--feature-weights", "0.5", "0.5"],
        ["--feature-weights", "0.2", "0.2", "0.2", "0.2", "-0.1"],
    ],
)
def test_cli_rejects_invalid_five_feature_settings(arguments):
    with pytest.raises(SystemExit) as error:
        main(["run", "unused.yaml", "--policy", "feature-scored", *arguments])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["--policy", "scored", "--feature-weights", "0", "1", "0", "0"],
        ["--policy", "feature-scored", "--feature-weights", "1", "1", "1", "1"],
        ["--policy", "feature-scored", "--truck-weight", "1"],
    ],
)
def test_cli_rejects_wrong_policy_flags_and_invalid_simplex(arguments, capsys):
    with pytest.raises(SystemExit) as error:
        main(["run", "unused.yaml", *arguments])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "require" in message or "sum to one" in message


def test_fifo_cli_saves_effective_baseline_settings(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    config = tmp_path / "fifo.yaml"
    config.write_text("target_fraction: 0.60\nbuffer_cycles: 1.5\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "run",
                str(root / "configs/archive/legacy_single_bay_14h/experiment.yaml"),
                "--policy",
                "fifo",
                "--fifo-config",
                str(config),
                "--record",
                "summary",
                "--save",
                "--validate",
            ]
        )
        == 0
    )
    metadata = json.loads(next((tmp_path / "runs").glob("*/metadata.json")).read_text())
    assert metadata["policy"] == "FifoPolicy"
    assert metadata["policy_parameters"]["target_fraction"] == 0.6
    assert metadata["policy_parameters"]["buffer_cycles"] == 1.5


def test_fifo_options_require_fifo_policy(capsys):
    with pytest.raises(SystemExit) as error:
        main(["run", "unused.yaml", "--fifo-config", "settings.yaml"])
    assert error.value.code == 2
    assert "require --policy fifo" in capsys.readouterr().err


@pytest.mark.parametrize("policy", ["empty-queue-first", "longest-queue-first"])
def test_area_queue_policy_cli_saves_v4_membership_and_shared_settings(
    tmp_path, monkeypatch, policy
):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "run",
                str(
                    root
                    / "configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml"
                ),
                "--policy",
                policy,
                "--fifo-config",
                str(root / "configs/policies/fifo.yaml"),
                "--save",
                "--record",
                "snapshots",
                "--validate",
            ]
        )
        == 0
    )
    directory = next((tmp_path / "runs").iterdir())
    metadata = json.loads((directory / "metadata.json").read_text())
    metrics = json.loads((directory / "summary.json").read_text())
    assert metadata["schema_version"] == 4
    assert metadata["seed"] is None
    assert metadata["policy_parameters"]["queue_rule"] == policy.replace("-", "_")
    assert set(metrics["queues"]) == {"north", "south"}
    assert set(metrics["unloading_areas"]) == {"north", "south"}
