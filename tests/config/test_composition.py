"""Independent inputs, cross-file validation, provenance, and reusable composition."""

from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from mill_yard.config.loader import (
    ScenarioFiles,
    load_initial_conditions,
    load_scenario_files,
    load_truck_schedule,
    load_yard_design,
    load_yard_scenario,
    select_scenario_files,
)
from mill_yard.gui.configuration import ConfigurationDraft
from mill_yard.model.configuration import (
    InitialConditions,
    ScheduledArrival,
    TruckSchedule,
    compose_scenario,
)
from mill_yard.policies.fifo import FifoPolicy
from mill_yard.simulation.engine import run_yard

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def parts(tmp_path):
    original = load_scenario_files(
        ROOT / "configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml"
    )
    paths = {}
    for key in ("yard", "truck_schedule", "initial_conditions"):
        path = tmp_path / f"{key}.yaml"
        path.write_bytes(getattr(original, key).read_bytes())
        paths[key] = path
    return ScenarioFiles(**paths)


def edit(path, change):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_manifest_paths_are_relative_to_manifest_not_working_directory(tmp_path, monkeypatch):
    path = ROOT / "configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml"
    expected = load_yard_scenario(path)
    monkeypatch.chdir(tmp_path)
    assert load_yard_scenario(path) == expected
    assert len(expected.configuration_sources) == 4
    for source in expected.configuration_sources:
        assert Path(source.path).is_absolute()
        assert source.sha256 == sha256(Path(source.path).read_bytes()).hexdigest()


def test_three_files_and_pure_python_composition_match_preset(parts):
    yard = load_yard_design(parts.yard)
    schedule = load_truck_schedule(parts.truck_schedule)
    initial = load_initial_conditions(parts.initial_conditions)
    expected = load_yard_scenario(
        ROOT / "configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml"
    )
    assert compose_scenario(yard, schedule, initial) == expected
    assert parts.load() == expected
    before = (asdict(yard), asdict(schedule), asdict(initial))
    run_yard(
        compose_scenario(yard, schedule, initial), FifoPolicy(), record="summary", validate=True
    )
    assert before == (asdict(yard), asdict(schedule), asdict(initial))


def test_schedule_owns_horizon_and_overrides_do_not_mutate_it(parts):
    assert parts.load().horizon == 180
    assert parts.load(horizon=20).horizon == 20
    assert replace(parts, horizon=30).load(horizon=40).horizon == 40
    assert load_truck_schedule(parts.truck_schedule).horizon == 180


def test_multi_area_schedule_requires_explicit_routes(parts):
    edit(parts.truck_schedule, lambda data: data["arrivals"][0].pop("area_id"))
    with pytest.raises(ValueError, match="must specify area_id"):
        parts.load()


def test_single_named_area_can_resolve_an_omitted_route(parts):
    yard = load_yard_design(parts.yard)
    yard = replace(
        yard,
        stops=tuple(x for x in yard.stops if x.area_id == "north"),
        unloading_areas=(yard.unloading_areas[0],),
    )
    initial = replace(load_initial_conditions(parts.initial_conditions), piles=())
    schedule = TruckSchedule(10, (ScheduledArrival(0, 5, "t"),))
    resolved = compose_scenario(yard, schedule, initial)
    assert resolved.arrivals[0].area_id == "north"
    assert all(p.initial == 0 for p in resolved.piles)


def test_unknown_area_is_not_silently_rerouted(parts):
    edit(parts.truck_schedule, lambda data: data["arrivals"][0].update(area_id="missing"))
    with pytest.raises(ValueError, match="unknown unloading area 'missing'"):
        parts.load()


@pytest.mark.parametrize("group", ["piles", "infeeds", "loaders"])
def test_unknown_initial_asset_is_rejected(parts, group):
    edit(parts.initial_conditions, lambda data: data[group][0].update(id="unknown"))
    with pytest.raises(ValueError, match=r"unknown .* IDs"):
        parts.load()


def test_loader_positions_are_required_but_stock_defaults_to_zero(parts):
    initial = load_initial_conditions(parts.initial_conditions)
    yard = load_yard_design(parts.yard)
    schedule = load_truck_schedule(parts.truck_schedule)
    with pytest.raises(ValueError, match="starting positions"):
        compose_scenario(yard, schedule, InitialConditions())
    resolved = compose_scenario(yard, schedule, InitialConditions(loaders=initial.loaders))
    assert all(x.initial == 0 for x in (*resolved.piles, *resolved.infeeds))


def test_over_capacity_initial_stock_is_rejected_not_clipped(parts):
    edit(parts.initial_conditions, lambda data: data["piles"][0].update(amount=10000))
    with pytest.raises(ValueError, match="exceeds capacity"):
        parts.load()


