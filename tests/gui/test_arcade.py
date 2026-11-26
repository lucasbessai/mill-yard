"""Exercise actual Arcade drawing and mouse callbacks when OpenGL is available."""

import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def arcade_window(tiny_yard, tmp_path, monkeypatch):
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("No desktop display")
    pytest.importorskip("arcade")
    from pyglet.gl import ContextException

    from mill_yard.gui.arcade_app import ArcadeYardWindow

    path = tmp_path / "yard.yaml"
    path.write_text(yaml.safe_dump(asdict(tiny_yard)), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    try:
        window = ArcadeYardWindow(path, policy="human", validate=True, visible=False)
    except ContextException as exc:
        pytest.skip(f"No OpenGL desktop: {exc}")
    try:
        window.switch_to()
        window.on_draw()
        yield window
    finally:
        window.on_close()


def click(window, rect):
    import arcade

    window.on_mouse_press(
        rect.x + rect.width / 2, rect.y + rect.height / 2, arcade.MOUSE_BUTTON_LEFT, 0
    )
    window.on_mouse_release(
        rect.x + rect.width / 2, rect.y + rect.height / 2, arcade.MOUSE_BUTTON_LEFT, 0
    )


def choose_first_task(window, loader_id):
    click(window, window.yard.flags[loader_id])
    window.on_draw()
    assert window.selected_loader == loader_id
    assert all(a.loader_id == loader_id for _, a in window.task_hits)
    click(window, window.task_hits[0][0])
    window.on_draw()


def test_flags_build_joint_draft_then_commit_to_simpy(arcade_window):
    import arcade

    w = arcade_window
    assert set(w.yard.flags) == {"l1", "l2"}
    w.on_update(60)
    assert w.session.simulation.state.time == 0
    choose_first_task(w, "l1")
    assert "l1" not in w.yard.flags
    assert "l2" in w.yard.flags
    assert w.session.simulation.state.trucks["truck"].reserved_quantity == 0
    choose_first_task(w, "l2")
    assert not w.yard.flags
    assert len(w.session.decision.assignments) == 2
    # Save excludes draft; committing uses the exact same engine API as Tk.
    w.save()
    saved = next((w.scenario_path.parent / "runs").iterdir())
    assert json.loads((saved / "summary.json").read_text())["active_loader_jobs"] == 0
    w.on_key_press(arcade.key.ENTER, 0)
    w.on_update(10)
    w.on_draw()
    assert w.session.simulation.finished
    assert len(w.session.simulation.completed_trucks) == 1
    assert "20.0 m" in w.drawing.labels["metric-travel"].text


def test_flags_reappear_after_undo_and_quantity_editing_is_validated(arcade_window):
    w = arcade_window
    click(w, w.quantity_rect)
    w.on_text("7")  # Exceeds this loader's capacity of 5 t.
    choose_first_task(w, "l1")
    assert not w.session.decision.assignments
    assert w.session.simulation.state.time == 0
    w.quantity = "3"
    click(w, w.task_hits[0][0])
    w.on_draw()
    assert w.session.decision.assignments[0].quantity == 3
    assert "l1" not in w.yard.flags
    w.undo()
    w.on_draw()
    assert set(w.yard.flags) == {"l1", "l2"}


def test_map_zoom_hit_testing_pan_and_event_scrolling(arcade_window):
    import arcade

    w = arcade_window
    w.on_mouse_scroll(400, 400, 0, 3)
    assert w.camera.zoom > 1
    w.on_draw()
    choose_first_task(w, "l1")  # Flags remain clickable after zoom.
    previous = w.camera.center
    w.on_mouse_drag(400, 400, 25, -30, arcade.MOUSE_BUTTON_RIGHT, 0)
    assert w.camera.center != previous
    w.fit()
    assert w.camera.zoom == 1
    w.commit(wait=True)
    # Human wait decisions permit later events; leave every subsequent decision idle.
    for _ in range(20):
        if w.session.decision is not None:
            w.commit(wait=True)
        w.session.step()
        if w.session.simulation.finished:
            break
    w.on_draw()
    events = w.session.simulation.recorder.events
    assert w.events_scroll.count == len(events)
    rect = w.events_rect
    w.on_mouse_scroll(rect.x + 20, rect.y + 20, 0, 1000)
    assert w.events_scroll.offset == 0
    w.on_draw()
    assert "#1 |" in w.drawing.labels["event:0:title"].text
    w.on_key_press(arcade.key.END, 0)
    assert w.events_scroll.offset == w.events_scroll.maximum
    w.restart()
    w.on_draw()
    assert set(w.yard.flags) == {"l1", "l2"}
    assert w.events_scroll.count == len(w.session.simulation.recorder.events)


def test_automated_policy_does_not_require_flags_or_operator_input(arcade_window):
    w = arcade_window
    w.policy_name = "threshold"
    w.restart()
    w.on_draw()
    assert not w.yard.flags
    assert w.session.decision is None
    w.toggle_play()
    w.on_update(10)
    w.on_draw()
    assert w.session.simulation.finished
    assert w.drawing.labels["metric-wait"].text.startswith("Total truck wait:")
    assert w.drawing.labels["metric-starvation"].text.startswith("Total mill starvation:")


def test_summary_recording_and_policy_change_can_restart_without_history(arcade_window):
    w = arcade_window
    w.session.record = "summary"
    w.set_policy("threshold")
    w.on_draw()
    assert w.policy_name == "threshold"
    assert not w.yard.flags
    assert w.events_scroll.count == 0
    assert "No events retained" in w.drawing.labels["no-events"].text
    w.toggle_play()
    w.on_update(10)
    w.on_draw()
    assert w.session.simulation.finished


def test_map_and_panel_can_build_one_joint_decision(arcade_window):
    w = arcade_window
    click(w, w.yard.loaders["l1"])
    w.on_draw()
    assert w.selected_loader == "l1"
    assert w.yard.pickups == {("truck", "truck")}
    assert not w.yard.deposits
    click(w, w.yard.targets[("truck", "truck")])
    w.on_draw()
    assert w.selected_pickup == ("truck", "truck")
    assert w.yard.deposits == {("pile", "pile")}
    assert not w.session.decision.assignments
    click(w, w.yard.targets[("pile", "pile")])
    w.on_draw()
    assert w.session.decision.assignments[0].loader_id == "l1"
    assert w.selected_pickup is None
    # Choose the other loader and its entire task using only panel controls.
    click(w, w.loader_rect)
    w.on_draw()
    row = next(rect for rect, name in w.menu_rows if name == "l2")
    click(w, row)
    w.on_draw()
    assert w.selected_loader == "l2"
    click(w, w.task_hits[0][0])
    assert len(w.session.decision.assignments) == 2
    assert w.session.simulation.state.time == 0
    assert w.session.simulation.state.trucks["truck"].reserved_quantity == 0
    w.commit()
    w.on_update(10)
    assert w.session.simulation.finished


def test_map_selection_handles_pile_to_infeed_after_zoom_and_pan(arcade_window, tiny_yard):
    import arcade

    w = arcade_window
    scenario = replace(
        tiny_yard,
        piles=(replace(tiny_yard.piles[0], initial=10),),
        infeeds=(replace(tiny_yard.infeeds[0], initial=0),),
    )
    path = w.scenario_path.parent / "stocked.yaml"
    path.write_text(yaml.safe_dump(asdict(scenario)), encoding="utf-8")
    w.load_scenario(path)
    w.zoom(1.25)
    w.camera.pan(-30, 20)
    w.on_draw()
    click(w, w.yard.loaders["l1"])
    w.on_draw()
    assert ("pile", "pile") in w.yard.pickups
    click(w, w.yard.targets[("pile", "pile")])
    w.on_draw()
    assert w.yard.deposits == {("infeed", "mill")}
    w.on_key_press(arcade.key.ESCAPE, 0)
    w.on_draw()
    assert w.selected_loader == "l1"
    assert w.selected_pickup is None
    click(w, w.yard.targets[("pile", "pile")])
    w.on_draw()
    click(w, w.yard.targets[("infeed", "mill")])
    assert w.session.decision.assignments[0].source_kind == "pile"
    assert w.session.decision.assignments[0].destination_kind == "infeed"


def test_policy_dropdown_selects_directly_and_cancel_preserves_run(arcade_window):
    import arcade

    w = arcade_window
    click(w, w.policy_rect)
    w.on_draw()
    assert {name for _, name in w.menu_rows} == {
        "human",
        "random",
        "scored",
        "threshold",
        "feature-scored",
        "fifo",
        "empty-queue-first",
        "longest-queue-first",
    }
    click(w, next(rect for rect, name in w.menu_rows if name == "scored"))
    w.on_draw()
    assert w.policy_name == "scored"
    assert w.session.simulation.state.time == 0
    assert w.session.decision is None
    w.toggle_play()
    current_sim = w.session.simulation
    click(w, w.policy_rect)
    w.on_draw()
    w.on_update(10)
    assert w.session.simulation.state.time == 0
    w.on_key_press(arcade.key.ESCAPE, 0)
    assert w.session.playing
    assert w.session.simulation is current_sim
    click(w, w.policy_rect)
    w.on_draw()
    w.on_key_press(arcade.key.DOWN, 0)  # scored -> human
    w.on_key_press(arcade.key.ENTER, 0)
    assert w.policy_name == "human"
    assert w.session.decision is not None


def test_open_config_dialog_browses_yaml_and_resets_the_yard(arcade_window, tiny_yard):
    w = arcade_window
    folder = w.scenario_path.parent / "more"
    folder.mkdir()
    alternative = replace(tiny_yard, horizon=20, loaders=(tiny_yard.loaders[0],))
    path = folder / "other.yml"
    path.write_text(yaml.safe_dump(asdict(alternative)), encoding="utf-8")
    (folder / "ignore.txt").write_text("not a scenario", encoding="utf-8")
    w.zoom(2)
    w.quantity = "3"
    click(w, next(rect for key, rect, _, _ in w.buttons if key == "open"))
    w.on_draw()
    dialog = w.scenario_dialog
    click(w, next(rect for rect, entry, _ in dialog.rows if entry == folder))
    w.on_draw()
    assert [entry.name for entry, _ in dialog.entries] == ["other.yml"]
    click(w, dialog.rows[0][0])
    click(w, dialog.open_rect)
    w.on_draw()
    assert w.scenario_dialog is None
    assert w.scenario_path == path
    assert w.scenario.horizon == 20
    assert set(w.yard.loaders) == {"l1"}
    assert w.camera.zoom == 1
    assert w.quantity == ""
    assert w.policy_name == "human"


def test_invalid_config_does_not_replace_current_run_and_cancel_resumes(arcade_window):
    import arcade

    w = arcade_window
    w.set_policy("threshold")
    w.toggle_play()
    sim, scenario, path = w.session.simulation, w.scenario, w.scenario_path
    invalid = path.parent / "invalid.yaml"
    invalid.write_text("not: [valid", encoding="utf-8")
    w.open_scenario()
    w.on_draw()
    w.on_text(str(invalid))
    w.on_key_press(arcade.key.ENTER, 0)
    w.on_draw()
    assert w.scenario_dialog.error
    assert w.session.simulation is sim
    assert w.scenario is scenario
    assert w.scenario_path == path
    w.on_update(10)
    assert sim.state.time == 0
    w.on_key_press(arcade.key.ESCAPE, 0)
    assert w.scenario_dialog is None
    assert w.session.playing


def test_dragging_over_a_deposit_target_does_not_assign_work(arcade_window):
    import arcade

    w = arcade_window
    click(w, w.yard.loaders["l1"])
    w.on_draw()
    click(w, w.yard.targets[("truck", "truck")])
    w.on_draw()
    target = w.yard.targets[("pile", "pile")]
    x, y = target.x + target.width / 2, target.y + target.height / 2
    before = w.camera.center
    w.on_mouse_press(x, y, arcade.MOUSE_BUTTON_LEFT, 0)
    w.on_mouse_drag(x + 25, y + 10, 25, 10, arcade.MOUSE_BUTTON_LEFT, 0)
    w.on_mouse_release(x + 25, y + 10, arcade.MOUSE_BUTTON_LEFT, 0)
    assert not w.session.decision.assignments
    assert w.selected_pickup == ("truck", "truck")
    assert w.camera.center != before


@pytest.mark.parametrize(
    ("kind", "count"), [("truck_wait", 1), ("loader_travel", 2), ("mill_starvation", 1)]
)
def test_clickable_totals_show_individual_metrics_and_resume_playback(arcade_window, kind, count):
    import arcade

    w = arcade_window
    w.set_policy("threshold")
    w.toggle_play()
    w.on_update(0.3)  # Includes half of each current loaded-travel leg.
    w.on_draw()
    before = asdict(w.session.simulation.state)
    display_time = w.session.display_time
    click(w, w.metric_hits[kind])
    w.on_draw()
    dialog = w.metric_dialog
    assert dialog.kind == kind
    assert len(dialog.rows) == count
    assert dialog.time == display_time
    if kind == "truck_wait":
        assert dialog.rows[0]["status"] == "completed"
    elif kind == "loader_travel":
        assert sum(row["travel_distance"] for row in dialog.rows) == pytest.approx(10)
    w.on_update(60)
    w.on_key_press(arcade.key.SPACE, 0)
    w.on_key_press(arcade.key.RIGHT, 0)
    assert asdict(w.session.simulation.state) == before
    assert w.session.display_time == display_time
    w.on_key_press(arcade.key.ESCAPE, 0)
    assert w.metric_dialog is None
    assert w.session.playing


def test_starvation_label_projects_time_and_disappears_after_refill(arcade_window, tiny_yard):
    import arcade

    w = arcade_window
    scenario = replace(
        tiny_yard,
        arrivals=(),
        piles=(replace(tiny_yard.piles[0], initial=10),),
        infeeds=(replace(tiny_yard.infeeds[0], initial=0, consumption_rate=1),),
    )
    path = w.scenario_path.parent / "starved.yaml"
    path.write_text(yaml.safe_dump(asdict(scenario)), encoding="utf-8")
    w.load_scenario(path)
    w.set_policy("threshold")
    w.toggle_play()
    w.on_update(0.1)
    w.on_draw()
    assert w.yard.labels["starvation:mill"].text == "Starved total 0.5 min"
    click(w, w.metric_hits["mill_starvation"])
    w.on_draw()
    assert w.metric_dialog.rows == [{"id": "mill", "status": "starved", "starvation": 0.5}]
    w.on_key_press(arcade.key.ESCAPE, 0)
    w.on_update(1)
    w.on_draw()
    assert "starvation:mill" not in w.yard.labels
    click(w, w.metric_hits["mill_starvation"])
    w.on_draw()
    assert w.metric_dialog.rows[0]["status"] == "running"
    # Target 8 t assigns 5 t + 3 t; the smaller job deposits first at t=3.2.
    assert w.metric_dialog.rows[0]["starvation"] == pytest.approx(3.2)


def test_metric_dialog_preserves_human_draft_and_enter_does_not_commit_it(arcade_window):
    import arcade

    w = arcade_window
    choose_first_task(w, "l1")
    draft = w.session.decision
    assignments = list(draft.assignments)
    click(w, w.metric_hits["truck_wait"])
    w.on_draw()
    w.on_key_press(arcade.key.ENTER, 0)
    assert w.metric_dialog is None
    assert w.session.decision is draft
    assert draft.assignments == assignments
    assert not w.session.playing
    assert w.session.simulation.state.time == 0


def test_truck_breakdown_scrolls_all_rows_and_can_close_with_mouse(arcade_window, tiny_yard):
    import arcade

    from mill_yard.model.types import Arrival

    w = arcade_window
    scenario = replace(tiny_yard, arrivals=tuple(Arrival(0, 10, f"truck-{i}") for i in range(30)))
    path = w.scenario_path.parent / "queue.yaml"
    path.write_text(yaml.safe_dump(asdict(scenario)), encoding="utf-8")
    w.load_scenario(path)
    w.on_draw()
    click(w, w.metric_hits["truck_wait"])
    w.on_draw()
    dialog = w.metric_dialog
    assert len(dialog.rows) == 30
    assert sum(row["status"] == "queued" for row in dialog.rows) == 29
    w.on_mouse_scroll(400, 400, 0, -100)
    w.on_draw()
    assert dialog.scroll.offset == dialog.scroll.maximum
    w.on_key_press(arcade.key.HOME, 0)
    assert dialog.scroll.offset == 0
    w.on_key_press(arcade.key.PAGEDOWN, 0)
    assert dialog.scroll.offset == dialog.scroll.capacity
    track = dialog.track_rect
    w.on_mouse_press(track.x + 5, track.y + 5, arcade.MOUSE_BUTTON_LEFT, 0)
    w.on_mouse_drag(track.x + 5, track.y + track.height, 0, 100, arcade.MOUSE_BUTTON_LEFT, 0)
    w.on_mouse_release(track.x + 5, track.y + track.height, arcade.MOUSE_BUTTON_LEFT, 0)
    assert dialog.scroll.offset == 0
    click(w, dialog.close_rect)
    assert w.metric_dialog is None
    assert w.session.decision is not None


def test_empty_truck_breakdown_and_empty_inactive_mill(arcade_window, tiny_yard):
    w = arcade_window
    scenario = replace(tiny_yard, arrivals=(), infeeds=(replace(tiny_yard.infeeds[0], initial=0),))
    path = w.scenario_path.parent / "empty.yaml"
    path.write_text(yaml.safe_dump(asdict(scenario)), encoding="utf-8")
    w.load_scenario(path)
    w.on_draw()
    assert "starvation:mill" not in w.yard.labels
    click(w, w.metric_hits["truck_wait"])
    w.on_draw()
    assert w.metric_dialog.rows == []
    assert w.metric_dialog.totals["truck_wait"] == 0
    assert "No trucks have arrived yet" in w.metric_dialog.drawing.labels["empty"].text
    click(w, w.metric_dialog.close_rect)
    assert not w.session.playing


def test_feature_policy_can_be_selected_and_switched_with_existing_parameters(arcade_window):
    from mill_yard.policies.scored import ScoredParameters

    w = arcade_window
    w.parameters = ScoredParameters(truck_weight=4)
    click(w, w.policy_rect)
    w.on_draw()
    click(w, next(rect for rect, name in w.menu_rows if name == "feature-scored"))
    w.on_draw()
    assert w.policy_name == "feature-scored"
    assert w.session.simulation.policy.metadata()["weights"]["mill"] == 0.4
    w.toggle_play()
    w.on_update(10)
    assert w.session.simulation.finished
    w.set_policy("scored")
    assert w.session.simulation.policy.parameters.truck_weight == 4


def test_fifo_policy_can_be_selected_in_gui_and_switched_back(arcade_window):
    from mill_yard.policies.fifo import FifoParameters

    w = arcade_window
    w.parameters = FifoParameters(target_fraction=0.6)
    click(w, w.policy_rect)
    w.on_draw()
    click(w, next(rect for rect, name in w.menu_rows if name == "fifo"))
    w.on_draw()
    assert w.session.simulation.policy.parameters.target_fraction == 0.6
    w.toggle_play()
    w.on_update(10)
    assert w.session.simulation.finished
    w.set_policy("scored")
    w.set_policy("fifo")
    assert w.session.simulation.policy.parameters.target_fraction == 0.6


def test_independent_area_queue_rows_scroll_separately_and_show_metric_membership(
    arcade_window, multi_area_yard
):
    from mill_yard.model.types import Arrival

    w = arcade_window
    scenario = replace(
        multi_area_yard,
        arrivals=tuple(
            Arrival(0, 10, f"{area}-{index:02}", area)
            for area in ("north", "south")
            for index in range(20)
        ),
    )
    path = w.scenario_path.parent / "areas.yaml"
    path.write_text(yaml.safe_dump(asdict(scenario)), encoding="utf-8")
    w.load_scenario(path)
    w.on_draw()
    assert set(w.queue_rows) == {"north", "south"}
    assert w.queue_scrolls["north"].count == 18
    assert w.queue_scrolls["south"].count == 19
    row = w.queue_rows["north"]
    w.on_mouse_scroll(row.x + 20, row.y + 20, 0, -3)
    assert w.queue_scrolls["north"].offset == 3
    assert w.queue_scrolls["south"].offset == 0
    click(w, w.metric_hits["truck_wait"])
    w.on_draw()
    assert {row["area_id"] for row in w.metric_dialog.rows} == {"north", "south"}
    assert ("Area", "area_id") in w.metric_dialog.columns


def test_three_area_navigation_and_new_policy_menu_selection(arcade_window):
    import arcade

    w = arcade_window
    w.load_scenario(
        Path(__file__).resolve().parents[2]
        / "configs/experiments/queues/3areas_compact__008trucks_storage_blocked_090min.yaml"
    )
    w.on_draw()
    assert set(w.queue_rows) == {"north", "south"}
    w.on_key_press(arcade.key.PAGEDOWN, 0)
    w.on_draw()
    assert set(w.queue_rows) == {"south", "east"}
    click(w, w.policy_rect)
    w.on_draw()
    click(w, next(rect for rect, name in w.menu_rows if name == "longest-queue-first"))
    w.on_draw()
    assert w.session.simulation.policy.metadata()["queue_rule"] == "longest_queue_first"
    w.toggle_play()
    w.on_update(10)
    w.on_draw()
    assert w.session.display_time > 0
    assert not w.status.startswith("Simulation error")


def test_input_dialog_stages_components_and_applies_a_fresh_run(arcade_window):
    import arcade

    w = arcade_window
    root = Path(__file__).resolve().parents[2]
    original = w.session.simulation
    click(w, next(rect for key, rect, _, _ in w.buttons if key == "inputs"))
    w.on_draw()
    dialog = w.scenario_dialog
    assert not dialog.valid  # The fixture is a legacy combined scenario.
    for key, folder, filename in (
        ("yard", "yards", "2areas_uneven__3bays_2piles_1mill_2loaders_mixed.yaml"),
        ("truck_schedule", "truck_schedules", "2areas_uneven__012trucks_north_heavy_180min.yaml"),
        (
            "initial_conditions",
            "initial_conditions",
            "2areas_uneven__3bays_2piles_1mill_2loaders_mixed__partial_stock.yaml",
        ),
    ):
        click(w, dialog.browse_buttons[key])
        w.on_draw()
        w.on_text(str(root / "configs" / folder / filename))
        w.on_key_press(arcade.key.ENTER, 0)
        w.on_draw()
        assert dialog.browser is None
        assert w.session.simulation is original
    assert dialog.valid
    click(w, dialog.fields["horizon"])
    w.on_text("8")
    w.on_draw()
    click(w, dialog.apply_rect)
    w.on_draw()
    assert w.scenario_dialog is None
    assert w.session.simulation is not original
    assert w.scenario.horizon == 8
    assert w.files.yard.name == "2areas_uneven__3bays_2piles_1mill_2loaders_mixed.yaml"
    assert len(w.scenario.configuration_sources) == 3
    assert set(w.queue_rows) == {"north", "south"}
    w.restart()
    assert w.scenario.horizon == 8


def test_input_dialog_keeps_invalid_draft_out_of_current_run(arcade_window):
    import arcade

    w = arcade_window
    root = Path(__file__).resolve().parents[2]
    w.load_scenario(
        root / "configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml"
    )
    w.set_policy("threshold")
    w.toggle_play()
    original, files = w.session.simulation, w.files
    w.open_configuration()
    w.on_draw()
    dialog = w.scenario_dialog
    dialog.draft.choose(
        "truck_schedule", root / "configs/archive/legacy_single_bay_14h/truck_schedule.yaml"
    )
    dialog.refresh()
    assert not dialog.valid
    assert "unknown unloading area" in dialog.message
    dialog.apply()
    assert w.session.simulation is original
    assert w.files is files
    w.on_key_press(arcade.key.ESCAPE, 0)
    assert w.scenario_dialog is None
    assert w.session.playing


def test_input_browser_rejects_wrong_kind_without_accepting_it(arcade_window):
    import arcade

    w = arcade_window
    root = Path(__file__).resolve().parents[2]
    w.open_configuration()
    w.on_draw()
    dialog = w.scenario_dialog
    dialog.browse("yard")
    w.on_draw()
    w.on_text(str(root / "configs/archive/legacy_single_bay_14h/truck_schedule.yaml"))
    w.on_key_press(arcade.key.ENTER, 0)
    assert "expected kind: yard" in dialog.browser.error
    assert not dialog.draft.yard
