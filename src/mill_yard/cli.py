"""Command-line entry point for running a yard scenario."""

import argparse
from dataclasses import fields, replace
from math import isfinite

from mill_yard.config.loader import select_scenario_files
from mill_yard.config.policy import load_feature_policy_parameters, load_fifo_policy_parameters
from mill_yard.evaluations.metrics import summary
from mill_yard.policies.factory import OPERATOR_POLICY_NAMES, POLICY_NAMES, create_policy
from mill_yard.policies.feature_scored import FeatureScoredParameters
from mill_yard.policies.features import FeatureWeights
from mill_yard.policies.fifo import FifoParameters
from mill_yard.policies.scored import ScoredParameters
from mill_yard.records.persistence import create_run_directory, save_run
from mill_yard.simulation.engine import run_yard


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and print a compact run summary."""
    parser = argparse.ArgumentParser(prog="mill-yard", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run a YAML scenario, optionally with terminal input")
    gui = commands.add_parser("gui", help="Watch or operate a yard in a desktop window")
    for command in (run, gui):
        command.add_argument(
            "scenario", nargs="?", help="Experiment preset or legacy combined scenario"
        )
        command.add_argument("--yard", help="Yard definition YAML")
        command.add_argument(
            "--truck-schedule", help="Truck schedule YAML (includes default horizon)"
        )
        command.add_argument(
            "--initial-conditions", help="Initial stocks and loader positions YAML"
        )
        command.add_argument("--horizon", type=float, help="Override the truck schedule's horizon")
        policies = command.add_mutually_exclusive_group()
        policies.add_argument("--policy", choices=POLICY_NAMES, default="threshold")
        policies.add_argument(
            "--human",
            dest="policy",
            action="store_const",
            const="human",
            help="Operate the loaders manually",
        )
        for item in fields(ScoredParameters):
            command.add_argument(
                "--" + item.name.replace("_", "-"),
                type=float,
                default=None,
                help="Override scored policy parameter " + item.name,
            )
        command.add_argument("--feature-config", help="Feature-scored policy YAML settings")
        command.add_argument(
            "--feature-version",
            type=int,
            choices=(2, 3, 4),
            help="2: legacy; 3: local backlog; 4: local backlog and area waiting urgency",
        )
        command.add_argument("--fifo-config", help="Operator-style FIFO policy YAML settings")
        command.add_argument(
            "--feature-weights",
            nargs="+",
            type=float,
            metavar="WEIGHT",
            help="Simplex weights TRUCK MILL INVENTORY COST [BAY_RELEASE]; four or five values",
        )
        command.add_argument("--seed", type=int, default=0, help="Random-policy seed")
        command.add_argument(
            "--wake-mode", choices=("interval", "policy"), help="Override scenario wake mode"
        )
        command.add_argument("--wake-interval", type=float, help="Override interval in minutes")
        command.add_argument(
            "--record",
            choices=("summary", "events", "snapshots"),
            default="snapshots" if command is run else "events",
        )
        command.add_argument(
            "--validate", action="store_true", help="Check physical invariants at each transition"
        )
    run.add_argument("--trace", action="store_true", help="Print events during the run")
    run.add_argument("--trace-state", action="store_true", help="Include state in the live trace")
    run.add_argument("--save", action="store_true", help="Save results under runs/")
    gui.add_argument(
        "--backend", choices=("tkinter", "arcade"), default="tkinter", help="Desktop frontend"
    )
    gui.add_argument(
        "--speed",
        type=float,
        default=5.0,
        help="Playback speed in simulation minutes per real second",
    )
    args = parser.parse_args(argv)
    components = (args.yard, args.truck_schedule, args.initial_conditions)
    if args.scenario is not None and any(x is not None for x in components):
        parser.error("Choose an experiment path or --yard/--truck-schedule/--initial-conditions")
    if args.scenario is None and any(x is None for x in components):
        parser.error("Provide an experiment path or all three component files")
    if args.horizon is not None and (not isfinite(args.horizon) or args.horizon <= 0):
        parser.error("Horizon must be finite and positive")
    weights = {
        item.name: getattr(args, item.name)
        for item in fields(ScoredParameters)
        if getattr(args, item.name) is not None
    }
    if weights and args.policy != "scored":
        parser.error("Scored-weight options require --policy scored")

    parameters = ScoredParameters(**weights) if args.policy == "scored" else None
    if args.fifo_config and args.policy not in OPERATOR_POLICY_NAMES:
        parser.error(
            "FIFO options require --policy fifo, empty-queue-first, or longest-queue-first"
        )
    if args.policy in OPERATOR_POLICY_NAMES:
        try:
            parameters = (
                load_fifo_policy_parameters(args.fifo_config)
                if args.fifo_config
                else FifoParameters()
            )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if (
        args.feature_config or args.feature_weights is not None or args.feature_version is not None
    ) and args.policy != "feature-scored":
        parser.error("Feature options require --policy feature-scored")
    if args.policy == "feature-scored":
        try:
            parameters = (
                load_feature_policy_parameters(args.feature_config)
                if args.feature_config
                else FeatureScoredParameters()
            )
            if args.feature_version is not None:
                # Apply version and weights together so a legacy override can clear R.
                version = args.feature_version
            else:
                version = parameters.feature_version
            if args.feature_weights is not None:
                if len(args.feature_weights) not in (4, 5):
                    raise ValueError("--feature-weights requires four or five values")
                parameters = replace(
                    parameters,
                    weights=FeatureWeights(*args.feature_weights),
                    feature_version=version,
                )
            else:
                parameters = replace(parameters, feature_version=version)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if args.command == "gui":
        if not isfinite(args.speed) or args.speed <= 0:
            parser.error("Playback speed must be positive and finite")
        try:
            if args.backend == "arcade":
                from mill_yard.gui.arcade_app import launch
            else:
                from mill_yard.gui.app import launch

            launch(
                args.scenario,
                **(
                    {
                        "yard": args.yard,
                        "truck_schedule": args.truck_schedule,
                        "initial_conditions": args.initial_conditions,
                    }
                    if args.scenario is None
                    else {}
                ),
                **({"horizon": args.horizon} if args.horizon is not None else {}),
                policy=args.policy,
                seed=args.seed,
                parameters=parameters,
                speed=args.speed,
                record=args.record,
                validate=args.validate,
                wake_mode=args.wake_mode,
                wake_interval=args.wake_interval,
            )
        except ImportError as exc:
            parser.error(
                f"Cannot open {args.backend} GUI: {exc}. "
                "For Arcade, install the gui extra with uv sync --extra gui."
            )
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(f"Cannot open {args.backend} GUI: {exc}")
        return 0

    try:
        scenario = select_scenario_files(
            args.scenario,
            yard=args.yard,
            truck_schedule=args.truck_schedule,
            initial_conditions=args.initial_conditions,
            horizon=args.horizon,
            wake_mode=args.wake_mode,
            wake_interval=args.wake_interval,
        ).load()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    policy = create_policy(args.policy, seed=args.seed, parameters=parameters, terminal=True)
    path = create_run_directory() if args.save else None
    try:
        result = run_yard(
            scenario,
            policy,
            record=args.record,
            trace=args.trace,
            trace_state=args.trace_state,
            events_path=path / "events.jsonl" if path else None,
            retain_events=not args.save,
            validate=args.validate,
        )
    except (EOFError, KeyboardInterrupt):
        print(
            "Simulation stopped by the operator. Any streamed events remain in the run directory."
        )
        return 130
    metrics = summary(result)
    print(
        f"Trucks completed: {len(result.completed_trucks)}; unfinished: {len(result.state.trucks)}"
    )
    print(
        f"Truck waiting minutes: {metrics['completed_truck_wait_total']:.2f} completed; {metrics['unfinished_truck_wait_so_far']:.2f} unfinished so far"
    )
    print(f"Consumed by infeed: {metrics['consumed']}; starvation minutes: {metrics['starvation']}")
    print(
        f"Loader travel metres: { {k: v['total_travel_distance'] for k, v in metrics['loaders'].items()} }"
    )
    if args.save:
        print(f"Saved run: {save_run(result, run_path=path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
