"""Optional Arcade desktop frontend, sharing the Tk viewer's session API."""

import json
from pathlib import Path
from textwrap import wrap

import arcade

from mill_yard.config.loader import select_scenario_files
from mill_yard.gui.arcade_dialogs import ConfigurationDialog, MetricDialog, ScenarioDialog
from mill_yard.gui.arcade_scene import GREEN, INK, MUTED, WHITE, Drawing, YardRenderer
from mill_yard.gui.controls import ScreenRect, ScrollList, YardCamera
from mill_yard.gui.presentation import live_metrics
from mill_yard.gui.session import SimulationSession
from mill_yard.policies.factory import POLICY_NAMES, create_policy
from mill_yard.records.persistence import save_run


class ArcadeYardWindow(arcade.Window):
    """Playback, map/panel dispatch, scenario selection, and full event history."""

    def __init__(
        self,
        scenario_path=None,
        *,
        yard=None,
        truck_schedule=None,
        initial_conditions=None,
        horizon=None,
        policy="threshold",
        seed=0,
        parameters=None,
        speed=5.0,
        record="events",
        validate=False,
        wake_mode=None,
        wake_interval=None,
        visible=True,
    ):
        self.wake_overrides = {
            key: value
            for key, value in (("wake_mode", wake_mode), ("wake_interval", wake_interval))
            if value is not None
        }
        self.files = select_scenario_files(
            scenario_path,
            yard=yard,
            truck_schedule=truck_schedule,
            initial_conditions=initial_conditions,
            horizon=horizon,
            **self.wake_overrides,
        )
        self.scenario_path = self.files.experiment or self.files.legacy or Path("Custom inputs")
        self.scenario = self.files.load()
        self.policy_name, self.seed, self.parameters = policy, seed, parameters
        self.session = SimulationSession(
            self.scenario, self._policy, speed=speed, record=record, validate=validate
        )
        try:
            super().__init__(
                1320,
                880,
                "mill-yard | Arcade viewer and operator",
                resizable=True,
                visible=visible,
                vsync=True,
            )
        except Exception:
            self.session.close()
            raise
        self.set_minimum_size(1120, 760)
        self.drawing, self.yard = Drawing(), YardRenderer()
        self.queue_drawing = YardRenderer()
        self.events_scroll = ScrollList()
        self.tasks_scroll = ScrollList(follow=False)
        self.queue_scrolls = {}
        self.queue_area_scroll = ScrollList(follow=False)
        self.queue_rows = {}
        self.selected_loader = None
        self.selected_pickup = None
        self.scenario_dialog = None
        self.metric_dialog = None
        self.metric_hits = {}
        self.menu_kind = None
        self.menu_rows = []
        self.menu_index = 0
        self._resume_after_overlay = False
        self.quantity = ""
        self.editing_quantity = False
        self.status = (
            "Click loader, pickup, then deposit to draft a task, or use the dispatch panel."
        )
        self.buttons = []
        self.task_hits = []
        self._scroll_drag = None
        self._map_press = None
        self._map_dragged = False
        self._choice_signature = object()
        self._layout()
        self.camera = YardCamera.fit(self.scenario, self.map_rect)
        self._sync_choices()

    def _policy(self):
        return create_policy(self.policy_name, seed=self.seed, parameters=self.parameters)

    def _layout(self):
        side_x = self.width - 410
        queue_height = 100 * min(2, len(self.scenario.unloading_areas))
        map_y = 94 + queue_height
        self.map_rect = ScreenRect(14, map_y, side_x - 28, self.height - 96 - map_y)
        self.queue_rect = ScreenRect(14, 82, side_x - 28, queue_height)
        self.side_rect = ScreenRect(side_x, 82, 396, self.height - 178)
        self.tasks_rect = ScreenRect(side_x + 12, self.height - 373, 372, 160)
        self.quantity_rect = ScreenRect(side_x + 227, self.height - 197, 142, 26)
        self.events_rect = ScreenRect(side_x + 12, 104, 372, self.height - 632)

    def _sync_choices(self):
        decision = self.session.decision
        signature = (
            None if decision is None else (id(decision.request), tuple(decision.assignments))
        )
        if signature == self._choice_signature:
            return
        self._choice_signature = signature
        self.options = [] if decision is None else decision.options
        self.decision_loaders = {a.loader_id for a in self.options}
        if self.selected_loader not in self.decision_loaders:
            self.selected_loader = None
        if not any(
            a.loader_id == self.selected_loader
            and (a.source_kind, a.source_id) == self.selected_pickup
            for a in self.options
        ):
            self.selected_pickup = None
        self.tasks_scroll = ScrollList(follow=False)

    @property
    def overlay_open(self):
        return (
            self.scenario_dialog is not None
            or self.metric_dialog is not None
            or self.menu_kind is not None
        )

    def on_update(self, delta_time):
        if self.overlay_open:
            return
        try:
            self.session.tick(delta_time)
            self._sync_choices()
        except Exception as exc:
            self.session.playing = False
            self.status = f"Simulation error: {exc}"

    def _clip(self, rect):
        # Arcade's default framebuffer applies the display's pixel ratio itself.
        self.ctx.scissor = (round(rect.x), round(rect.y), round(rect.width), round(rect.height))

    def on_draw(self):
        self.clear((235, 240, 243))
        self.default_camera.use()
        self._layout()
        self.buttons, self.task_hits = [], []
        self.drawing.begin()
        self.drawing.box(self.map_rect, (240, 245, 237), (198, 211, 199))
        self._clip(self.map_rect)
        self.yard.draw(
            self.session,
            self.camera,
            self.map_rect,
            self.decision_loaders,
            self.selected_loader,
            self.options,
            self.selected_pickup,
        )
        self.ctx.scissor = None
        self._toolbar()
        self._operator_panel()
        self._events_panel()
        self._queue_panel()
        self._metrics_panel()
        self.drawing.text("status", self.status[:175], 14, 16, size=10, color=MUTED)
        if self.menu_kind is not None:
            self._draw_menu()
        if self.scenario_dialog is not None:
            self.scenario_dialog.draw(self)
        if self.metric_dialog is not None:
            self.metric_dialog.draw(self)
        self.drawing.end()

    def _button(self, key, label, rect, action, *, enabled=True, active=False):
        color = GREEN if active else (217, 227, 233) if enabled else (228, 233, 236)
        self.drawing.box(rect, color, (168, 187, 198))
        self.drawing.text(
            f"button:{key}",
            label,
            rect.x + 9,
            rect.y + rect.height / 2,
            size=10,
            color=WHITE if active else INK if enabled else (150, 160, 168),
        )
        self.buttons.append((key, rect, action, enabled))

    def _toolbar(self):
        y, x = self.height - 42, 14
        pending = self.session.decision is not None
        for key, label, width, action, enabled in (
            (
                "play",
                "Pause [Space]" if self.session.playing else "Play [Space]",
                104,
                self.toggle_play,
                not pending and not self.session.simulation.finished,
            ),
            (
                "step",
                "Next [Right]",
                98,
                self.step,
                not pending and not self.session.simulation.finished,
            ),
            ("slower", "- rate", 58, lambda: self.change_speed(0.5), True),
            ("faster", "+ rate", 58, lambda: self.change_speed(2), True),
            ("fit", "Fit [F]", 65, self.fit, True),
            ("zoom-out", "Zoom -", 66, lambda: self.zoom(1 / 1.25), True),
            ("zoom-in", "Zoom +", 66, lambda: self.zoom(1.25), True),
            ("restart", "Restart [R]", 92, self.restart, True),
            ("save", "Save [S]", 79, self.save, True),
            (
                "policy",
                f"Policy: {self.policy_name} v",
                220,
                lambda: self.open_menu("policy"),
                True,
            ),
        ):
            self._button(key, label, ScreenRect(x, y, width, 28), action, enabled=enabled)
            if key == "policy":
                self.policy_rect = ScreenRect(x, y, width, 28)
            x += width + 6
        self._button(
            "open", "Open config...", ScreenRect(14, self.height - 80, 108, 27), self.open_scenario
        )
        self._button(
            "inputs",
            "Inputs...",
            ScreenRect(128, self.height - 80, 86, 27),
            self.open_configuration,
        )
        sim = self.session.simulation
        mode = (
            "Operator decision"
            if pending
            else "Finished"
            if sim.finished
            else "Playing"
            if self.session.playing
            else "Paused"
        )
        self.drawing.text(
            "scenario",
            f"{self.scenario_path.name} | seed {self.seed} | "
            f"{self.session.speed:g} sim min/s | zoom {self.camera.zoom:.2f}x",
            224,
            self.height - 65,
            size=10,
            color=MUTED,
        )
        self.drawing.text(
            "clock",
            f"{mode} | {self.session.display_time:.2f} / {self.scenario.horizon:g} min",
            self.width - 400,
            self.height - 65,
            bold=True,
        )

    def _operator_panel(self):
        d, x, top = self.drawing, self.side_rect.x + 12, self.height - 118
        d.box(self.side_rect, (250, 252, 253), (203, 215, 223))
        d.text("operator-title", "Operator dispatch", x, top, size=14, bold=True)
        d.text("operator-help", "Choose on the map or use the loader menu.", x, top - 27)
        self.loader_rect = ScreenRect(x, top - 61, 202, 26)
        self._button(
            "loader",
            f"Loader: {self.selected_loader or 'select'} v",
            self.loader_rect,
            lambda: self.open_menu("loader"),
            enabled=bool(self.decision_loaders),
        )
        d.text("quantity-label", "Quantity (blank = max)", self.quantity_rect.x, top - 40, size=9)
        d.box(self.quantity_rect, WHITE, GREEN if self.editing_quantity else (174, 189, 199))
        d.text(
            "quantity-value",
            self.quantity or "max",
            self.quantity_rect.x + 7,
            self.quantity_rect.y + 13,
            color=INK if self.quantity else MUTED,
        )
        options = [a for a in self.options if a.loader_id == self.selected_loader]
        self.tasks_scroll.sync(len(options), int(self.tasks_rect.height // 52))
        d.box(self.tasks_rect, (237, 243, 246))
        for row, index in enumerate(self.tasks_scroll.visible):
            a = options[index]
            rect = ScreenRect(
                x,
                self.tasks_rect.y + self.tasks_rect.height - (row + 1) * 52,
                self.tasks_rect.width - 14,
                49,
            )
            d.box(rect, (225, 237, 232), (174, 199, 189))
            d.text(
                f"task:{row}:source",
                f"{a.source_kind} {a.source_id} -> {a.destination_kind} {a.destination_id}",
                rect.x + 7,
                rect.y + 33,
                size=10,
            )
            d.text(
                f"task:{row}:quantity",
                f"Up to {a.quantity:.2f} t | click to add",
                rect.x + 7,
                rect.y + 14,
                size=10,
                color=GREEN,
            )
            self.task_hits.append((rect, a))
        if not options:
            message = (
                "Choose a loader on the map or above."
                if self.options
                else (
                    "Draft ready: commit or undo."
                    if self.session.decision
                    else "No operator input pending."
                )
            )
            d.text("no-tasks", message, x + 8, self.tasks_rect.y + 85, size=10, color=MUTED)
        self._scrollbar("tasks", self.tasks_rect, self.tasks_scroll)
        assignments = [] if self.session.decision is None else self.session.decision.assignments
        y = self.tasks_rect.y - 21
        d.text("draft-title", f"Draft joint decision: {len(assignments)} job(s)", x, y, bold=True)
        for index, a in enumerate(assignments[-3:]):
            d.text(
                f"draft:{index}",
                f"{a.loader_id}: {a.source_id} -> {a.destination_id} ({a.quantity:g} t)",
                x,
                y - 20 - index * 17,
                size=10,
            )
        enabled = self.session.decision is not None
        y -= 94
        self._button("undo", "Undo [U]", ScreenRect(x, y, 88, 28), self.undo, enabled=enabled)
        self._button(
            "commit",
            "Commit [Enter]",
            ScreenRect(x + 95, y, 135, 28),
            self.commit,
            enabled=enabled,
            active=enabled,
        )
        self._button(
            "wait",
            "Wait / idle",
            ScreenRect(x + 237, y, 132, 28),
            lambda: self.commit(wait=True),
            enabled=enabled,
        )

    def _events_panel(self):
        rect, d = self.events_rect, self.drawing
        events = self.session.simulation.recorder.events
        self.events_scroll.sync(len(events), int(rect.height // 55))
        d.text(
            "events-title",
            f"Model events ({len(events)})",
            rect.x,
            rect.y + rect.height + 20,
            size=13,
            bold=True,
        )
        d.box(rect, (235, 241, 245))
        self._clip(rect)
        for row, index in enumerate(self.events_scroll.visible):
            event = events[index]
            y = rect.y + rect.height - row * 55 - 13
            d.text(
                f"event:{row}:title",
                f"#{event['sequence']} | {event['time']:.3f} min | {event['event']}",
                rect.x + 6,
                y,
                size=10,
                bold=True,
            )
            details = {
                k: v
                for k, v in event.items()
                if k
                not in (
                    "state",
                    "schema_version",
                    "sequence",
                    "time",
                    "event",
                    "policy_state",
                    "candidates",
                )
            }
            lines = wrap(json.dumps(details, ensure_ascii=False), width=48) or [""]
            for line, value in enumerate(lines[:2]):
                d.text(
                    f"event:{row}:detail:{line}",
                    value,
                    rect.x + 6,
                    y - 16 - line * 14,
                    size=9,
                    color=MUTED,
                )
        if not events:
            d.text(
                "no-events",
                "No events retained (--record summary).",
                rect.x + 8,
                rect.y + rect.height - 24,
                size=10,
                color=MUTED,
            )
        self.ctx.scissor = None
        self._scrollbar("events", rect, self.events_scroll)

    def _scrollbar(self, key, rect, scroll):
        if scroll.maximum <= 0:
            return
        track = ScreenRect(rect.x + rect.width - 10, rect.y, 10, rect.height)
        self.drawing.box(track, (207, 219, 226))
        height = max(18, rect.height * scroll.capacity / scroll.count)
        top = rect.y + rect.height - (rect.height - height) * scroll.offset / scroll.maximum
        self.drawing.box(ScreenRect(track.x + 1, top - height, 8, height), (117, 144, 159))

    def _queue_panel(self):
        rect, d = self.queue_rect, self.drawing
        state = self.session.simulation.state
        areas = self.scenario.unloading_areas
        self.queue_area_scroll.sync(len(areas), min(2, len(areas)))
        self.queue_rows = {}
        self.queue_drawing.begin()
        for row, index in enumerate(self.queue_area_scroll.visible):
            area = areas[index]
            queue = state.queues[area.id]
            box = ScreenRect(rect.x, rect.y + rect.height - (row + 1) * 100, rect.width, 98)
            self.queue_rows[area.id] = box
            scroll = self.queue_scrolls.setdefault(area.id, ScrollList(follow=False))
            scroll.sync(len(queue), max(1, int(rect.width // 130)))
            d.box(box, (232, 237, 240), (203, 215, 223))
            stopped = sum(
                state.stops[s.id] is not None for s in self.scenario.stops if s.area_id == area.id
            )
            d.text(
                f"queue-title:{area.id}",
                f"{area.id}: {len(queue)} waiting / {stopped} at stops | wheel: trucks",
                box.x + 8,
                box.y + 83,
                size=10,
                color=MUTED,
            )
            self._clip(box)
            for column, truck_index in enumerate(scroll.visible):
                self.queue_drawing.truck(
                    state.trucks[queue[truck_index]],
                    self.session.display_time,
                    box.x + 47 + column * 130,
                    box.y + 49,
                    0.85,
                )
            self.ctx.scissor = None
        self.queue_drawing.end()
        if len(areas) > 2:
            y = rect.y + rect.height - 27
            self._button(
                "queue-prev",
                "Areas ^",
                ScreenRect(rect.x + rect.width - 126, y, 59, 24),
                lambda: self.queue_area_scroll.scroll(-1),
            )
            self._button(
                "queue-next",
                "Areas v",
                ScreenRect(rect.x + rect.width - 63, y, 59, 24),
                lambda: self.queue_area_scroll.scroll(1),
            )

    def _metrics_panel(self):
        m, d, sim = (
            live_metrics(self.session.simulation, self.session.display_time),
            self.drawing,
            self.session.simulation,
        )
        d.box(ScreenRect(0, 29, self.width, 44), (217, 230, 234))
        self.metric_hits = {}
        x, available = 14, self.width - 44
        for kind, key, text, fraction in (
            ("truck_wait", "metric-wait", f"Total truck wait: {m['truck_wait']:.2f} min", 0.27),
            (
                "loader_travel",
                "metric-travel",
                f"Total loader travel: {m['travel_distance']:.1f} m / {m['travel_time']:.2f} min",
                0.40,
            ),
            (
                "mill_starvation",
                "metric-starvation",
                f"Total mill starvation: {m['starvation']:.2f} min",
                0.33,
            ),
        ):
            rect = ScreenRect(x, 33, available * fraction, 36)
            d.box(rect, (229, 239, 242), (167, 193, 201))
            d.text(key, text, x + 9, 51, size=10, color=GREEN, bold=True)
            d.text(f"{key}:arrow", ">", x + rect.width - 15, 51, color=GREEN)
            self.metric_hits[kind] = rect
            x += rect.width + 8
        d.text(
            "yard-info",
            f"Completed trucks: {len(sim.completed_trucks)} | "
            f"Still present: {len(sim.state.trucks)} | "
            f"Consumed: {sum(sim.state.consumed.values()):.1f} t",
            self.map_rect.x + 10,
            self.map_rect.y + self.map_rect.height - 17,
            size=10,
            color=MUTED,
        )

    def toggle_play(self):
        if self.session.playing:
            self.session.playing = False
        else:
            self.session.play()

    def step(self):
        self.session.step()
        self._sync_choices()

    def change_speed(self, factor):
        self.session.speed = min(1024, max(0.125, self.session.speed * factor))

    def fit(self):
        self.camera = YardCamera.fit(self.scenario, self.map_rect)

    def zoom(self, factor):
        rect = self.map_rect
        self.camera.zoom_at(factor, rect.x + rect.width / 2, rect.y + rect.height / 2, rect)

    def restart(self):
        self.apply_configuration(self.files)
        self.status = "Restarted from time zero."

    def _reset_controls(self):
        self.events_scroll = ScrollList()
        self.queue_scrolls = {}
        self.queue_area_scroll = ScrollList(follow=False)
        self.queue_rows = {}
        self.selected_loader, self.selected_pickup, self.quantity = None, None, ""
        self.editing_quantity = False
        self.menu_kind = None
        self.menu_rows = []
        self.metric_dialog = None
        self._scroll_drag = None
        self._map_press = None
        self._map_dragged = False
        self._choice_signature = object()
        self._sync_choices()

    def set_policy(self, name):
        if name not in POLICY_NAMES:
            raise ValueError(f"Unknown policy: {name}")
        if name == self.policy_name:
            return
        self.policy_name = name
        self.restart()
        self.status = f"Restarted with {self.policy_name} policy."

    def select_loader(self, loader_id):
        if loader_id not in self.decision_loaders:
            return
        self.selected_loader, self.selected_pickup = loader_id, None
        self.tasks_scroll = ScrollList(follow=False)
        self.status = f"{loader_id}: click a blue pickup location, or choose a task in the panel."

    def _menu_values(self):
        return (
            list(POLICY_NAMES)
            if self.menu_kind == "policy"
            else [spec.id for spec in self.scenario.loaders if spec.id in self.decision_loaders]
        )

    def open_menu(self, kind):
        self._resume_after_overlay = self.session.playing
        self.session.playing = False
        self.editing_quantity = False
        self.menu_kind = kind
        current = self.policy_name if kind == "policy" else self.selected_loader
        values = self._menu_values()
        self.menu_index = values.index(current) if current in values else 0

    def _draw_menu(self):
        anchor = self.policy_rect if self.menu_kind == "policy" else self.loader_rect
        self.menu_rows = []
        for index, value in enumerate(self._menu_values()):
            rect = ScreenRect(anchor.x, anchor.y - (index + 1) * 29, max(180, anchor.width), 29)
            self.drawing.box(rect, (204, 231, 217) if index == self.menu_index else WHITE, MUTED)
            self.drawing.text(f"menu:{index}", value, rect.x + 10, rect.y + 15)
            self.menu_rows.append((rect, value))

    def close_menu(self, resume=True):
        self.menu_kind = None
        self.menu_rows = []
        if resume and self._resume_after_overlay:
            self.session.play()

    def choose_menu_value(self, value):
        kind = self.menu_kind
        self.close_menu(resume=value == self.policy_name if kind == "policy" else False)
        if kind == "policy":
            self.set_policy(value)
        else:
            self.select_loader(value)

    def open_scenario(self):
        self._resume_after_overlay = self.session.playing
        self.session.playing = False
        self.editing_quantity = False
        self.scenario_dialog = ScenarioDialog(
            (self.files.experiment or self.files.legacy or self.files.yard).resolve().parent,
            self.load_scenario,
        )

    def open_configuration(self):
        self._resume_after_overlay = self.session.playing
        self.session.playing = False
        self.editing_quantity = False
        self.scenario_dialog = ConfigurationDialog(self.files, self.apply_configuration)

    def load_scenario(self, path):
        """Build and validate the replacement before discarding the current run."""
        self.apply_configuration(select_scenario_files(path, **self.wake_overrides))

    def apply_configuration(self, files):
        scenario = files.load(**self.wake_overrides)
        session = SimulationSession(
            scenario,
            self._policy,
            speed=self.session.speed,
            record=self.session.record,
            validate=self.session.validate,
        )
        self.session.close()
        self.session, self.scenario, self.files = session, scenario, files
        self.scenario_path = files.experiment or files.legacy or Path("Custom inputs")
        self._reset_controls()
        self.fit()
        self.status = f"Loaded {self.scenario_path.name}; restarted with {self.policy_name} policy."

    def _finish_dialog(self):
        if self.scenario_dialog is not None and self.scenario_dialog.done:
            accepted = self.scenario_dialog.accepted
            self.scenario_dialog = None
            if not accepted and self._resume_after_overlay:
                self.session.play()

    def open_metrics(self, kind):
        self._resume_after_overlay = self.session.playing
        self.session.playing = False
        self.editing_quantity = False
        self._map_press = None
        self.metric_dialog = MetricDialog(kind, self.session.simulation, self.session.display_time)

    def _finish_metric_dialog(self):
        if self.metric_dialog is not None and self.metric_dialog.done:
            self.metric_dialog = None
            if self._resume_after_overlay:
                self.session.play()

    def _map_click(self, x, y):
        for loader_id, rect in reversed(
            list(self.yard.flags.items()) + list(self.yard.loaders.items())
        ):
            if loader_id in self.decision_loaders and rect.contains(x, y):
                self.select_loader(loader_id)
                return
        options = [a for a in self.options if a.loader_id == self.selected_loader]
        if self.selected_pickup is not None:
            for a in options:
                target = self.yard.targets.get((a.destination_kind, a.destination_id))
                if (
                    (a.source_kind, a.source_id) == self.selected_pickup
                    and target is not None
                    and target.contains(x, y)
                ):
                    self.add_assignment(a)
                    return
        for a in options:
            key = (a.source_kind, a.source_id)
            target = self.yard.targets.get(key)
            if target is not None and target.contains(x, y):
                self.selected_pickup = key
                self.status = (
                    f"Pickup {a.source_id}: click a green deposit location to add the task."
                )
                return

    def add_assignment(self, assignment):
        if self.session.decision is None:
            return
        try:
            self.session.decision.add(assignment, float(self.quantity) if self.quantity else None)
            self.quantity, self.editing_quantity = "", False
            self._sync_choices()
            self.status = "Task added to draft. Choose another loader or Commit to resume."
        except ValueError as exc:
            self.status = str(exc)

    def undo(self):
        if self.session.decision is not None:
            self.session.decision.undo()
            self._sync_choices()

    def commit(self, wait=False):
        if self.session.decision is None:
            return
        try:
            self.session.submit(wait=wait)
            self.editing_quantity = False
            self._sync_choices()
            self.status = "Decision committed; playback resumes until the next operator decision."
        except (ValueError, RuntimeError) as exc:
            self.status = str(exc)

    def save(self):
        self.session.playing = False
        try:
            path = save_run(self.session.simulation.result())
            self.status = (
                f"Saved {'complete' if self.session.simulation.finished else 'partial'} run: {path}"
            )
        except OSError as exc:
            self.status = f"Cannot save run: {exc}"

    def on_mouse_press(self, x, y, button, modifiers):
        if button != arcade.MOUSE_BUTTON_LEFT:
            return
        if self.scenario_dialog is not None:
            self.scenario_dialog.click(x, y)
            self._finish_dialog()
            return
        if self.metric_dialog is not None:
            self.metric_dialog.click(x, y)
            self._finish_metric_dialog()
            return
        if self.menu_kind is not None:
            for rect, value in self.menu_rows:
                if rect.contains(x, y):
                    self.choose_menu_value(value)
                    return
            self.close_menu()
            return
        for kind, rect in self.metric_hits.items():
            if rect.contains(x, y):
                self.open_metrics(kind)
                return
        self.editing_quantity = (
            self.quantity_rect.contains(x, y) and self.session.decision is not None
        )
        if self.editing_quantity:
            return
        for _, rect, action, enabled in self.buttons:
            if enabled and rect.contains(x, y):
                action()
                return
        for key, rect, scroll in (
            ("events", self.events_rect, self.events_scroll),
            ("tasks", self.tasks_rect, self.tasks_scroll),
        ):
            if scroll.maximum and rect.contains(x, y) and x >= rect.x + rect.width - 12:
                self._scroll_drag = key
                self._seek_scroll(y, rect, scroll)
                return
        if self.map_rect.contains(x, y):
            self._map_press = (x, y)
            self._map_dragged = False
            return
        for rect, assignment in self.task_hits:
            if (
                rect.contains(x, y)
                and assignment.loader_id == self.selected_loader
                and assignment in self.options
            ):
                self.add_assignment(assignment)
                return

    @staticmethod
    def _seek_scroll(y, rect, scroll):
        thumb = max(18, rect.height * scroll.capacity / scroll.count)
        scroll.seek((rect.y + rect.height - y - thumb / 2) / max(1, rect.height - thumb))

    def on_mouse_release(self, x, y, button, modifiers):
        self._scroll_drag = None
        if self.metric_dialog is not None:
            self.metric_dialog.dragging = False
        if (
            button == arcade.MOUSE_BUTTON_LEFT
            and self._map_press is not None
            and not self._map_dragged
            and self.map_rect.contains(x, y)
            and not self.overlay_open
        ):
            self._map_click(x, y)
        self._map_press = None
        self._map_dragged = False

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self.metric_dialog is not None:
            self.metric_dialog.drag(y)
            return
        if self.overlay_open:
            return
        if self._scroll_drag is not None:
            rect, scroll = (
                (self.events_rect, self.events_scroll)
                if self._scroll_drag == "events"
                else (self.tasks_rect, self.tasks_scroll)
            )
            self._seek_scroll(y, rect, scroll)
        elif self.map_rect.contains(x, y) and buttons & (
            arcade.MOUSE_BUTTON_LEFT | arcade.MOUSE_BUTTON_MIDDLE | arcade.MOUSE_BUTTON_RIGHT
        ):
            if self._map_press is not None:
                if not self._map_dragged and (
                    (x - self._map_press[0]) ** 2 + (y - self._map_press[1]) ** 2 <= 16
                ):
                    return
                self._map_dragged = True
            self.camera.pan(dx, dy)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        if self.scenario_dialog is not None:
            self.scenario_dialog.scroll.scroll(-scroll_y * 3)
            return
        if self.metric_dialog is not None:
            self.metric_dialog.scroll.scroll(-scroll_y * 3)
            return
        if self.menu_kind is not None:
            return
        if self.events_rect.contains(x, y):
            self.events_scroll.scroll(-scroll_y * 3)
        elif self.tasks_rect.contains(x, y):
            self.tasks_scroll.scroll(-scroll_y)
        elif self.queue_rect.contains(x, y):
            for area_id, rect in self.queue_rows.items():
                if rect.contains(x, y):
                    self.queue_scrolls[area_id].scroll(-(scroll_y or scroll_x))
                    break
        elif self.map_rect.contains(x, y):
            self.camera.zoom_at(1.15 ** max(-10, min(10, scroll_y)), x, y, self.map_rect)

    def on_text(self, text):
        if self.scenario_dialog is not None:
            self.scenario_dialog.text(text)
            return
        if self.metric_dialog is not None:
            return
        if self.editing_quantity and len(self.quantity) < 16:
            self.quantity += "".join(c for c in text if c in "0123456789.")

    def on_key_press(self, symbol, modifiers):
        if self.scenario_dialog is not None:
            self.scenario_dialog.key(symbol, modifiers)
            self._finish_dialog()
            return
        if self.metric_dialog is not None:
            self.metric_dialog.key(symbol)
            self._finish_metric_dialog()
            return
        if self.menu_kind is not None:
            if symbol == arcade.key.ESCAPE:
                self.close_menu()
            elif symbol in (arcade.key.UP, arcade.key.DOWN):
                self.menu_index = (
                    self.menu_index + (1 if symbol == arcade.key.DOWN else -1)
                ) % len(self._menu_values())
            elif symbol == arcade.key.ENTER:
                self.choose_menu_value(self._menu_values()[self.menu_index])
            return
        if self.editing_quantity:
            if symbol == arcade.key.BACKSPACE:
                self.quantity = self.quantity[:-1]
            elif symbol in (arcade.key.ENTER, arcade.key.ESCAPE):
                self.editing_quantity = False
            return
        actions = {
            arcade.key.SPACE: self.toggle_play,
            arcade.key.RIGHT: self.step,
            arcade.key.F: self.fit,
            arcade.key.R: self.restart,
            arcade.key.S: self.save,
            arcade.key.U: self.undo,
            arcade.key.ENTER: self.commit,
            arcade.key.EQUAL: lambda: self.zoom(1.25),
            arcade.key.MINUS: lambda: self.zoom(0.8),
            arcade.key.PAGEUP: lambda: self.queue_area_scroll.scroll(-1),
            arcade.key.PAGEDOWN: lambda: self.queue_area_scroll.scroll(1),
        }
        if symbol in actions:
            actions[symbol]()
        elif symbol == arcade.key.LEFT:
            self.status = "Backward replay is not implemented; Restart begins a fresh run."
        elif symbol in (arcade.key.HOME, arcade.key.END):
            self.events_scroll.seek(0 if symbol == arcade.key.HOME else 1)
        elif symbol == arcade.key.ESCAPE:
            if self.selected_pickup is not None:
                self.selected_pickup = None
            else:
                self.selected_loader = None

    def on_close(self):
        self.session.close()
        super().on_close()


def launch(scenario_path, **options):
    """Open Arcade only when explicitly selected; headless runs never import it."""
    window = ArcadeYardWindow(scenario_path, **options)
    try:
        arcade.run()
    finally:
        window.session.close()
        window.close()