@pytest.mark.parametrize(
    ("part", "change", "message"),
    [
        ("yard", lambda d: d["piles"][0].update(initial=1), "Invalid yard"),
        ("yard", lambda d: d["loaders"][0].update(location={"x": 0, "y": 0}), "Invalid yard"),
        ("truck_schedule", lambda d: d.update(horizon=True), "horizon"),
        ("truck_schedule", lambda d: d["arrivals"][0].update(quantity=-1), "quantity"),
        (
            "truck_schedule",
            lambda d: d["arrivals"][0].update(species="spruce"),
            "Invalid truck schedule",
        ),
        ("initial_conditions", lambda d: d["loaders"][0]["location"].update(x=True), "coordinates"),
        ("initial_conditions", lambda d: d["piles"][0].update(amount=-1), "inventory"),
        ("initial_conditions", lambda d: d["piles"].append(dict(d["piles"][0])), "unique"),
    ],
)
def test_individual_schema_validation(parts, part, change, message):
    edit(getattr(parts, part), change)
    with pytest.raises(ValueError, match=message):
        parts.load()


@pytest.mark.parametrize("version", [True, 2, "1"])
def test_input_schema_version_is_strict(parts, version):
    edit(parts.yard, lambda d: d.update(schema_version=version))
    with pytest.raises(ValueError, match="schema_version"):
        parts.load()


def test_duplicate_yaml_fields_are_rejected(parts):
    with parts.truck_schedule.open("a", encoding="utf-8") as stream:
        stream.write("\nhorizon: 20\n")
    with pytest.raises(ValueError, match="Duplicate YAML key"):
        parts.load()


def test_wrong_component_kind_is_rejected(parts):
    with pytest.raises(ValueError, match="expected kind: truck_schedule"):
        replace(parts, truck_schedule=parts.yard).load()


def test_future_arrivals_are_retained_and_horizon_arrival_is_inclusive(parts):
    yard = load_yard_design(parts.yard)
    initial = load_initial_conditions(parts.initial_conditions)
    schedule = TruckSchedule(
        5, (ScheduledArrival(5, 10, "at", "north"), ScheduledArrival(6, 10, "after", "south"))
    )
    scenario = compose_scenario(yard, schedule, initial)
    result = run_yard(scenario, FifoPolicy(), record="events", validate=True)
    assert len(scenario.arrivals) == 2
    assert set(result.state.trucks) == {"at"}
    assert not any(e["event"] == "decision" and e["time"] == 5 for e in result.events)


def test_all_experiment_presets_compose_with_valid_source_roles():
    paths = sorted((ROOT / "configs/experiments").rglob("*.yaml"))
    assert paths
    for path in paths:
        resolved = load_yard_scenario(path)
        assert {x.kind for x in resolved.configuration_sources} == {
            "experiment",
            "yard",
            "truck_schedule",
            "initial_conditions",
        }


def test_traffic_schedules_are_shared_between_fleet_sizes():
    one = load_scenario_files(
        ROOT / "configs/experiments/traffic/1area_shared__045trucks_1loader_180min.yaml"
    )
    two = load_scenario_files(
        ROOT / "configs/experiments/traffic/1area_shared__045trucks_2loaders_180min.yaml"
    )
    assert one.truck_schedule == two.truck_schedule
    assert one.yard != two.yard


def test_draft_invalid_combination_is_staged_and_recovers(parts):
    draft = ConfigurationDraft.from_files(parts)
    assert draft.validate()[0]
    previous = draft.files()
    draft.choose(
        "truck_schedule", ROOT / "configs/archive/legacy_single_bay_14h/truck_schedule.yaml"
    )
    valid, message = draft.validate()
    assert not valid
    assert "unknown unloading area" in message
    assert previous.load().horizon == 180
    draft.choose("truck_schedule", parts.truck_schedule)
    draft.horizon = "10"
    assert draft.validate()[0]
    assert draft.files().load().horizon == 10
    draft.horizon = "nan"
    assert not draft.validate()[0]


def test_direct_selection_requires_three_inputs_and_rejects_ambiguous_mix(parts):
    with pytest.raises(ValueError, match="all three"):
        select_scenario_files(yard=parts.yard)
    with pytest.raises(ValueError, match="Choose an experiment"):
        select_scenario_files(
            ROOT / "configs/archive/legacy_single_bay_14h/experiment.yaml", yard=parts.yard
        )


@pytest.mark.parametrize(
    "content",
    [
        "kind: experiment\nschema_version: 1\nyard: a.yaml\n",
        "kind: experiment\nschema_version: 1\nyard: a\ntruck_schedule: b\ninitial_conditions: c\nunknown: 1",
        "kind: experiment\nschema_version: true\nyard: a\ntruck_schedule: b\ninitial_conditions: c",
    ],
)
def test_invalid_experiment_descriptors_are_rejected(tmp_path, content):
    path = tmp_path / "experiment.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=r"experiment|schema_version"):
        load_scenario_files(path)
