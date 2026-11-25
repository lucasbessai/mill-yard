"""Find dispatch theta weights for a single scenario or batch using OMADS, differential evolution, or both."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from mill_yard.config.loader import default_traffic_paths, load_yard_scenario
from mill_yard.optimization.objective import ObjectiveParameters
from mill_yard.optimization.optimizer import (
    OMADSParameters,
    OptimizationComparison,
    OptimizerParameters,
    optimize_policy,
)
from mill_yard.policies.optimizable import FeatureParameters


def _report(results):
    def transition(result, name):
        initial = getattr(result.initial_score, name)
        final = getattr(result.score, name)
        return f"{initial:.6g} -> {final:.6g}"

    def count(value):
        return "—" if value is None else str(value)

    batch = results[0].mode == "batch"
    rows = [
        (
            "Initial mean objective" if batch else "Initial objective",
            lambda r: f"{r.initial_score.total:.8g}",
        ),
        ("Best mean objective" if batch else "Best objective", lambda r: f"{r.score.total:.8g}"),
        ("Improvement (%)", lambda r: f"{r.improvement_percent:.3f}"),
        (
            "Mean truck waiting (min)" if batch else "Truck waiting (min)",
            lambda r: transition(r, "truck_wait_minutes"),
        ),
        (
            "Mean loader travel (m)" if batch else "Loader travel (m)",
            lambda r: transition(r, "travel_metres"),
        ),
        (
            "Mean mill shortfall (t)" if batch else "Mill shortfall (t)",
            lambda r: transition(r, "shortfall_tonnes"),
        ),
        ("Actual simulations", lambda r: count(r.simulations)),
        ("Objective calls", lambda r: count(r.objective_calls)),
        ("Policy cache hits", lambda r: count(r.cache_hits)),
        ("Solver evaluations", lambda r: count(r.function_evaluations)),
        ("OMADS iterations", lambda r: count(r.iterations if r.generations is None else None)),
        ("DE generations", lambda r: count(r.generations)),
        (
            "Candidate budget" if batch else "Simulation budget",
            lambda r: count(r.evaluation_budget),
        ),
        ("Stopped before budget", lambda r: count(r.stopped_before_budget)),
        ("Runtime (s)", lambda r: f"{r.runtime_seconds:.3f}"),
        ("Termination", lambda r: r.termination_reason),
    ]
    if batch:
        rows[0:0] = [
            ("Scenarios", lambda r: count(r.scenario_count)),
            ("Candidate evaluations", lambda r: count(r.candidate_evaluations)),
        ]
    rows.extend(
        (f"Best weight ({name})", lambda r, i=i: f"{r.theta[i]:.10g}")
        for i, name in enumerate(("truck", "mill", "inventory", "cost"))
    )
    table = [("Metric", *(result.algorithm for result in results))]
    table += [(name, *(value(r) for r in results)) for name, value in rows]
    _print_table(table)
    for result in results:
        print(f"{result.algorithm} — {result.message}")
        if batch:
            print(f"\n{result.algorithm} per scenario (initial → best)")
            table = [("Scenario", "Objective", "Wait (min)", "Travel (m)", "Shortfall (t)")]
            for scores in (*result.scenario_scores, result):
                name = scores.scenario_id if scores is not result else "Batch mean"
                table.append(
                    (
                        name,
                        *(
                            transition(scores, metric)
                            for metric in (
                                "total",
                                "truck_wait_minutes",
                                "travel_metres",
                                "shortfall_tonnes",
                            )
                        ),
                    )
                )
            _print_table(table)


def _print_table(table):
    widths = [max(map(len, column)) for column in zip(*table, strict=True)]
    for row in table:
        print(
            "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)).rstrip()
        )


def _plot(results, path):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    batch = results[0].mode == "batch"
    figure, axes = plt.subplots(layout="constrained")
    try:
        for result in results:
            axes.step(
                [
                    point.candidate_evaluations if batch else point.simulations
                    for point in result.evaluation_history
                ],
                [point.objective for point in result.evaluation_history],
                where="post",
                marker=".",
                label=result.algorithm,
            )
        axes.axhline(
            results[0].initial_score.total, linestyle="--", color="0.4", label="Initial policy"
        )
        axes.set(
            xlabel="Cumulative candidate-policy evaluations"
            if batch
            else "Cumulative simulation evaluations",
            ylabel="Best mean objective found" if batch else "Best objective found",
        )
        axes.legend()
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path)
    finally:
        plt.close(figure)


def _result_payload(result):
    payload = {
        **asdict(result),
        "method": (
            "scipy.optimize.differential_evolution"
            if result.generations is not None
            else "OMADS.mads.main"
            if result.settings.full_mads
            else "OMADS.poll.main"
        ),
        "objective_total": result.score.total,
        "initial_objective_total": result.initial_score.total,
        "improvement_percent": result.improvement_percent,
    }

    if result.mode == "batch":
        payload["scenario_count"] = result.scenario_count
        for item, scores in zip(payload["scenario_scores"], result.scenario_scores, strict=True):
            item["initial_objective_total"] = scores.initial_score.total
            item["objective_total"] = scores.score.total
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", type=Path, metavar="SCENARIO")
    parser.add_argument(
        "--mode",
        choices=("single", "batch"),
        default="single",
        help="Batch defaults to all traffic configurations",
    )
    parser.add_argument("--method", choices=("omads", "de", "both"), default="omads")
    parser.add_argument("--budget", type=int, help="Maximum unique candidate policies per method")
    parser.add_argument("--full-mads", action="store_true", help="Enable Latin hypercube search")
    parser.add_argument("--plot", type=Path, help="Save best objective versus evaluations")

    parser.add_argument(
        "--initial-theta",
        nargs=4,
        type=float,
        default=(0.25, 0.4, 0.15, 0.2),
        metavar=("TRUCK", "MILL", "INVENTORY", "COST"),
    )
    parser.add_argument(
        "--output", type=Path, help="Write settings, scenario, scores, and history as JSON"
    )
    for name, default in asdict(OptimizerParameters()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=type(default), default=default)
    for name, default in asdict(ObjectiveParameters()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=default)
    for name, default in asdict(FeatureParameters()).items():
        parser.add_argument(
            "--feature-" + name.replace("_", "-"),
            type=float,
            default=default,
            dest="feature_" + name,
        )
    args = parser.parse_intermixed_args()
    if args.mode == "single" and len(args.scenarios) != 1:
        parser.error("Single mode requires exactly one scenario path")
    try:
        settings = OptimizerParameters(
            **{n: getattr(args, n) for n in asdict(OptimizerParameters())}
        )
        objective = ObjectiveParameters(
            **{n: getattr(args, n) for n in asdict(ObjectiveParameters())}
        )
        features = FeatureParameters(
            **{n: getattr(args, "feature_" + n) for n in asdict(FeatureParameters())}
        )
        paths = args.scenarios or default_traffic_paths()
        if args.mode == "batch":
            identifiers = [path.stem for path in paths]
            if len(set(identifiers)) != len(identifiers):
                parser.error("Batch scenario filenames must have unique stems")
            scenario = {path.stem: load_yard_scenario(path) for path in paths}
        else:
            scenario = load_yard_scenario(paths[0])
        result = optimize_policy(
            scenario,
            mode=args.mode,
            initial_theta=args.initial_theta,
            settings=settings,
            features=features,
            objective=objective,
            method=args.method,
            omads_settings=OMADSParameters(full_mads=args.full_mads),
            evaluation_budget=args.budget,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if isinstance(result, OptimizationComparison):
        results = (result.omads, result.de)
        payload = {
            "method": "both",
            "omads": _result_payload(result.omads),
            "de": _result_payload(result.de),
        }
    else:
        results = (result,)
        payload = _result_payload(result)
    _report(results)
    if args.plot:
        _plot(results, args.plot)
        print(f"Saved {args.plot}")
    if args.output:
        payload["theta_order"] = ["truck", "mill", "inventory", "cost"]
        payload["mode"] = args.mode
        if args.mode == "batch":
            payload["schema_version"] = 3
            payload["scenarios"] = [
                {
                    "id": path.stem,
                    "path": str(path.resolve()),
                    "scenario": asdict(scenario[path.stem]),
                }
                for path in paths
            ]
        else:
            payload["schema_version"] = 2
            payload["scenario_path"] = str(paths[0].resolve())
            payload["scenario"] = asdict(scenario)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
