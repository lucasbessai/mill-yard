"""Run each candidate policy and rank its end-of-run objective (lower is better)."""

import argparse
from dataclasses import asdict

from mill_yard.config.loader import load_yard_scenario
from mill_yard.optimization.objective import ObjectiveParameters, evaluate_objective
from mill_yard.policies.joint import RandomPolicy, ThresholdPolicy
from mill_yard.policies.scored import ScoredPolicy
from mill_yard.simulation.engine import run_yard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", default="configs/example.yaml")
    parser.add_argument("--seed", type=int, default=42)
    for name, default in asdict(ObjectiveParameters()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=default)
    args = parser.parse_args()
    parameters = ObjectiveParameters(
        **{name: getattr(args, name) for name in asdict(ObjectiveParameters())}
    )
    scenario = load_yard_scenario(args.scenario)
    print(f"Horizon: {scenario.horizon:g} minutes; random seed: {args.seed}")
    print(f"Objective parameters: {asdict(parameters)}")
    rows = []
    for name, policy in (
        ("threshold", ThresholdPolicy()),
        ("scored", ScoredPolicy()),
        ("random", RandomPolicy(args.seed)),
    ):
        result = run_yard(scenario, policy, record="summary")
        rows.append((name, evaluate_objective(result, parameters), len(result.state.trucks)))
    print("policy,objective,wait_minutes,travel_metres,shortfall_tonnes,unfinished_trucks")
    for name, score, unfinished in sorted(rows, key=lambda row: row[1].total):
        print(
            f"{name},{score.total:.6f},{score.truck_wait_minutes:.2f},"
            f"{score.travel_metres:.2f},{score.shortfall_tonnes:.2f},{unfinished}"
        )
    print("Ranked by objective, lowest first; ties do not identify a unique winner.")


if __name__ == "__main__":
    main()
