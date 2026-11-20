"""OMADS configuration and execution for encoded yard decision vectors."""

from pathlib import Path

from mill_yard.model.yard import YardScenario
from mill_yard.searchtree.blackbox import make_omads_blackbox
from mill_yard.searchtree.objective import ObjectiveStatistics, ObjectiveWeights
from mill_yard.searchtree.policy import action_table


def omads_configuration(
    scenario: YardScenario,
    *,
    decision_length: int = 10,
    budget: int = 20,
    seed: int = 0,
    weights: ObjectiveWeights | None = None,
    normalization: ObjectiveStatistics | None = None,
    post_dir: str | Path = "runs/omads",
) -> dict:
    """Build a small MADS setup for a fixed-length decision-code vector."""
    if (
        not isinstance(decision_length, int)
        or isinstance(decision_length, bool)
        or decision_length < 1
    ):
        raise ValueError("decision_length must be a positive integer")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise ValueError("budget must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError("seed must be an integer")

    base = len(action_table(scenario))
    upper_bound = float(base - 1)
    width = max(upper_bound, 1.0)
    return {
        "evaluator": {
            "blackbox": make_omads_blackbox(
                scenario,
                decision_length=decision_length,
                weights=weights,
                normalization=normalization,
            )
        },
        "param": {
            "name": "mill_yard_decision_search",
            "baseline": [0.0] * decision_length,
            "lb": [0.0] * decision_length,
            "ub": [upper_bound] * decision_length,
            "var_names": [f"decision_{index}" for index in range(decision_length)],
            "var_type": ["R"] * decision_length,
            "scaling": [width] * decision_length,
            "post_dir": str(post_dir),
        },
        "options": {
            "seed": seed,
            "budget": budget,
            "tol": 1e-3,
            "check_cache": True,
            "store_cache": True,
            "rich_direction": True,
            "display": True,
        },
        "search": {"type": "sampling", "s_method": "RS", "ns": min(10, budget)},
    }


def run_omads(
    scenario: YardScenario,
    *,
    decision_length: int = 10,
    budget: int = 20,
    seed: int = 0,
    weights: ObjectiveWeights | None = None,
    normalization: ObjectiveStatistics | None = None,
    post_dir: str | Path = "runs/omads",
) -> dict:
    """Run OMADS and return its result dictionary.

    Install the optional dependency with ``uv sync --extra optimization``.
    """
    try:
        from OMADS import mads
    except ImportError as error:
        raise ImportError(
            "OMADS is optional; install it with `uv sync --extra optimization`."
        ) from error

    configuration = omads_configuration(
        scenario,
        decision_length=decision_length,
        budget=budget,
        seed=seed,
        weights=weights,
        normalization=normalization,
        post_dir=post_dir,
    )
    output, *_ = mads.main(configuration)
    return output


__all__ = ["omads_configuration", "run_omads"]
