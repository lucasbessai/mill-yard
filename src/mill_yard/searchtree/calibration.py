"""Random-path sampling for objective normalization statistics."""

from dataclasses import asdict, dataclass
from random import Random
from statistics import mean, stdev

from mill_yard.model.yard import YardScenario
from mill_yard.searchtree.objective import (
    MetricStatistics,
    ObjectiveStatistics,
    objective_components,
)
from mill_yard.searchtree.policy import DecisionSequencePolicy, action_table
from mill_yard.simulation.engine import run_yard


@dataclass(frozen=True)
class RandomPathObservation:
    """One random vector and the resulting terminal cost components."""

    decision_codes: tuple[int, ...]
    starvation: float
    truck_waiting: float
    loader_travel: float
    invalid_decisions: int


@dataclass(frozen=True)
class RandomPathCalibration:
    """Calibration summary and sampled paths for one scenario/design size."""

    sample_count: int
    decision_length: int
    action_code_count: int
    seed: int
    statistics: ObjectiveStatistics
    observations: tuple[RandomPathObservation, ...]

    def to_dict(self) -> dict:
        """Return a JSON-serializable copy of this calibration result."""
        return asdict(self)


def calibrate_random_paths(
    scenario: YardScenario,
    *,
    samples: int = 100,
    decision_length: int = 10,
    seed: int = 0,
) -> RandomPathCalibration:
    """Sample uniform action-code vectors and summarize their run outcomes.

    Each coordinate is drawn uniformly from the scenario's action table,
    including idle. Sampling is with replacement and every vector is evaluated
    through the same sequence policy and full-horizon simulation used by the
    OMADS black box.
    """
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 2:
        raise ValueError("samples must be an integer of at least 2")
    if (
        not isinstance(decision_length, int)
        or isinstance(decision_length, bool)
        or decision_length < 1
    ):
        raise ValueError("decision_length must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError("seed must be an integer")

    actions = action_table(scenario)
    random = Random(seed)
    observations = []
    metric_values = {"starvation": [], "truck_waiting": [], "loader_travel": []}

    for _ in range(samples):
        codes = tuple(random.randrange(len(actions)) for _ in range(decision_length))
        policy = DecisionSequencePolicy(codes, scenario)
        result = run_yard(scenario, policy, record="summary")
        components = objective_components(result)
        for name, value in components.items():
            metric_values[name].append(value)
        observations.append(
            RandomPathObservation(
                decision_codes=codes,
                starvation=components["starvation"],
                truck_waiting=components["truck_waiting"],
                loader_travel=components["loader_travel"],
                invalid_decisions=policy.invalid_decisions,
            )
        )

    def describe(values: list[float]) -> MetricStatistics:
        return MetricStatistics(
            mean=mean(values),
            maximum=max(values),
            standard_deviation=stdev(values),
            minimum=min(values),
        )

    statistics = ObjectiveStatistics(
        starvation=describe(metric_values["starvation"]),
        truck_waiting=describe(metric_values["truck_waiting"]),
        loader_travel=describe(metric_values["loader_travel"]),
    )
    return RandomPathCalibration(
        sample_count=samples,
        decision_length=decision_length,
        action_code_count=len(actions),
        seed=seed,
        statistics=statistics,
        observations=tuple(observations),
    )


__all__ = ["RandomPathCalibration", "RandomPathObservation", "calibrate_random_paths"]
