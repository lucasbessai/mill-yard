"""Generate weight sensitivity and in-sample operating studies from a saved OMADS batch."""

import argparse
import csv
import json
from dataclasses import asdict, fields
from math import isclose, isfinite
from pathlib import Path
from statistics import fmean

import matplotlib
import numpy as np

from mill_yard.config.loader import default_traffic_paths, load_yard_scenario, traffic_scenario_id
from mill_yard.optimization.objective import ObjectiveParameters, ObjectiveScore, evaluate_objective
from mill_yard.policies.optimizable import FeatureParameters, OptimizablePolicy
from mill_yard.simulation.engine import run_yard

WEIGHTS = ("truck", "mill", "inventory", "cost")
SWEEPS = {
    "truck": (0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.7, 0.9, 1),
    "mill": (0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1),
}
TRAFFIC = ("light", "medium", "heavy")
SCENARIO_IDS = tuple(f"{traffic}_{n}loader" for traffic in TRAFFIC for n in (1, 2))
METRICS = tuple(field.name for field in fields(ObjectiveScore))
RAW_METRICS = ("truck_wait_minutes", "travel_metres", "shortfall_tonnes")
INITIAL_COLOR = "#788793"
OPTIMIZED_COLOR = "#00766e"


def check_weights(weights):
    if (
        len(weights) != len(WEIGHTS)
        or any(not isfinite(value) or value < 0 for value in weights)
        or not isclose(sum(weights), 1, rel_tol=0, abs_tol=1e-12)
    ):
        raise ValueError(f"Invalid simplex weights {weights}")


def redistribute(reference, index, value):
    if not 0 <= value <= 1:
        raise ValueError("Trial weight must be in [0, 1]")
    if value == reference[index]:
        return reference
    if reference[index] == 1:
        raise ValueError("Proportional redistribution is undefined at a pure reference weight")
    scale = (1 - value) / (1 - reference[index])
    weights = tuple(value if j == index else weight * scale for j, weight in enumerate(reference))
    check_weights(weights)
    return weights


def check_match(actual, expected, label):
    if not isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError(f"{label} differs from expected value: {actual:.16g} vs {expected:.16g}")


def score_row(score):
    return {"objective": score.total, **asdict(score)}


def saved_score(values, total, label, simulate):
    if all(name in values and isfinite(values[name]) for name in METRICS):
        score = ObjectiveScore(**{name: values[name] for name in METRICS})
        if total is None or isclose(score.total, total, rel_tol=1e-10, abs_tol=1e-10):
            return score
    score = simulate()
    if total is not None:
        check_match(score.total, total, label)
    return score


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def scenario_for_comparison(data):
    """Compare resolved inputs and source hashes across checkout locations."""
    return {
        **data,
        "configuration_sources": [
            {key: value for key, value in source.items() if key != "path"}
            for source in data.get("configuration_sources", [])
        ],
    }


