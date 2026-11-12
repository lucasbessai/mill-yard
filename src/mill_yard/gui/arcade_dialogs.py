"""Arcade scenario and metric dialogs; no Tk installation is required."""

from pathlib import Path
from textwrap import wrap

import arcade
import yaml

from mill_yard.gui.arcade_scene import GREEN, INK, MUTED, WHITE, Drawing
from mill_yard.gui.configuration import COMPONENT_LABELS, ConfigurationDraft
from mill_yard.gui.controls import ScreenRect, ScrollList
from mill_yard.gui.presentation import live_metrics, metric_breakdown


class ScenarioDialog:
    """Browse folders or type a path, keeping load errors inside the chooser."""

    def __init__(
        self,
        directory,
        on_open,
        *,
        title="Open scenario",
        message="Loading starts a fresh run with the selected policy.",
    ):
        self.on_open = on_open
        self.title = title
        self.message = message
        self.drawing = Drawing()
        self.scroll = ScrollList(follow=False)
        self.error = ""
        self.done = self.accepted = False
        self.editing = self.select_all = True
        self.selected = None
        self.directory, self.entries, self.path_text = Path.cwd(), [], ""
        self.navigate(directory)

    def navigate(self, directory):
        try:
            directory = Path(directory).expanduser().resolve()
            entries = [(p, p.is_dir()) for p in directory.iterdir()]
            entries = [
                (p, is_dir)
                for p, is_dir in entries
                if is_dir or p.suffix.lower() in (".yaml", ".yml")
            ]
            entries.sort(key=lambda item: (not item[1], item[0].name.lower()))
        except OSError as exc:
            self.error = str(exc)
            return
        self.directory, self.entries = directory, entries
        self.path_text = str(directory)
        self.selected = None
        self.scroll = ScrollList(follow=False)
        self.error = ""

    def draw(self, window):
        d = self.drawing
        d.begin()
        width, height = min(800, window.width - 80), min(580, window.height - 100)
        x, y = (window.width - width) / 2, (window.height - height) / 2
        top = y + height
        d.box(ScreenRect(0, 0, window.width, window.height), (20, 35, 45, 130))
        d.box(ScreenRect(x, y, width, height), (247, 250, 252), MUTED)
        d.text("title", self.title, x + 18, top - 28, size=16, bold=True)
        d.text(
            "help",
            "Browse folders or type a YAML path. Enter / Open selects the file.",
            x + 18,
            top - 55,
            size=10,
            color=MUTED,
        )
        self.path_rect = ScreenRect(x + 18, top - 104, width - 110, 32)
        self.up_rect = ScreenRect(x + width - 80, top - 104, 62, 32)
        self.list_rect = ScreenRect(x + 18, y + 104, width - 36, height - 226)
        self.open_rect = ScreenRect(x + width - 211, y + 18, 90, 32)
        self.cancel_rect = ScreenRect(x + width - 108, y + 18, 90, 32)
        d.box(
            self.path_rect,
            (220, 238, 251) if self.editing and self.select_all else WHITE,
            GREEN if self.editing else MUTED,
        )
        window._clip(self.path_rect)
        d.text("path", self.path_text, self.path_rect.x + 7, self.path_rect.y + 16, size=10)
        window.ctx.scissor = None
        for key, label, rect in (
            ("up", "Up", self.up_rect),
            ("open", "Open", self.open_rect),
            ("cancel", "Cancel", self.cancel_rect),
        ):
            d.box(rect, GREEN if key == "open" else (220, 231, 237), MUTED)
            d.text(key, label, rect.x + 14, rect.y + 16, color=WHITE if key == "open" else INK)
        d.box(self.list_rect, WHITE, (200, 213, 220))
        self.scroll.sync(len(self.entries), int(self.list_rect.height // 30))
        self.rows = []
        for row, index in enumerate(self.scroll.visible):
            path, is_dir = self.entries[index]
            rect = ScreenRect(
                self.list_rect.x + 2,
                self.list_rect.y + self.list_rect.height - (row + 1) * 30,
                self.list_rect.width - 18,
                29,
            )
            if path == self.selected:
                d.box(rect, (211, 235, 224))
            d.text(
                f"entry:{row}",
                ("[Folder]  " if is_dir else "[YAML]    ") + path.name[:90],
                rect.x + 8,
                rect.y + 15,
                size=11,
            )
            self.rows.append((rect, path, is_dir))
        if self.scroll.maximum:
            d.text(
                "scroll",
                f"{self.scroll.offset + 1}-{min(len(self.entries), self.scroll.offset + self.scroll.capacity)}"
                f" / {len(self.entries)} (wheel to scroll)",
                x + 18,
                y + 83,
                size=10,
                color=MUTED,
            )
        message = (self.error or self.message).replace("\n", " ")
        d.text(
            "message",
            message if len(message) <= 100 else "..." + message[-97:],
            x + 18,
            y + 64,
            size=10,
            color=(176, 63, 53) if self.error else MUTED,
        )
        d.end()

    def accept(self):
        try:
            path = Path(self.path_text).expanduser()
            if not path.is_absolute():
                path = self.directory / path
            if path.is_dir():
                self.navigate(path)
                return
            if path.suffix.lower() not in (".yaml", ".yml"):
                raise ValueError("Choose a .yaml or .yml scenario file")
            self.on_open(path)
        except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
            self.error = str(exc)
            return
        self.done = self.accepted = True

    def click(self, x, y):
        if self.cancel_rect.contains(x, y):
            self.done = True
        elif self.open_rect.contains(x, y):
            self.accept()
        elif self.up_rect.contains(x, y):
            self.navigate(self.directory.parent)
        elif self.path_rect.contains(x, y):
            self.editing = self.select_all = True
        else:
            self.editing = False
            for rect, path, is_dir in self.rows:
                if rect.contains(x, y):
                    if is_dir:
                        self.navigate(path)
                    else:
                        self.selected, self.path_text = path, str(path)
                    break

    def text(self, text):
        if self.editing:
            if self.select_all:
                self.path_text, self.select_all = "", False
            self.path_text += "".join(c for c in text if c.isprintable())

    def key(self, symbol, modifiers):
        if symbol == arcade.key.ESCAPE:
            self.done = True
        elif symbol == arcade.key.ENTER:
            self.accept()
        elif self.editing:
            if symbol == arcade.key.A and modifiers & (
                arcade.key.MOD_CTRL | arcade.key.MOD_COMMAND
            ):
                self.select_all = True
            elif symbol == arcade.key.BACKSPACE:
                self.path_text = "" if self.select_all else self.path_text[:-1]
                self.select_all = False
            elif symbol == arcade.key.DELETE:
                self.path_text = ""


class ConfigurationDialog:
    """Stage independent inputs; apply only after complete compatibility checks."""

    def __init__(self, files, on_apply):
        self.draft = ConfigurationDraft.from_files(files)
        self.on_apply = on_apply
        self.drawing = Drawing()
        self.browser = None
        self._scroll = ScrollList(follow=False)
        self.done = self.accepted = False
        self.selected_field = None
        self.select_all = False
        self.fields, self.browse_buttons = {}, {}
        self.refresh()

    @property
    def scroll(self):
        return self.browser.scroll if self.browser is not None else self._scroll

    def refresh(self):
        self.valid, self.message = self.draft.validate()

    def draw(self, window):
        if self.browser is not None:
            self.browser.draw(window)
            return
        d = self.drawing
        d.begin()
        width, height = min(960, window.width - 80), 500
        x, y = (window.width - width) / 2, (window.height - height) / 2
        top = y + height
        d.box(ScreenRect(0, 0, window.width, window.height), (20, 35, 45, 130))
        d.box(ScreenRect(x, y, width, height), (247, 250, 252), MUTED)
        d.text("title", "Simulation inputs", x + 20, top - 30, size=16, bold=True)
        d.text(
            "help",
            "Choose three inputs, then Apply & restart. The current run is preserved until then.",
            x + 20,
            top - 58,
            size=10,
            color=MUTED,
        )
        self.fields, self.browse_buttons = {}, {}
        for index, (key, label) in enumerate(COMPONENT_LABELS.items()):
            row_y = top - 130 - index * 72
            d.text(f"label:{key}", label, x + 20, row_y + 46, bold=True)
            rect = ScreenRect(x + 20, row_y, width - 150, 32)
            self.fields[key] = rect
            d.box(rect, (220, 238, 251) if self.selected_field == key else WHITE, MUTED)
            value = getattr(self.draft, key)
            display = "..." + value[-95:] if len(value) > 98 else value
            window._clip(rect)
            d.text(
                f"value:{key}", display or "Choose a YAML file", rect.x + 7, rect.y + 16, size=10
            )
            window.ctx.scissor = None
            button = ScreenRect(x + width - 118, row_y, 98, 32)
            self.browse_buttons[key] = button
            d.box(button, (220, 231, 237), MUTED)
            d.text(f"browse:{key}", "Browse...", button.x + 10, button.y + 16)
        d.text(
            "horizon-label",
            "Horizon override (minutes; blank uses schedule)",
            x + 20,
            y + 192,
            size=10,
        )
        rect = ScreenRect(x + 20, y + 140, 160, 32)
        self.fields["horizon"] = rect
        d.box(rect, (220, 238, 251) if self.selected_field == "horizon" else WHITE, MUTED)
        d.text(
            "horizon", self.draft.horizon or "Schedule default", rect.x + 7, rect.y + 16, size=10
        )
        for index, line in enumerate(
            wrap(self.message.replace("\n", " "), max(40, int((width - 40) / 7)))[:3]
        ):
            d.text(
                f"message:{index}",
                line,
                x + 20,
                y + 116 - index * 18,
                size=10,
                color=GREEN if self.valid else (176, 63, 53),
            )
        self.apply_rect = ScreenRect(x + width - 280, y + 20, 160, 32)
        self.cancel_rect = ScreenRect(x + width - 108, y + 20, 88, 32)
        d.box(self.apply_rect, GREEN if self.valid else (220, 231, 237), MUTED)
        d.text(
            "apply",
            "Apply & restart",
            self.apply_rect.x + 12,
            self.apply_rect.y + 16,
            color=WHITE if self.valid else MUTED,
        )
        d.box(self.cancel_rect, (220, 231, 237), MUTED)
        d.text("cancel", "Cancel", self.cancel_rect.x + 12, self.cancel_rect.y + 16)
        d.end()

    def browse(self, kind):
        self.browser = ScenarioDialog(
            self.draft.directory(kind),
            lambda path: self.draft.choose(kind, path),
            title=f"Select {COMPONENT_LABELS[kind].lower()}",
            message="Selecting this input stages changes; Apply & restart starts the run.",
        )

    def _finish_browser(self):
        if self.browser is not None and self.browser.done:
            self.browser = None
            self.refresh()

    def apply(self):
        self.refresh()
        if not self.valid:
            return
        try:
            self.on_apply(self.draft.files())
        except (OSError, ValueError, RuntimeError) as exc:
            self.valid, self.message = False, str(exc)
            return
        self.done = self.accepted = True

    def click(self, x, y):
        if self.browser is not None:
            self.browser.click(x, y)
            self._finish_browser()
            return
        if self.cancel_rect.contains(x, y):
            self.done = True
        elif self.apply_rect.contains(x, y):
            self.apply()
        else:
            self.selected_field = None
            for key, rect in self.browse_buttons.items():
                if rect.contains(x, y):
                    self.browse(key)
                    return
            for key, rect in self.fields.items():
                if rect.contains(x, y):
                    self.selected_field, self.select_all = key, True
                    return

    def text(self, text):
        if self.browser is not None:
            self.browser.text(text)
            return
        if self.selected_field is not None:
            value = "" if self.select_all else getattr(self.draft, self.selected_field)
            setattr(
                self.draft, self.selected_field, value + "".join(c for c in text if c.isprintable())
            )
            self.select_all = False
            self.refresh()

    def key(self, symbol, modifiers):
        if self.browser is not None:
            self.browser.key(symbol, modifiers)
            self._finish_browser()
            return
        if symbol == arcade.key.ESCAPE:
            self.done = True
        elif symbol == arcade.key.ENTER:
            self.apply()
        elif self.selected_field is not None:
            if symbol == arcade.key.A and modifiers & (
                arcade.key.MOD_CTRL | arcade.key.MOD_COMMAND
            ):
                self.select_all = True
            elif symbol in (arcade.key.BACKSPACE, arcade.key.DELETE):
                value = getattr(self.draft, self.selected_field)
                setattr(
                    self.draft,
                    self.selected_field,
                    "" if self.select_all or symbol == arcade.key.DELETE else value[:-1],
                )
                self.select_all = False
                self.refresh()


class MetricDialog:
    """A scrollable, detached snapshot of individual asset performance."""

    def __init__(self, kind, simulation, display_time):
        self.kind = kind
        self.time = display_time
        self.totals = live_metrics(simulation, display_time)
        details = metric_breakdown(simulation, display_time)
        if kind == "truck_wait":
            self.title = "Truck waiting time"
            self.help = "Completed and still-present trucks; pickup service is excluded."
            self.rows = details["trucks"]
            self.columns = (
                ("Truck", "id"),
                ("Area", "area_id"),
                ("Status", "status"),
                ("Wait (min)", "waiting_time"),
            )
            self.total = f"Total truck wait: {self.totals['truck_wait']:.2f} min"
        elif kind == "loader_travel":
            self.title = "Loader travel"
            self.help = "Empty and loaded travel, including progress on current travel legs."
            self.rows = details["loaders"]
            self.columns = (
                ("Loader", "id"),
                ("Phase", "phase"),
                ("Distance (m)", "travel_distance"),
                ("Travel (min)", "travel_time"),
            )
            self.total = (
                f"Total loader travel: {self.totals['travel_distance']:.1f} m / "
                f"{self.totals['travel_time']:.2f} min"
            )
        elif kind == "mill_starvation":
            self.title = "Mill starvation"
            self.help = "Cumulative starvation by mill / infeed; inactive mills do not accrue time."
            self.rows = details["mills"]
            self.columns = (
                ("Mill / infeed", "id"),
                ("Status", "status"),
                ("Starvation (min)", "starvation"),
            )
            self.total = f"Total mill starvation: {self.totals['starvation']:.2f} min"
        else:
            raise ValueError(f"Unknown metric: {kind}")
        self.done = False
        self.dragging = False
        self.scroll = ScrollList(follow=False)
        self.drawing = Drawing()

    def draw(self, window):
        d = self.drawing
        d.begin()
        width, height = min(900, window.width - 80), min(580, window.height - 100)
        x, y = (window.width - width) / 2, (window.height - height) / 2
        top = y + height
        d.box(ScreenRect(0, 0, window.width, window.height), (20, 35, 45, 130))
        d.box(ScreenRect(x, y, width, height), (247, 250, 252), MUTED)
        d.text("title", self.title, x + 18, top - 28, size=16, bold=True)
        d.text("help", self.help, x + 18, top - 57, size=10, color=MUTED)
        d.text(
            "time",
            f"At {self.time:.2f} simulation min | cumulative totals",
            x + 18,
            top - 80,
            size=10,
            color=MUTED,
        )
        self.table_rect = ScreenRect(x + 18, y + 112, width - 36, height - 244)
        self.close_rect = ScreenRect(x + width - 108, y + 18, 90, 32)
        self.track_rect = ScreenRect(
            self.table_rect.x + self.table_rect.width - 12,
            self.table_rect.y,
            12,
            self.table_rect.height,
        )
        d.box(self.table_rect, WHITE, (200, 213, 220))
        column_width = (self.table_rect.width - 14) / len(self.columns)
        for col, (title, _) in enumerate(self.columns):
            d.text(
                f"heading:{col}",
                title,
                self.table_rect.x + 8 + col * column_width,
                self.table_rect.y + self.table_rect.height + 17,
                bold=True,
            )
        self.scroll.sync(len(self.rows), int(self.table_rect.height // 30))
        for row, index in enumerate(self.scroll.visible):
            bottom = self.table_rect.y + self.table_rect.height - (row + 1) * 30
            if row % 2 == 0:
                d.box(
                    ScreenRect(self.table_rect.x + 1, bottom, self.table_rect.width - 14, 30),
                    (233, 242, 245),
                )
            for col, (_, field) in enumerate(self.columns):
                cell = ScreenRect(self.table_rect.x + col * column_width, bottom, column_width, 30)
                window._clip(cell)
                value = self.rows[index][field]
                d.text(
                    f"cell:{row}:{col}",
                    f"{value:.2f}" if isinstance(value, float) else str(value),
                    cell.x + 8,
                    bottom + 15,
                    size=10,
                )
                window.ctx.scissor = None
        if not self.rows:
            d.text(
                "empty",
                "No trucks have arrived yet.",
                self.table_rect.x + 10,
                self.table_rect.y + self.table_rect.height - 24,
                color=MUTED,
            )
        if self.scroll.maximum:
            d.box(self.track_rect, (207, 219, 226))
            thumb = max(18, self.track_rect.height * self.scroll.capacity / self.scroll.count)
            thumb_top = (
                self.track_rect.y
                + self.track_rect.height
                - (self.track_rect.height - thumb) * self.scroll.offset / self.scroll.maximum
            )
            d.box(ScreenRect(self.track_rect.x + 2, thumb_top - thumb, 8, thumb), (117, 144, 159))
        first = self.scroll.offset + 1 if self.rows else 0
        d.text(
            "range",
            f"Showing {first}-{min(len(self.rows), self.scroll.offset + self.scroll.capacity)}"
            f" of {len(self.rows)} | Wheel / Up / Down / Home / End",
            x + 18,
            y + 87,
            size=10,
            color=MUTED,
        )
        d.text("total", self.total, x + 18, y + 61, bold=True)
        d.box(self.close_rect, GREEN, MUTED)
        d.text("close", "Close", self.close_rect.x + 18, self.close_rect.y + 16, color=WHITE)
        d.end()

    def click(self, x, y):
        if self.close_rect.contains(x, y):
            self.done = True
        elif self.scroll.maximum and self.track_rect.contains(x, y):
            self.dragging = True
            self.drag(y)

    def drag(self, y):
        if self.dragging:
            thumb = max(18, self.track_rect.height * self.scroll.capacity / self.scroll.count)
            self.scroll.seek(
                (self.track_rect.y + self.track_rect.height - y - thumb / 2)
                / max(1, self.track_rect.height - thumb)
            )

    def key(self, symbol):
        if symbol in (arcade.key.ESCAPE, arcade.key.ENTER):
            self.done = True
        elif symbol in (arcade.key.HOME, arcade.key.END):
            self.scroll.seek(0 if symbol == arcade.key.HOME else 1)
        elif symbol in (arcade.key.UP, arcade.key.DOWN, arcade.key.PAGEUP, arcade.key.PAGEDOWN):
            rows = self.scroll.capacity if symbol in (arcade.key.PAGEUP, arcade.key.PAGEDOWN) else 1
            self.scroll.scroll(rows if symbol in (arcade.key.DOWN, arcade.key.PAGEDOWN) else -rows)
