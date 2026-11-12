"""Generated multi-asset regressions with invariant and event-log checks."""

from random import Random

import pytest

from mill_yard.model.scenario import InfeedSpec, LoaderSpec, PileSpec, StopSpec, YardScenario
from mill_yard.model.types import Arrival, Point
from mill_yard.policies.random import RandomPolicy
from mill_yard.policies.scored import ScoredPolicy
from mill_yard.policies.threshold import ThresholdPolicy
from mill_yard.simulation.engine import run_yard


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("policy_type", [ThresholdPolicy, RandomPolicy, ScoredPolicy])
def test_generated_runs_preserve_physics_and_consistent_starvation_logs(seed, policy_type):
    rng = Random(seed)
    scenario = YardScenario(
        horizon=25,
        arrivals=tuple(Arrival(rng.randrange(15), rng.randrange(1, 21), f"t{i}") for i in range(6)),
        stops=tuple(StopSpec(f"s{i}", Point(0, i * 5), rng.randrange(1, 3)) for i in range(2)),
        piles=tuple(
            PileSpec(f"p{i}", Point(10 + i * 5, 0), 50, rng.randrange(51), 2) for i in range(2)
        ),
        infeeds=tuple(
            InfeedSpec(
                f"i{i}", Point(30, i * 5), 30, rng.randrange(31), rng.choice([0, 0.5, 1]), 5, 20
            )
            for i in range(2)
        ),
        loaders=tuple(LoaderSpec(f"l{i}", Point(0, 0), 10, 10, 5, 5) for i in range(2)),
        wake_interval=2,
    )
    policy = RandomPolicy(seed) if policy_type is RandomPolicy else policy_type()
    result = run_yard(scenario, policy, record="events", validate=True)
    starts, totals = {}, {i.id: 0.0 for i in scenario.infeeds}
    for event in result.events:
        if event["event"] == "starvation_start":
            assert event["infeed"] not in starts
            starts[event["infeed"]] = event["time"]
        elif event["event"] == "starvation_end":
            assert event["infeed"] in starts
            totals[event["infeed"]] += event["time"] - starts.pop(event["infeed"])
    for infeed, started in starts.items():
        totals[infeed] += scenario.horizon - started
    assert totals == pytest.approx(result.state.starvation, abs=1e-7)