def run_studies(source):
    payload = json.loads(source.read_text(encoding="utf-8"))
    saved = payload.get("omads", payload)
    if saved["mode"] != "batch" or not saved.get("algorithm", "").startswith("OMADS"):
        raise ValueError("Expected a saved OMADS batch result")
    configs = {entry["id"]: entry["scenario"] for entry in payload["scenarios"]}
    if set(configs) != set(SCENARIO_IDS) or len(payload["scenarios"]) != len(SCENARIO_IDS):
        raise ValueError("Saved result must contain each of the six traffic scenarios once")
    paths = {traffic_scenario_id(path): path for path in default_traffic_paths()}
    scenarios = {name: load_yard_scenario(paths[name]) for name in SCENARIO_IDS}

    def check_scenarios():
        for name, scenario in scenarios.items():
            current = json.loads(json.dumps(asdict(scenario)))
            if scenario_for_comparison(current) != scenario_for_comparison(configs[name]):
                raise ValueError(
                    f"Scenario {name} differs from the saved configuration. "
                    "Generate a new batch result with the current simulation and presets."
                )

    check_scenarios()
    features = FeatureParameters(**saved["features"])
    objective = ObjectiveParameters(**saved["objective_parameters"])
    reference, initial = tuple(saved["theta"]), tuple(saved["initial_theta"])
    check_weights(reference)
    check_weights(initial)

    def evaluate(name, weights):
        policy = OptimizablePolicy(weights, features)
        return evaluate_objective(run_yard(scenarios[name], policy, record="summary"), objective)

    def evaluate_batch(weights):
        return {name: evaluate(name, weights) for name in scenarios}

    # Replay independently for reproducibility and validate before generating output.
    replay = evaluate_batch(reference)
    if replay != evaluate_batch(reference):
        raise ValueError("The saved policy replay differed between repeated evaluations")
    check_match(
        fmean(score.total for score in replay.values()), saved["objective_total"], "Batch objective"
    )
    saved_scores = {row["scenario_id"]: row for row in saved.get("scenario_scores", [])}
    operating = []
    for name, replayed in replay.items():
        row = saved_scores.get(name, {})
        before = saved_score(
            row.get("initial_score", {}),
            row.get("initial_objective_total"),
            f"{name} initial",
            lambda name=name: evaluate(name, initial),
        )
        for key, value in score_row(replayed).items():
            original = (
                row.get("objective_total") if key == "objective" else row.get("score", {}).get(key)
            )
            if original is not None:
                check_match(value, original, f"{name} optimized {key}")
        optimized = saved_score(
            row.get("score", {}),
            row.get("objective_total"),
            f"{name} optimized",
            lambda replayed=replayed: replayed,
        )
        traffic, loader_label = name.split("_")
        operating.append(
            {
                "scenario": name,
                "traffic": traffic,
                "loaders": int(loader_label.removesuffix("loader")),
                "initial_objective": before.total,
                "optimized_objective": optimized.total,
                "improvement_percent": 100 * (before.total - optimized.total) / before.total,
                **{f"initial_{key}": getattr(before, key) for key in RAW_METRICS},
                **{f"optimized_{key}": getattr(optimized, key) for key in RAW_METRICS},
            }
        )
    check_match(
        fmean(row["initial_objective"] for row in operating),
        saved["initial_objective_total"],
        "Initial batch objective",
    )

    raw, aggregate = [], []
    for name, grid in SWEEPS.items():
        i = WEIGHTS.index(name)
        for value in sorted({*grid, reference[i]}):
            weights = redistribute(reference, i, value)
            scores = replay if weights == reference else evaluate_batch(weights)
            rows = [score_row(score) for score in scores.values()]
            candidate = {
                "sweep": name,
                "trial_weight": value,
                "optimized_reference": weights == reference,
                **{f"theta_{key}": weight for key, weight in zip(WEIGHTS, weights, strict=True)},
            }
            raw.extend(
                {**candidate, "scenario": scenario, **row}
                for scenario, row in zip(scores, rows, strict=True)
            )
            aggregate.append(
                {
                    **candidate,
                    **{f"mean_{key}": fmean(row[key] for row in rows) for key in rows[0]},
                }
            )
            check_match(
                aggregate[-1]["mean_objective"],
                sum(row["objective"] for row in rows) / len(scenarios),
                "Sweep mean",
            )
    check_scenarios()
    return raw, aggregate, operating


def plot_studies(output, aggregate, operating):
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import ScalarFormatter

    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "pdf.fonttype": 42,
        }
    )

    def finish(fig, ax, stem, *, legend=True):
        ax.grid(axis="y", alpha=0.22)
        ax.set_axisbelow(True)
        if legend:
            ax.legend(frameon=False)
        for suffix in ("png", "pdf"):
            fig.savefig(output / f"{stem}.{suffix}", dpi=300)
        plt.close(fig)

    for index, name in enumerate(SWEEPS, start=1):
        rows = [row for row in aggregate if row["sweep"] == name]
        reference = next(row for row in rows if row["optimized_reference"])
        fig, ax = plt.subplots(figsize=(6.4, 3.8), layout="constrained")
        ax.plot(
            [r["trial_weight"] for r in rows],
            [r["mean_objective"] for r in rows],
            "o-",
            color="#304f76",
            markersize=4,
            label="Sampled policies",
        )
        ax.scatter(
            reference["trial_weight"],
            reference["mean_objective"],
            marker="D",
            s=58,
            color=OPTIMIZED_COLOR,
            edgecolor="white",
            zorder=5,
            label="Saved OMADS policy",
        )
        ax.set(
            title=f"{name.title()}-urgency weight sensitivity",
            xlabel=rf"{name.title()}-urgency weight, $\theta_{{{name[0].upper()}}}$",
            ylabel="Mean batch objective",
            xlim=(-0.025, 1.025),
        )
        finish(fig, ax, f"0{index}_{name}_weight")

    fig, ax = plt.subplots(figsize=(6.6, 3.9), layout="constrained")
    rows = [row for row in aggregate if row["sweep"] == "truck"]
    reference = next(row for row in rows if row["optimized_reference"])
    for key, label, color in (
        ("wait", "Truck waiting", "#304f76"),
        ("distance", "Loader travel", "#00766e"),
        ("shortfall", "Mill shortfall", "#ad5322"),
    ):
        ax.plot(
            [r["trial_weight"] for r in rows],
            [r[f"mean_{key}_contribution"] for r in rows],
            "o-",
            markersize=4,
            color=color,
            label=label,
        )
    ax.axvline(
        reference["trial_weight"], linestyle="--", color="#515961", label="Saved OMADS weight"
    )
    ax.set(
        title="Objective trade-offs along the truck-weight sweep",
        xlabel=r"Truck-urgency weight, $\theta_T$",
        ylabel="Mean normalized contribution",
        xlim=(-0.025, 1.025),
    )
    finish(fig, ax, "03_truck_components")

    x = np.arange(len(TRAFFIC))
    fig, ax = plt.subplots(figsize=(6.4, 3.8), layout="constrained")
    for index, color in ((1, "#304f76"), (2, OPTIMIZED_COLOR)):
        rows = [r for r in operating if r["loaders"] == index]
        bars = ax.bar(
            x + (index - 1.5) * 0.35,
            [r["improvement_percent"] for r in rows],
            0.32,
            color=color,
            label=f"{index} loader" + ("s" if index == 2 else ""),
        )
        ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=9)
    ax.set_xticks(x, [name.title() for name in TRAFFIC])
    ax.set(
        title="Objective improvement by operating configuration",
        xlabel="Traffic intensity",
        ylabel="Objective improvement (%)",
    )
    ax.margins(y=0.18)
    finish(fig, ax, "04_operating_improvement")

    fig, ax = plt.subplots(figsize=(8.0, 4.0), layout="constrained")
    x = np.array([0, 1, 2.4, 3.4, 4.8, 5.8])
    for index, (prefix, color) in enumerate(
        (("initial", INITIAL_COLOR), ("optimized", OPTIMIZED_COLOR))
    ):
        ax.bar(
            x + (index - 0.5) * 0.35,
            [r[f"{prefix}_truck_wait_minutes"] for r in operating],
            0.33,
            color=color,
            label=f"{prefix.title()} policy",
        )
    ax.set_xticks(
        x,
        [
            f"{r['traffic'].title()}\n{r['loaders']} loader" + ("s" if r["loaders"] == 2 else "")
            for r in operating
        ],
    )
    ax.yaxis.set_major_formatter(ScalarFormatter())
    ax.set(
        title="Truck waiting by traffic intensity and loader count",
        ylabel="Total truck waiting per scenario (min)",
    )
    finish(fig, ax, "05_operating_wait")


