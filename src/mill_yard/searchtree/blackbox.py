"""OMADS black-box evaluation of a decision-code vector."""

from math import isfinite
from numbers import Real

from mill_yard.model.yard import YardScenario
from mill_yard.searchtree.objective import (
    ObjectiveStatistics,
    ObjectiveWeights,
    normalized_objective_value,
    objective_value,
)
from mill_yard.searchtree.policy import DecisionSequencePolicy, action_table
from mill_yard.simulation.engine import run_yard


def make_omads_blackbox(
    scenario: YardScenario,
    *,
    decision_length: int = 10,
    weights: ObjectiveWeights | None = None,
    normalization: ObjectiveStatistics | None = None,
):
    """Create a callable that evaluates vectors using ``run_yard``.

    The returned callable accepts the coordinate vector ``x`` and returns the
    OMADS black-box structure ``[objective, [constraint_values]]``. Coordinates are rounded
    to the nearest integer action code; the caller should still give OMADS
    bounds of zero through ``len(action_table(scenario)) - 1`` for each
    coordinate.

    The current problem has no constraints, so the returned constraint value
    is always zero. Simulations retain summary-level data only.
    """
    if (
        not isinstance(decision_length, int)
        or isinstance(decision_length, bool)
        or decision_length < 1
    ):
        raise ValueError("decision_length must be a positive integer")

    weights = weights or ObjectiveWeights()
    base = len(action_table(scenario))

    def blackbox(x):
        """Evaluate one optimizer vector and return objective plus constraints."""
        try:
            coordinates = list(x)
        except TypeError as error:
            raise TypeError("optimizer point must be a one-dimensional vector") from error
        if len(coordinates) != decision_length:
            raise ValueError(f"optimizer vector must contain exactly {decision_length} values")

        decision_codes = []
        for value in coordinates:
            if isinstance(value, bool) or not isinstance(value, Real):
                raise TypeError("optimizer coordinates must be real numbers")
            if not isfinite(value):
                raise ValueError("optimizer coordinates must be finite")
            code = round(value)
            if not 0 <= code < base:
                raise ValueError(f"rounded action codes must be in the range [0, {base})")
            decision_codes.append(code)

        policy = DecisionSequencePolicy(decision_codes, scenario)
        result = run_yard(scenario, policy, record="summary")
        score = (
            normalized_objective_value(result, normalization, weights)
            if normalization is not None
            else objective_value(result, weights)
        )
        return [score, [0.0]]

    return blackbox


__all__ = ["make_omads_blackbox"]
