"""Arcade drawing primitives and a zoomable schematic of the live yard."""

import arcade

from mill_yard.gui.controls import ScreenRect
from mill_yard.gui.presentation import infeed_amount, loader_position, mill_starvation, truck_wait

INK = (35, 52, 65)
MUTED = (88, 107, 119)
GREEN = (44, 124, 95)
AMBER = (225, 146, 42)
WHITE = (255, 255, 255)
LOADER_COLORS = ((235, 175, 53), (72, 145, 205), (176, 110, 201), (220, 115, 85))


class Drawing:
    """Reuse text objects by semantic key, with no cache growth between frames."""

    def __init__(self):
        self.labels = {}
        self.used = set()

    def begin(self):
        self.used.clear()

    def end(self):
        for key in self.labels.keys() - self.used:
            del self.labels[key]

    def text(self, key, text, x, y, *, size=11, color=INK, bold=False):
        self.used.add(key)
        label = self.labels.get(key)
        if label is None:
            label = self.labels[key] = arcade.Text(
                str(text),
                x,
                y,
                color,
                size,
                font_name="Arial",
                bold=bold,
                anchor_y="center",
            )
        else:
            label.text, label.x, label.y, label.color = str(text), x, y, color
            label.font_size = size
        label.draw()

    @staticmethod
    def box(rect, color, outline=None):
        arcade.draw_lbwh_rectangle_filled(rect.x, rect.y, rect.width, rect.height, color)
        if outline:
            arcade.draw_lbwh_rectangle_outline(rect.x, rect.y, rect.width, rect.height, outline, 1)


