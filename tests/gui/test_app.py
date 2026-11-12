"""A real Tk window smoke test; headless systems skip only the window test."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml


def test_native_window_can_render_and_commit_an_operator_decision(tiny_yard, tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from mill_yard.gui.app import YardApplication

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"No desktop display: {exc}")
    root.withdraw()
    scenario = tmp_path / "yard.yaml"
    scenario.write_text(yaml.safe_dump(asdict(tiny_yard)), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    app = None
    try:
        app = YardApplication(root, scenario, policy="human", validate=True)
        root.update_idletasks()
        app.render()
        assert app.canvas.find_all()
        assert len(app.tasks.get_children()) == 2
        app.tasks.selection_set("0")
        app.add_assignment()
        app.tasks.selection_set("0")
        app.add_assignment()
        assert len(app.session.decision.assignments) == 2
        app.save()
        saved = next((tmp_path / "runs").iterdir())
        assert (
            json.loads((saved / "metadata.json").read_text(encoding="utf-8"))["run_status"]
            == "partial"
        )
        assert (
            json.loads((saved / "summary.json").read_text(encoding="utf-8"))["active_loader_jobs"]
            == 0
        )
        app.commit()
        app.session.tick(10)
        app.render()
        assert app.session.simulation.finished
        assert "Finished" in app.clock.get()
        history = app.events.get("1.0", "end").strip().splitlines()
        assert len(history) == len(app.session.simulation.recorder.events)
        assert "initial" in history[0]
        assert len(history) > 7
        app.render()
        assert app.events.get("1.0", "end").strip().splitlines() == history
        assert "Total truck wait:" in app.metrics.get()
        assert "Total loader travel:" in app.metrics.get()
        assert "Total mill starvation:" in app.metrics.get()
        assert root.bind("<Right>")
    finally:
        if app is not None:
            app.close()
        else:
            root.destroy()


def test_tk_can_select_independent_area_queue(multi_area_yard, tmp_path):
    tk = pytest.importorskip("tkinter")
    from mill_yard.gui.app import YardApplication

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"No desktop display: {exc}")
    root.withdraw()
    path = tmp_path / "areas.yaml"
    path.write_text(yaml.safe_dump(asdict(multi_area_yard)), encoding="utf-8")
    app = None
    try:
        app = YardApplication(root, path, policy="human", validate=True)
        assert tuple(app.queue_selector["values"]) == ("north", "south")
        app.queue_area.set("south")
        app.render()
        labels = [
            app.canvas.itemcget(item, "text")
            for item in app.canvas.find_all()
            if app.canvas.type(item) == "text"
        ]
        assert any("south" in label and "waiting for a bay" in label for label in labels)
    finally:
        if app is not None:
            app.close()
        else:
            root.destroy()


def test_tk_input_dialog_validates_then_applies_or_cancels():
    tk = pytest.importorskip("tkinter")
    from mill_yard.gui.app import YardApplication

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"No desktop display: {exc}")
    root.withdraw()
    app = None
    try:
        repo = Path(__file__).resolve().parents[2]
        app = YardApplication(
            root,
            repo / "configs/experiments/queues/2areas_uneven__012trucks_north_heavy_180min.yaml",
            policy="human",
            validate=True,
        )
        original = app.session.simulation
        app.open_configuration()
        dialog = app.configuration_dialog
        dialog.window.withdraw()
        dialog.variables["truck_schedule"].set(
            str(repo / "configs/archive/legacy_single_bay_14h/truck_schedule.yaml")
        )
        assert dialog.apply_button.instate(["disabled"])
        dialog.apply()
        assert app.session.simulation is original
        dialog.choose(
            "truck_schedule",
            repo / "configs/truck_schedules/2areas_uneven__012trucks_north_heavy_180min.yaml",
        )
        dialog.horizon.set("10")
        assert not dialog.apply_button.instate(["disabled"])
        dialog.apply()
        assert app.configuration_dialog is None
        assert app.session.simulation is not original
        assert app.session.scenario.horizon == 10
        previous = app.session.simulation
        app.open_configuration()
        app.configuration_dialog.cancel()
        assert app.session.simulation is previous
    finally:
        if app is not None:
            app.close()
        else:
            root.destroy()
