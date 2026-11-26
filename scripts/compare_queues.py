"""Compare deterministic FIFO, empty-queue-first, and longest-queue-first rules."""

from pathlib import Path

from mill_yard.config.loader import load_yard_scenario
from mill_yard.evaluations.metrics import summary
from mill_yard.policies.factory import OPERATOR_POLICY_NAMES, create_policy
from mill_yard.simulation.engine import run_yard


def main():
    print(
        "scenario,policy,wait_total,wait_p95_so_far,wait_max_so_far,starvation,travel_metres,unfinished"
    )
    for path in sorted(
        (Path(__file__).resolve().parents[1] / "configs/experiments/queues").glob("*.yaml")
    ):
        scenario = load_yard_scenario(path)
        for name in OPERATOR_POLICY_NAMES:
            result = run_yard(scenario, create_policy(name), record="summary", validate=True)
            m = summary(result)
            wait = m["completed_truck_wait_total"] + m["unfinished_truck_wait_so_far"]
            print(
                f"{path.stem},{name},{wait:.2f},{m['truck_wait_so_far_p95']:.2f},"
                f"{m['truck_wait_so_far_max']:.2f},{sum(m['starvation'].values()):.2f},"
                f"{m['loader_travel_distance_total']:.2f},{len(result.state.trucks)}"
            )


if __name__ == "__main__":
    main()