def write_summary(path, source, aggregate, operating):
    lines = [
        "# Dispatch-policy sensitivity studies",
        "",
        f"Reference run: `{source}`. Each sweep holds one weight at its trial value and "
        "redistributes the remainder in proportion to the saved weights. Each objective is "
        "the arithmetic mean of six fixed scenarios. Figure segments connect discrete samples.",
        "",
        "| Sweep | Saved weight | Saved mean | Lower neighbor mean | Upper neighbor mean | Best sampled weight and mean |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in SWEEPS:
        rows = [row for row in aggregate if row["sweep"] == name]
        i = next(i for i, row in enumerate(rows) if row["optimized_reference"])
        ref = rows[i]
        best = min(rows, key=lambda row: row["mean_objective"])
        lower = f"{rows[i - 1]['mean_objective']:.5f}" if i else "—"
        upper = f"{rows[i + 1]['mean_objective']:.5f}" if i + 1 < len(rows) else "—"
        lines.append(
            f"| {name.title()} | {ref['trial_weight']:.8f} | {ref['mean_objective']:.5f} | "
            f"{lower} | {upper} | {best['trial_weight']:.8f}, {best['mean_objective']:.5f} |"
        )

    truck = [row for row in aggregate if row["sweep"] == "truck"]
    lines += [
        "",
        "Equal sampled objective values can occur when several weight vectors produce "
        "the same dispatch decisions. These one-dimensional samples cannot establish "
        "a mathematical local or global minimum.",
        "",
        "| Truck weight | Mean wait (min) | Mean travel (m) | Mean shortfall (t) |",
        "|---|---:|---:|---:|",
    ]
    for row in truck:
        lines.append(
            f"| {row['trial_weight']:.6g} | {row['mean_truck_wait_minutes']:.2f} | "
            f"{row['mean_travel_metres']:.2f} | {row['mean_shortfall_tonnes']:.2f} |"
        )

    best = max(operating, key=lambda row: row["improvement_percent"])
    heavy = [row["improvement_percent"] for row in operating if row["traffic"] == "heavy"]
    lines += [
        "",
        "## Operating configurations",
        "",
        "The same initial and optimized policy were compared in all six cases. These cases "
        "were used in the optimization, so the comparison is in-sample.",
        f"The largest relative gain was {best['improvement_percent']:.2f}% in "
        f"{best['traffic']} traffic with {best['loaders']} loader(s). The heavy cases improved "
        f"by {min(heavy):.2f}% to {max(heavy):.2f}%.",
        "",
        "The operating summary uses raw minutes, metres, and tonnes. The sweep summary "
        "contains both raw means and normalized objective contributions. The per-scenario "
        "sweep CSV contains the underlying evaluations.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("runs/optimization/batch-six-500.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("runs/analysis/decision_policy_sensitivity")
    )
    args = parser.parse_args()
    try:
        raw, aggregate, operating = run_studies(args.source)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(f"Cannot use saved batch result: {error}")
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "sweep_scenarios.csv", raw)
    write_csv(args.output / "sweep_summary.csv", aggregate)
    write_csv(args.output / "operating_summary.csv", operating)
    plot_studies(args.output, aggregate, operating)
    write_summary(args.output / "summary.md", args.source, aggregate, operating)
    print(f"Saved studies to {args.output}")


if __name__ == "__main__":
    main()
