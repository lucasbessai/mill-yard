"""Construct the built-in policies for CLI and desktop sessions."""

from mill_yard.policies.feature_scored import FeatureScoredParameters, FeatureScoredPolicy
from mill_yard.policies.fifo import FifoParameters, FifoPolicy
from mill_yard.policies.human_controlled import HumanControlledPolicy, TerminalHumanPolicy
from mill_yard.policies.queue_first import EmptyQueueFirstPolicy, LongestQueueFirstPolicy
from mill_yard.policies.random import RandomPolicy
from mill_yard.policies.scored import ScoredParameters, ScoredPolicy
from mill_yard.policies.threshold import ThresholdPolicy

OPERATOR_POLICY_NAMES = ("fifo", "empty-queue-first", "longest-queue-first")
POLICY_NAMES = ("threshold", "random", "scored", "human", "feature-scored", *OPERATOR_POLICY_NAMES)


def create_policy(name, *, seed=0, parameters=None, terminal=False):
    """Create fresh policies; GUI switches use defaults for other parameter families."""
    if name == "threshold":
        return ThresholdPolicy()
    if name == "fifo":
        return FifoPolicy(parameters if isinstance(parameters, FifoParameters) else None)
    if name == "empty-queue-first":
        return EmptyQueueFirstPolicy(parameters if isinstance(parameters, FifoParameters) else None)
    if name == "longest-queue-first":
        return LongestQueueFirstPolicy(
            parameters if isinstance(parameters, FifoParameters) else None
        )
    if name == "random":
        return RandomPolicy(seed)
    if name == "scored":
        return ScoredPolicy(parameters if isinstance(parameters, ScoredParameters) else None)
    if name == "feature-scored":
        return FeatureScoredPolicy(
            parameters if isinstance(parameters, FeatureScoredParameters) else None
        )
    if name == "human":
        return TerminalHumanPolicy() if terminal else HumanControlledPolicy()
    raise ValueError(f"Unknown policy: {name}")
