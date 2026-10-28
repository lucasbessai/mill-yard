"""Command-line entry point for running a yard scenario."""

import argparse

from mill_yard.config.loader import load_yard_scenario
from mill_yard.policies.joint import RandomPolicy, ThresholdPolicy
from mill_yard.policies.scored import ScoredParameters, ScoredPolicy
from mill_yard.records.persistence import save_run, summary
from mill_yard.simulation.engine import run_yard


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and print a compact run summary."""
    parser = argparse.ArgumentParser(prog="mill-yard", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run a YAML scenario")
    run.add_argument("scenario")
    run.add_argument("--policy", choices=("threshold", "random", "scored"), default="threshold")
    for field in (
        "truck_weight",
        "queue_weight",
        "refill_weight",
        "starvation_weight",
        "travel_weight",
        "wait_reference",
        "travel_reference",
        "min_score",
    ):
        run.add_argument(
            "--" + field.replace("_", "-"),
            type=float,
            default=None,
            help="Override scored policy parameter " + field,
        )
    run.add_argument("--seed", type=int, default=0, help="Random-policy seed")
    run.add_argument(
        "--wake-mode", choices=("interval", "policy"), help="Override scenario wake mode"
    )
    run.add_argument("--wake-interval", type=float, help="Override interval in minutes")
    run.add_argument("--record", choices=("summary", "events", "snapshots"), default="snapshots")
    run.add_argument("--trace", action="store_true", help="Print events during the run")
    run.add_argument("--trace-state", action="store_true", help="Include state in the live trace")
    run.add_argument("--save", action="store_true", help="Save results under runs/")
    args = parser.parse_args(argv)
    from dataclasses import replace

    scenario = load_yard_scenario(args.scenario)
    overrides = {}
    if args.wake_mode is not None:
        overrides["wake_mode"] = args.wake_mode
    if args.wake_interval is not None:
        overrides["wake_interval"] = args.wake_interval
    scenario = replace(scenario, **overrides)
    if args.policy == "scored":
        policy_args = {
            key: getattr(args, key)
            for key in ScoredParameters.__dataclass_fields__
            if getattr(args, key) is not None
        }
        policy = ScoredPolicy(ScoredParameters(**policy_args))
    else:
        policy = ThresholdPolicy() if args.policy == "threshold" else RandomPolicy(args.seed)
    result = run_yard(
        scenario, policy, record=args.record, trace=args.trace, trace_state=args.trace_state
    )
    metrics = summary(result)
    print(
        f"Trucks completed: {len(result.completed_trucks)}; unfinished: {len(result.state.trucks)}"
    )
    print(
        f"Truck waiting minutes: {metrics['completed_truck_wait_total']:.2f} completed; {metrics['unfinished_truck_wait_so_far']:.2f} unfinished so far"
    )
    print(f"Consumed by infeed: {metrics['consumed']}; starvation minutes: {metrics['starvation']}")
    print(f"Loader travel metres: { {k: v['distance'] for k, v in metrics['loaders'].items()} }")
    if args.save:
        print(f"Saved run: {save_run(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
