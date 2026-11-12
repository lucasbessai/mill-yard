"""Simple Tk desktop viewer and human-operated mill-yard session."""

import math
import time
import tkinter as tk
from contextlib import suppress
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mill_yard.config.loader import select_scenario_files
from mill_yard.gui.presentation import (
    Viewport,
    infeed_amount,
    live_metrics,
    loader_position,
    truck_wait,
)
from mill_yard.gui.session import SimulationSession
from mill_yard.gui.tk_configuration import ConfigurationDialog
from mill_yard.policies.factory import POLICY_NAMES, create_policy
from mill_yard.records.persistence import save_run

LOADER_COLORS = ("#e8aa2b", "#4287cf", "#b86ad9", "#e57755", "#58b78c")


class YardApplication:
    """Canvas-based schematic with explicit playback and dispatch controls."""

    def __init__(
        self,
        root,
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
    ):
        self.root = root
        self.parameters = parameters
        self.record = record
        self.validate = validate
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
        self.configuration_dialog = None
        self.session = None
        self._request = None
        self._options = []
        self._closing = False
        self._event_count = 0
        self._last_frame = time.monotonic()
        self.path = tk.StringVar(value=self.files.label)
        self.policy_name = tk.StringVar(value=policy)
        self.seed = tk.StringVar(value=str(seed))
        self.speed = tk.StringVar(value=str(speed))
        self.quantity = tk.StringVar()
        self.queue_area = tk.StringVar(value="main")
        self.clock = tk.StringVar()
        self.metrics = tk.StringVar()
        self.status = tk.StringVar(value="Right arrow: next timestamp. Space: play/pause.")
        root.title("mill-yard — simulation viewer and operator")
        root.geometry("1280x820")
        root.minsize(1050, 680)
        self._build()
        root.bind("<Right>", self._right)
        root.bind("<Left>", self._left)
        root.bind("<space>", self._space)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.restart()
        self._timer = root.after(50, self._frame)

    def _build(self):
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Scenario").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.path, width=26).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Open…", command=self.open_scenario).pack(side="left")
        ttk.Button(toolbar, text="Inputs...", command=self.open_configuration).pack(
            side="left", padx=5
        )
        ttk.Label(toolbar, text="Policy / operator").pack(side="left", padx=(12, 3))
        ttk.Combobox(
            toolbar, textvariable=self.policy_name, values=POLICY_NAMES, state="readonly", width=22
        ).pack(side="left")
        ttk.Label(toolbar, text="Seed").pack(side="left", padx=(8, 2))
        ttk.Entry(toolbar, textvariable=self.seed, width=6).pack(side="left")
        ttk.Button(toolbar, text="Apply & restart", command=self.restart).pack(side="left", padx=8)
        ttk.Button(toolbar, text="Save current run", command=self.save).pack(side="right")

        controls = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        controls.pack(fill="x")
        self.play_button = ttk.Button(controls, text="Play [Space]", command=self.toggle_play)
        self.play_button.pack(side="left")
        self.step_button = ttk.Button(controls, text="Next timestamp [→]", command=self.step)
        self.step_button.pack(side="left", padx=5)
        ttk.Label(controls, text="Speed: sim min / real second").pack(side="left", padx=(12, 4))
        ttk.Spinbox(
            controls, from_=0.1, to=120, increment=0.5, textvariable=self.speed, width=7
        ).pack(side="left")
        ttk.Label(controls, textvariable=self.clock, font=("Segoe UI", 11, "bold")).pack(
            side="right"
        )

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8)
        map_frame = ttk.Frame(body)
        body.add(map_frame, weight=3)
        self.canvas = tk.Canvas(
            map_frame, background="#f1f5f0", highlightthickness=1, highlightbackground="#cbd5d1"
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.render())
        queue_controls = ttk.Frame(map_frame)
        queue_controls.pack(fill="x")
        ttk.Label(queue_controls, text="Truck queue area:").pack(side="left", padx=8)
        self.queue_selector = ttk.Combobox(
            queue_controls, textvariable=self.queue_area, state="readonly", width=22
        )
        self.queue_selector.pack(side="left")
        self.queue_selector.bind("<<ComboboxSelected>>", lambda _: self.render())
        ttk.Label(map_frame, textvariable=self.metrics, justify="left", padding=8).pack(fill="x")

        side = ttk.Frame(body, padding=(10, 0, 0, 0), width=390)
        body.add(side, weight=1)
        ttk.Label(side, text="Operator dispatch", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            side,
            text="Human mode pauses at feasible decisions.\nAdd jobs, then commit together; unused loaders idle.",
            justify="left",
        ).pack(anchor="w", pady=(4, 8))
        frame = ttk.Frame(side)
        frame.pack(fill="both", expand=True)
        self.tasks = ttk.Treeview(
            frame,
            columns=("loader", "source", "dest", "quantity"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        for key, title, width in (
            ("loader", "Loader", 75),
            ("source", "Source", 100),
            ("dest", "Destination", 100),
            ("quantity", "Max t", 55),
        ):
            self.tasks.heading(key, text=title)
            self.tasks.column(key, width=width, minwidth=40)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tasks.yview)
        self.tasks.configure(yscrollcommand=scroll.set)
        self.tasks.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tasks.bind("<Double-1>", lambda _: self.add_assignment())
        row = ttk.Frame(side)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Quantity (t; blank = max)").pack(side="left")
        ttk.Entry(row, textvariable=self.quantity, width=9).pack(side="left", padx=5)
        self.add_button = ttk.Button(row, text="Add", command=self.add_assignment)
        self.add_button.pack(side="right")
        ttk.Label(side, text="Draft joint decision").pack(anchor="w")
        self.draft = tk.Listbox(side, height=5, font=("Consolas", 9), exportselection=False)
        self.draft.pack(fill="x", pady=4)
        row = ttk.Frame(side)
        row.pack(fill="x")
        self.undo_button = ttk.Button(row, text="Undo last", command=self.undo_assignment)
        self.undo_button.pack(side="left")
        self.commit_button = ttk.Button(row, text="Commit & resume", command=self.commit)
        self.commit_button.pack(side="right")
        self.wait_button = ttk.Button(
            side, text="Wait / idle all & resume", command=lambda: self.commit(wait=True)
        )
        self.wait_button.pack(fill="x", pady=6)
        ttk.Label(side, text="Model events").pack(anchor="w", pady=(8, 3))
        events_frame = ttk.Frame(side)
        events_frame.pack(fill="both", expand=True)
        self.events = tk.Text(
            events_frame,
            height=8,
            width=45,
            font=("Consolas", 9),
            state="disabled",
            background="#f8fafc",
            wrap="word",
        )
        events_scroll = ttk.Scrollbar(events_frame, orient="vertical", command=self.events.yview)
        self.events.configure(yscrollcommand=events_scroll.set)
        self.events.pack(side="left", fill="both", expand=True)
        events_scroll.pack(side="right", fill="y")
        ttk.Label(self.root, textvariable=self.status, padding=8, wraplength=1200).pack(fill="x")

    def open_scenario(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Select a yard scenario",
            filetypes=[("YAML scenarios", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if path:
            self.path.set(path)
            self.restart()

    def restart(self):
        if self.session is not None:
            self.session.playing = False
        try:
            files = (
                self.files
                if self.path.get() == self.files.label
                else select_scenario_files(self.path.get(), **self.wake_overrides)
            )
            self.apply_configuration(files)
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror("Cannot start scenario", str(exc), parent=self.root)

    def apply_configuration(self, files):
        """Construct the replacement before closing the previous valid session."""
        scenario = files.load(**self.wake_overrides)
        seed, speed = int(self.seed.get()), float(self.speed.get())
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("Playback speed must be positive and finite")
        name = self.policy_name.get()
        session = SimulationSession(
            scenario,
            lambda: create_policy(name, seed=seed, parameters=self.parameters),
            speed=speed,
            record=self.record,
            validate=self.validate,
        )
        if self.session is not None:
            self.session.close()
        self.session, self.files = session, files
        self.path.set(files.label)
        area_ids = tuple(area.id for area in scenario.unloading_areas)
        self.queue_selector.configure(values=area_ids)
        if self.queue_area.get() not in area_ids:
            self.queue_area.set(area_ids[0])
        self._event_count = 0
        self.events.configure(state="normal")
        self.events.delete("1.0", "end")
        self.events.configure(state="disabled")
        self._request = None
        self._last_frame = time.monotonic()
        self.status.set(
            "Choose feasible jobs and commit them, or choose Wait to leave all idle."
            if session.decision is not None
            else "Paused. Right arrow advances one timestamp; Space starts playback."
        )
        self._refresh_options()
        self.render()

    def open_configuration(self):
        if self.configuration_dialog is not None:
            return
        original = self.session
        was_playing = original is not None and original.playing
        if original is not None:
            original.playing = False

        def closed(accepted):
            self.configuration_dialog = None
            if not accepted and was_playing and self.session is original:
                original.play()

        self.configuration_dialog = ConfigurationDialog(
            self.root, self.files, self.apply_configuration, closed
        )

    def toggle_play(self):
        if self.session is None:
            return
        if self.session.playing:
            self.session.playing = False
        else:
            self.session.play()
        self._last_frame = time.monotonic()
        self.render()

    def step(self):
        if self.session is None:
            return
        try:
            self.session.step()
            self._refresh_options()
            self.render()
        except Exception as exc:
            self._error(exc)

    def _refresh_options(self):
        if self.session is None:
            return
        decision = self.session.decision
        for item in self.tasks.get_children():
            self.tasks.delete(item)
        self.draft.delete(0, "end")
        self._options = decision.options if decision is not None else []
        for index, a in enumerate(self._options):
            self.tasks.insert(
                "",
                "end",
                iid=str(index),
                values=(a.loader_id, a.source_id, a.destination_id, f"{a.quantity:.2f}"),
            )
        if decision is not None:
            for a in decision.assignments:
                self.draft.insert(
                    "end", f"{a.loader_id}: {a.source_id} → {a.destination_id} ({a.quantity:.2f} t)"
                )
        state = "normal" if decision is not None else "disabled"
        for widget in (self.add_button, self.undo_button, self.commit_button, self.wait_button):
            widget.configure(state=state)
        self._request = self.session.simulation.pending_decision

    def add_assignment(self):
        if self.session is None or self.session.decision is None:
            return
        selected = self.tasks.selection()
        if not selected:
            self.status.set("Select a feasible task, then choose Add.")
            return
        try:
            a = self._options[int(selected[0])]
            quantity = float(self.quantity.get()) if self.quantity.get().strip() else None
            self.session.decision.add(a, quantity)
            self.quantity.set("")
            self._refresh_options()
            self.status.set("Draft updated; the simulation remains paused until you commit.")
        except ValueError as exc:
            self.status.set(str(exc))

    def undo_assignment(self):
        if self.session is not None and self.session.decision is not None:
            self.session.decision.undo()
            self._refresh_options()

    def commit(self, wait=False):
        if self.session is None or self.session.decision is None:
            return
        try:
            self.session.submit(wait=wait)
            self._last_frame = time.monotonic()
            self._refresh_options()
            self.status.set(
                "Decision committed. Playback will pause again at the next operator decision."
            )
            self.render()
        except (ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def save(self):
        if self.session is None:
            return
        self.session.playing = False
        self.session.display_time = self.session.simulation.state.time
        try:
            path = save_run(self.session.simulation.result())
            self.status.set(
                f"Saved {'complete' if self.session.simulation.finished else 'partial'} run to {path}. Uncommitted draft choices are not applied."
            )
        except OSError as exc:
            self._error(exc)
        self.render()

    def _frame(self):
        if self._closing:
            return
        now = time.monotonic()
        elapsed, self._last_frame = min(0.25, now - self._last_frame), now
        if self.session is not None:
            try:
                try:
                    speed = float(self.speed.get())
                except ValueError:
                    speed = None
                if speed is not None and math.isfinite(speed) and speed > 0:
                    self.session.speed = speed
                self.session.tick(elapsed)
                if self.session.simulation.pending_decision is not self._request:
                    self._refresh_options()
                self.render()
            except Exception as exc:
                self._error(exc)
        self._timer = self.root.after(50, self._frame)

    def _error(self, error):
        if self.session is not None:
            self.session.playing = False
        self.status.set(f"Simulation error: {error}")
        messagebox.showerror("Simulation error", str(error), parent=self.root)

    @staticmethod
    def _editing(event):
        return event.widget.winfo_class() in ("Entry", "TEntry", "TSpinbox", "Text")

    def _right(self, event):
        if not self._editing(event):
            self.step()
            return "break"
        return None

    def _left(self, event):
        if not self._editing(event):
            self.status.set(
                "Backward replay is not implemented. Apply & restart reruns the scenario from time zero."
            )
            return "break"
        return None

    def _space(self, event):
        if not self._editing(event):
            self.toggle_play()
            return "break"
        return None

    def render(self):
        if self.session is None:
            return
        session, canvas = self.session, self.canvas
        sim, scenario = session.simulation, session.scenario
        state, display_time = sim.state, session.display_time
        width, height = max(300, canvas.winfo_width()), max(300, canvas.winfo_height())
        view = Viewport.fit(scenario, width, height - 90, margin=110)
        canvas.delete("all")
        canvas.create_text(
            14,
            14,
            text="WORLD VIEW · metres · schematic sizes",
            anchor="nw",
            fill="#4b6258",
            font=("Segoe UI", 10),
        )
        for stop in scenario.stops:
            x, y = view.point(stop.location)
            canvas.create_rectangle(
                x - 35, y - 19, x + 35, y + 19, outline="#84968e", dash=(4, 3), width=2
            )
            canvas.create_text(x, y + 34, text=stop.id, fill="#33463d")
            truck_id = state.stops[stop.id]
            if truck_id is not None:
                truck = state.trucks[truck_id]
                self._truck(x, y, truck.id, truck.remaining_quantity)
        for pile in scenario.piles:
            x, y = view.point(pile.location)
            inventory = state.piles[pile.id]
            canvas.create_oval(
                x - 37, y - 25, x + 37, y + 25, fill="#c9b28f", outline="#806843", width=2
            )
            if inventory.amount > 0:
                self._logs(
                    x - 22, y - 11, min(6, max(1, round(6 * inventory.amount / pile.capacity)))
                )
            self._bar(x, y + 31, inventory.amount, pile.capacity, "#947240")
            canvas.create_text(
                x,
                y + 55,
                text=f"{pile.id}\n{inventory.amount:.1f}/{pile.capacity:g} t",
                fill="#3c3428",
            )
        for infeed in scenario.infeeds:
            x, y = view.point(infeed.location)
            inventory = state.infeeds[infeed.id]
            amount = infeed_amount(inventory, infeed.consumption_rate, state.time, display_time)
            color = "#df5a52" if amount <= 1e-9 and infeed.consumption_rate else "#5c9d78"
            canvas.create_rectangle(
                x - 26, y - 18, x + 26, y + 18, fill="#bfd7c8", outline=color, width=3
            )
            if amount > 0:
                self._logs(x - 17, y - 9, min(4, max(1, round(amount / infeed.capacity * 4))))
            canvas.create_rectangle(
                x + 42, y - 34, x + 100, y + 22, fill="#c2c9d0", outline="#677786", width=2
            )
            canvas.create_polygon(
                x + 38, y - 34, x + 70, y - 53, x + 104, y - 34, fill="#8f9ba7", outline="#677786"
            )
            canvas.create_text(x + 70, y - 2, text="MILL", fill="#34424e")
            self._bar(x, y + 31, amount, infeed.capacity, color)
            canvas.create_text(
                x, y + 56, text=f"{infeed.id}\n{amount:.1f}/{infeed.capacity:g} t", fill="#294337"
            )
        for index, loader in enumerate(state.loaders.values()):
            point = loader_position(loader, state, scenario, display_time)
            x, y = view.point(point)
            x += (index % 3) * 14
            y -= 12 + (index % 2) * 12
            color = LOADER_COLORS[index % len(LOADER_COLORS)]
            canvas.create_rectangle(
                x - 14, y - 10, x + 14, y + 10, fill=color, outline="#3e4650", width=2
            )
            canvas.create_oval(x - 13, y + 6, x - 5, y + 14, fill="#34424e")
            canvas.create_oval(x + 5, y + 6, x + 13, y + 14, fill="#34424e")
            if loader.carried > 0:
                self._logs(x - 12, y - 19, 3)
            canvas.create_text(
                x + 20,
                y - 13,
                text=loader.id,
                anchor="w",
                fill="#17212b",
                font=("Segoe UI", 9, "bold"),
            )
        queue_y = height - 43
        queue = state.queues[self.queue_area.get()]
        canvas.create_line(8, height - 87, width - 8, height - 87, fill="#cbd5d1")
        canvas.create_text(
            12,
            height - 78,
            text=f"{self.queue_area.get()} — waiting for a bay: {len(queue)} truck(s)",
            anchor="nw",
            fill="#33463d",
        )
        visible = max(1, (width - 90) // 100)
        for index, truck_id in enumerate(queue[:visible]):
            self._truck(
                55 + index * 100, queue_y, truck_id, state.trucks[truck_id].remaining_quantity
            )
        if len(queue) > visible:
            canvas.create_text(width - 25, queue_y, text=f"+{len(queue) - visible}", fill="#33463d")
        mode = (
            "Operator decision"
            if session.decision
            else "Finished"
            if sim.finished
            else "Playing"
            if session.playing
            else "Paused"
        )
        self.clock.set(f"{mode}   {display_time:.2f} / {scenario.horizon:g} min")
        self.play_button.configure(
            text="Pause [Space]" if session.playing else "Play [Space]",
            state="disabled" if session.decision or sim.finished else "normal",
        )
        self.step_button.configure(
            state="disabled" if session.decision or sim.finished else "normal"
        )
        phases = " · ".join(
            f"{loader.id}: {loader.phase} ({loader.carried:.1f} t)"
            for loader in state.loaders.values()
        )
        metrics = live_metrics(sim, display_time)
        self.metrics.set(
            f"Engine time: {state.time:.2f} min · Trucks completed: {len(sim.completed_trucks)} · Still present: {len(state.trucks)}\n"
            f"Consumed: {sum(state.consumed.values()):.1f} t · Total mill starvation: {metrics['starvation']:.2f} min\n"
            f"Total truck wait: {metrics['truck_wait']:.2f} min · Total loader travel: {metrics['travel_distance']:.1f} m\n{phases}"
        )
        new_events = sim.recorder.events[self._event_count :]
        if new_events:
            following = self.events.yview()[1] >= 0.999
            self.events.configure(state="normal")
            self.events.insert(
                "end", "".join(f"{e['time']:8.3f}  {e['event']}\n" for e in new_events)
            )
            self.events.configure(state="disabled")
            self._event_count += len(new_events)
            if following:
                self.events.see("end")

    def _logs(self, x, y, count):
        for index in range(count):
            self.canvas.create_rectangle(
                x + index * 7, y, x + index * 7 + 5, y + 18, fill="#9a7144", outline="#674a2b"
            )

    def _bar(self, x, y, amount, capacity, color):
        self.canvas.create_rectangle(x - 35, y, x + 35, y + 7, fill="#e4e8e5", outline="#9aa69f")
        self.canvas.create_rectangle(
            x - 35,
            y,
            x - 35 + 70 * min(1, max(0, amount / capacity)),
            y + 7,
            fill=color,
            outline="",
        )

    def _truck(self, x, y, truck_id, quantity):
        self.canvas.create_rectangle(
            x - 33, y - 11, x + 22, y + 11, fill="#d8dfe6", outline="#536474", width=2
        )
        self.canvas.create_rectangle(
            x + 22, y - 9, x + 34, y + 11, fill="#567ca5", outline="#3b536f"
        )
        if quantity > 0:
            self._logs(x - 24, y - 9, 6)
        self.canvas.create_text(
            x, y + 19, text=f"{truck_id} · {quantity:.1f} t", font=("Segoe UI", 8), fill="#263645"
        )
        truck = self.session.simulation.state.trucks[truck_id]
        self.canvas.create_text(
            x,
            y + 33,
            text=f"Wait {truck_wait(truck, self.session.display_time):.1f} min",
            font=("Segoe UI", 8),
            fill="#8c4e20",
        )

    def close(self):
        self._closing = True
        if hasattr(self, "_timer"):
            self.root.after_cancel(self._timer)
        if self.session is not None:
            self.session.close()
        self.root.destroy()


def launch(scenario_path, **options):
    """Open the native desktop window; importing this module is CLI-lazy."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError(
            "The desktop GUI requires Tk and a graphical display. On Linux, install Tk for your Python and run in a desktop session."
        ) from exc
    try:
        YardApplication(root, Path(scenario_path) if scenario_path is not None else None, **options)
        root.mainloop()
    finally:
        with suppress(tk.TclError):
            root.destroy()