class YardRenderer(Drawing):
    """Draw equipment, clickable loaders, and feasible dispatch highlights."""

    def draw(
        self,
        session,
        camera,
        rect,
        decision_loaders,
        selected_loader,
        options=(),
        selected_pickup=None,
    ):
        self.begin()
        self.flags = {}
        self.loaders = {}
        self.targets = {}
        sim, scenario = session.simulation, session.scenario
        state, now = sim.state, session.display_time
        # Shapes scale with zoom; labels retain legible screen sizes.
        size = camera.zoom**0.5
        if len(scenario.unloading_areas) > 1:
            for area in scenario.unloading_areas:
                x, y = camera.point(area.queue_location, rect)
                self.text(
                    f"area:{area.id}",
                    f"{area.id} / {len(state.queues[area.id])} waiting",
                    x - 60,
                    y + 54 * size,
                    size=10,
                    color=GREEN,
                    bold=True,
                )
        for stop in scenario.stops:
            x, y = camera.point(stop.location, rect)
            self.box(
                ScreenRect(x - 40 * size, y - 22 * size, 80 * size, 44 * size),
                (220, 230, 225),
                (135, 155, 145),
            )
            self.text(f"stop:{stop.id}", stop.id, x - 38 * size, y + 34 * size)
            truck_id = state.stops[stop.id]
            if truck_id is not None:
                self.truck(state.trucks[truck_id], now, x, y, size)
                self.targets[("truck", truck_id)] = ScreenRect(
                    x - 38 * size, y - 17 * size, 80 * size, 34 * size
                )
        for pile in scenario.piles:
            x, y = camera.point(pile.location, rect)
            inventory = state.piles[pile.id]
            self.targets[("pile", pile.id)] = ScreenRect(
                x - 44 * size, y - 29 * size, 88 * size, 58 * size
            )
            arcade.draw_ellipse_filled(x, y, 80 * size, 50 * size, (198, 175, 138))
            self.logs(x - 25 * size, y - 8 * size, inventory.amount, size)
            self.bar(x, y - 37 * size, inventory.amount, pile.capacity, size, (153, 112, 62))
            self.text(
                f"pile:{pile.id}",
                f"{pile.id}: {inventory.amount:.1f}/{pile.capacity:g} t",
                x - 50 * size,
                y - 56 * size,
            )
        for spec in scenario.infeeds:
            x, y = camera.point(spec.location, rect)
            self.targets[("infeed", spec.id)] = ScreenRect(
                x - 32 * size, y - 22 * size, 64 * size, 44 * size
            )
            amount = infeed_amount(state.infeeds[spec.id], spec.consumption_rate, state.time, now)
            color = GREEN if amount > 1e-9 or not spec.consumption_rate else (196, 72, 66)
            self.box(
                ScreenRect(x - 28 * size, y - 18 * size, 56 * size, 36 * size),
                (191, 217, 199),
                color,
            )
            self.logs(x - 19 * size, y - 9 * size, amount, size)
            self.box(
                ScreenRect(x + 45 * size, y - 27 * size, 64 * size, 54 * size),
                (186, 201, 210),
                (95, 116, 131),
            )
            arcade.draw_triangle_filled(
                x + 40 * size,
                y + 27 * size,
                x + 77 * size,
                y + 47 * size,
                x + 114 * size,
                y + 27 * size,
                (124, 147, 161),
            )
            self.text(f"building:{spec.id}", "MILL", x + 54 * size, y)
            self.bar(x, y - 37 * size, amount, spec.capacity, size, color)
            self.text(
                f"infeed:{spec.id}",
                f"{spec.id}: {amount:.1f}/{spec.capacity:g} t",
                x - 48 * size,
                y - 56 * size,
            )
            if spec.consumption_rate > 0 and amount <= 1e-9:
                self.text(
                    f"starvation:{spec.id}",
                    f"Starved total {mill_starvation(state, spec, now):.1f} min",
                    x - 48 * size,
                    y - 75 * size,
                    size=10,
                    color=(177, 61, 51),
                )
        loader_options = [a for a in options if a.loader_id == selected_loader]
        pickups = {(a.source_kind, a.source_id) for a in loader_options}
        deposits = {
            (a.destination_kind, a.destination_id)
            for a in loader_options
            if (a.source_kind, a.source_id) == selected_pickup
        }
        self.pickups = pickups
        self.deposits = deposits
        for key, target in self.targets.items():
            color = (
                AMBER
                if key == selected_pickup
                else GREEN
                if key in deposits
                else (52, 133, 219)
                if key in pickups and selected_pickup is None
                else None
            )
            if color:
                arcade.draw_lbwh_rectangle_outline(
                    target.x - 4, target.y - 4, target.width + 8, target.height + 8, color, 4
                )
        for index, loader in enumerate(state.loaders.values()):
            x, y = camera.point(loader_position(loader, state, scenario, now), rect)
            x += (50 + (index % 3) * 20) * size
            y += (22 + (index % 2) * 24) * size
            color = LOADER_COLORS[index % len(LOADER_COLORS)]
            body = ScreenRect(x - 16 * size, y - 10 * size, 32 * size, 20 * size)
            self.loaders[loader.id] = body
            self.box(body, color, INK)
            if loader.id == selected_loader:
                arcade.draw_lbwh_rectangle_outline(
                    body.x - 5, body.y - 5, body.width + 10, body.height + 10, GREEN, 3
                )
            for wheel in (-10, 10):
                arcade.draw_circle_filled(x + wheel * size, y - 10 * size, 5 * size, INK)
            self.logs(x - 11 * size, y + 10 * size, loader.carried, size)
            self.text(f"loader:{loader.id}", loader.id, x + 21 * size, y + 1)
            self.text(f"phase:{loader.id}", loader.phase, x + 21 * size, y - 16, size=9)
            if loader.id in decision_loaders:
                flag = ScreenRect(x - 5, y + 34 * size, 94, 24)
                arcade.draw_line(x - 5, y + 12 * size, flag.x, flag.y + 24, INK, 2)
                self.box(flag, GREEN if loader.id == selected_loader else AMBER, INK)
                self.text(
                    f"flag:{loader.id}",
                    "Choose task",
                    flag.x + 7,
                    flag.y + 12,
                    color=WHITE if loader.id == selected_loader else INK,
                    bold=True,
                )
                self.flags[loader.id] = flag
        self.end()

    def truck(self, truck, now, x, y, size=1):
        self.box(
            ScreenRect(x - 33 * size, y - 11 * size, 56 * size, 22 * size),
            (210, 222, 231),
            (87, 109, 128),
        )
        self.box(
            ScreenRect(x + 23 * size, y - 10 * size, 14 * size, 21 * size), (75, 124, 169), INK
        )
        self.logs(x - 25 * size, y - 8 * size, truck.remaining_quantity, size)
        self.text(
            f"truck:{truck.id}",
            f"{truck.id} / {truck.remaining_quantity:.1f} t",
            x - 38 * size,
            y - 25 * size,
            size=10,
        )
        self.text(
            f"wait:{truck.id}",
            f"Wait {truck_wait(truck, now):.1f} min",
            x - 38 * size,
            y - 41 * size,
            size=10,
            color=(140, 78, 32),
        )

    @staticmethod
    def logs(x, y, quantity, size):
        if quantity > 1e-9:
            for i in range(4):
                arcade.draw_lbwh_rectangle_filled(
                    x + i * 9 * size, y, 6 * size, 16 * size, (150, 108, 61)
                )

    @staticmethod
    def bar(x, y, amount, capacity, size, color):
        arcade.draw_lbwh_rectangle_filled(x - 36 * size, y, 72 * size, 7 * size, (220, 225, 222))
        arcade.draw_lbwh_rectangle_filled(
            x - 36 * size, y, 72 * size * min(1, max(0, amount / capacity)), 7 * size, color
        )
