"""Beaudoin-inspired truck allocation with the shared operator priorities."""

from mill_yard.policies.fifo import FifoPolicy
from mill_yard.policies.truck_dispatch import EmptyQueueFirstRule, LongestQueueFirstRule


class EmptyQueueFirstPolicy(FifoPolicy):
    """Keep each loader on an unloading-area queue until unreserved work clears."""

    def __init__(self, parameters=None):
        super().__init__(parameters, truck_rule=EmptyQueueFirstRule())


class LongestQueueFirstPolicy(FifoPolicy):
    """Prefer the largest queue, then longest truck wait, arrival, and area ID."""

    def __init__(self, parameters=None):
        super().__init__(parameters, truck_rule=LongestQueueFirstRule())
