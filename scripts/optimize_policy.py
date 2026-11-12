"""Find dispatch theta weights for one scenario using differential evolution."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from mill_yard.config.loader import load_yard_scenario
from mill_yard.optimization.objective import ObjectiveParameters
from mill_yard.optimization.optimizer import OptimizerParameters, optimize_policy
from mill_yard.policies.optimizable import FeatureParameters


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
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
    args = parser.parse_args()
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
        scenario = load_yard_scenario(args.scenario)
        result = optimize_policy(
            scenario,
            initial_theta=args.initial_theta,
            settings=settings,
            features=features,
            objective=objective,
        )
    except ValueError as error:
        parser.error(str(error))
    print("theta order: truck, mill, inventory, cost")
    print("Best theta:", ", ".join(f"{v:.10g}" for v in result.theta))
    print(
        f"Objective: {result.initial_score.total:.6f} -> {result.score.total:.6f} (lower is better)"
    )
    print(
        f"Wait: {result.score.truck_wait_minutes:.3f} min; "
        f"travel: {result.score.travel_metres:.3f} m; "
        f"shortfall: {result.score.shortfall_tonnes:.3f} t"
    )
    print(
        f"Generations: {result.generations}; simulations: {result.simulations}; "
        f"converged: {result.converged}"
    )
    print(result.message)
    if args.output:
        payload = asdict(result)
        payload.update(
            schema_version=1,
            method="scipy.optimize.differential_evolution",
            theta_order=["truck", "mill", "inventory", "cost"],
            scenario_path=str(Path(args.scenario).resolve()),
            scenario=asdict(scenario),
        )
        payload["objective_total"] = result.score.total
        payload["initial_objective_total"] = result.initial_score.total
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
