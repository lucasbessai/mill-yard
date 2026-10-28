"""Compare baseline policies on the illustrative deterministic traffic scenarios.

Run with: uv run python scripts/compare_traffic.py
"""

from pathlib import Path

from mill_yard.config.loader import load_yard_scenario
from mill_yard.policies.joint import RandomPolicy, ThresholdPolicy
from mill_yard.policies.scored import ScoredPolicy
from mill_yard.records.persistence import summary
from mill_yard.simulation.engine import run_yard


def main() -> None:
    """Print cumulative waiting, starvation, incomplete trucks and travel."""
    print("scenario,policy,wait_minutes,starvation_minutes,unfinished,travel_metres")
    for path in sorted(
        (Path(__file__).resolve().parent.parent / "configs" / "traffic").glob("*.yaml")
    ):
        scenario = load_yard_scenario(path)
        for name, policy in (
            ("threshold", ThresholdPolicy()),
            ("scored", ScoredPolicy()),
            ("random_seed_42", RandomPolicy(42)),
        ):
            result = run_yard(scenario, policy, record="summary")
            metrics = summary(result)
            wait = metrics["completed_truck_wait_total"] + metrics["unfinished_truck_wait_so_far"]
            starvation = sum(metrics["starvation"].values())
            print(
                f"{path.stem},{name},{wait:.2f},{starvation:.2f},"
                f"{len(metrics['unfinished_trucks'])},{metrics['loader_travel_distance_total']:.2f}"
            )


if __name__ == "__main__":
    main()
