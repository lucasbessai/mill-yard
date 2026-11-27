"""Plot fixed yard assets and the incumbent POI locations from a search."""

from pathlib import Path

from mill_yard.layouts.config import LayoutConfig
from mill_yard.layouts.optimizer import LayoutTrajectoryPoint


def plot_layout_trajectory(
    config: LayoutConfig,
    trajectory: tuple[LayoutTrajectoryPoint, ...],
    *,
    output: str | Path | None = None,
    show: bool = False,
):
    """Plot the search path for each selected POI and return ``(figure, axes)``.

    Each selected POI keeps one color throughout its path. Hollow and filled
    stars mark its starting and final positions; unselected yard locations
    remain in the background. ``output`` may be a PNG, SVG, or another
    Matplotlib-supported format. Set ``show`` to open an interactive window.
    """
    if not trajectory:
        raise ValueError("Cannot plot an empty layout trajectory")

    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    figure, axes = plt.subplots(figsize=(14, 9))
    selected = set(config.point_ids)

    for index, stop in enumerate(config.scenario.stops):
        axes.scatter(
            stop.location.x,
            stop.location.y,
            marker="^",
            color="tab:gray",
            s=65,
            label="Truck stops" if index == 0 else None,
        )
        axes.annotate(stop.id, (stop.location.x, stop.location.y), xytext=(4, 4), textcoords="offset points")
    fixed_piles = [pile for pile in config.scenario.piles if pile.id not in selected]
    for index, pile in enumerate(fixed_piles):
        axes.scatter(
            pile.location.x,
            pile.location.y,
            marker="s",
            color="tab:gray",
            s=65,
            label="Fixed piles" if index == 0 else None,
        )
        axes.annotate(pile.id, (pile.location.x, pile.location.y), xytext=(4, 4), textcoords="offset points")
    for index, infeed in enumerate(config.scenario.infeeds):
        axes.scatter(
            infeed.location.x,
            infeed.location.y,
            marker="D",
            color="tab:green",
            s=65,
            label="Infeeds" if index == 0 else None,
        )
        axes.annotate(infeed.id, (infeed.location.x, infeed.location.y), xytext=(4, 4), textcoords="offset points")
    for index, loader in enumerate(config.scenario.loaders):
        axes.scatter(
            loader.location.x,
            loader.location.y,
            marker="P",
            color="tab:orange",
            s=80,
            label="Loader starting locations" if index == 0 else None,
            zorder=2,
        )
        axes.annotate(loader.id, (loader.location.x, loader.location.y), xytext=(4, -10), textcoords="offset points")
    for index, area in enumerate(config.scenario.unloading_areas):
        location = area.queue_location
        axes.scatter(
            location.x,
            location.y,
            marker="X",
            color="tab:purple",
            s=75,
            label="Unloading-area queue anchors" if index == 0 else None,
            zorder=2,
        )
        axes.annotate(area.id, (location.x, location.y), xytext=(4, 4), textcoords="offset points")

    for poi_index, poi_id in enumerate(config.point_ids):
        xs = [point.vector[2 * poi_index] for point in trajectory]
        ys = [point.vector[2 * poi_index + 1] for point in trajectory]
        poi_color = f"C{poi_index}"
        axes.plot(xs, ys, color=poi_color, alpha=0.55, linewidth=1.5)
        axes.scatter(
            xs,
            ys,
            color=poi_color,
            marker="o",
            s=42,
            edgecolors=poi_color,
            linewidths=1.2,
            label=f"{poi_id} incumbent path",
            zorder=3,
        )
        axes.scatter(
            [xs[0]],
            [ys[0]],
            marker="D",
            s=90,
            facecolors="none",
            edgecolors=poi_color,
            linewidths=1.7,
            zorder=4,
        )
        axes.scatter(
            [xs[-1]],
            [ys[-1]],
            marker="*",
            s=150,
            color=poi_color,
            edgecolors="black",
            linewidths=0.5,
            zorder=5,
        )
        axes.annotate(
            f"{poi_id} start",
            (xs[0], ys[0]),
            xytext=(5, -12),
            textcoords="offset points",
            fontsize=8,
            color=poi_color,
        )
        axes.annotate(
            f"{poi_id} final",
            (xs[-1], ys[-1]),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=9,
            fontweight="bold",
            color=poi_color,
        )

    axes.add_patch(
        Rectangle(
            (config.x_bounds[0], config.y_bounds[0]),
            config.x_bounds[1] - config.x_bounds[0],
            config.y_bounds[1] - config.y_bounds[0],
            fill=False,
            linestyle="--",
            linewidth=1,
            edgecolor="black",
        )
    )
    final = trajectory[-1]
    violation_total = sum(final.violations)
    axes.set_title(
        f"Layout search: objective {final.objective:.3g}, "
        f"constraint violation {violation_total:.3g}"
    )
    axes.set_xlabel("x (m)")
    axes.set_ylabel("y (m)")
    axes.set_xlim(config.x_bounds)
    axes.set_ylim(config.y_bounds)
    axes.set_aspect("equal", adjustable="box")
    axes.grid(True, alpha=0.2)
    axes.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0)

    if output is not None:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output_path, bbox_inches="tight")
    if show:
        plt.show()
    return figure, axes
