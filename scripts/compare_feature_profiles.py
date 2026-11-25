"""Compare untuned four/five-feature profiles with physical invariant checks."""

import argparse
import sys
from pathlib import Path
from time import perf_counter

from mill_yard.config.loader import load_yard_scenario
from mill_yard.config.policy import load_feature_policy_parameters
from mill_yard.evaluations.metrics import summary
from mill_yard.policies.feature_scored import FeatureScoredPolicy
from mill_yard.simulation.engine import run_yard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", type=Path)
    parser.add_argument("--include-legacy", action="store_true")
    parser.add_argument(
        "--include-version-3",
        action="store_true",
        help="Also compare profiles without area waiting urgency",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    paths = args.scenarios or sorted((root / "configs/experiments/queues").glob("*.yaml"))
    profiles = ["feature_scored__v4_4weights", "feature_scored__v4_5weights"]
    if args.include_version_3:
        profiles.extend(
            ["legacy/feature_scored__v3_4weights", "legacy/feature_scored__v3_5weights"]
        )
    if args.include_legacy:
        profiles.append("legacy/feature_scored__v2_4weights")
    print("scenario,profile,wait_total,starvation,travel_metres,unfinished,seconds", flush=True)
    for path in paths:
        scenario = load_yard_scenario(path)
        for profile in profiles:
            print(f"Running {path.stem}: {profile}", file=sys.stderr, flush=True)
            parameters = load_feature_policy_parameters(root / f"configs/policies/{profile}.yaml")
            start = perf_counter()
            result = run_yard(
                scenario, FeatureScoredPolicy(parameters), record="summary", validate=True
            )
            metrics = summary(result)
            wait = metrics["completed_truck_wait_total"] + metrics["unfinished_truck_wait_so_far"]
            print(
                f"{path.stem},{profile},{wait:.2f},{sum(metrics['starvation'].values()):.2f},"
                f"{metrics['loader_travel_distance_total']:.2f},{len(result.state.trucks)},"
                f"{perf_counter() - start:.2f}",
                flush=True,
            )


if __name__ == "__main__":
    main()
