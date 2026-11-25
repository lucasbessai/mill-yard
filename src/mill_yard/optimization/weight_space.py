"""Named trainable/fixed policy weights, independent of optimization algorithms."""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from math import isclose, isfinite
from types import MappingProxyType

from mill_yard.policies.features import FeatureWeights


@dataclass(frozen=True)
class FeatureWeightSpace:
    """An optimizer sees only trainable names; fixed weights are inserted later."""

    trainable_weights: tuple[str, ...] = ("truck", "mill", "inventory", "cost")
    fixed_weights: Mapping[str, float] = field(default_factory=lambda: {"bay_release": 0.0})

    def __post_init__(self):
        names = {f.name for f in fields(FeatureWeights)}
        trainable, fixed = tuple(self.trainable_weights), dict(self.fixed_weights)
        if (
            len(set(trainable)) != len(trainable)
            or set(trainable) & fixed.keys()
            or set(trainable) | fixed.keys() != names
        ):
            raise ValueError("Every feature weight must appear exactly once as trainable or fixed")
        if any(not isfinite(v) or v < 0 for v in fixed.values()):
            raise ValueError("Fixed weights must be finite and nonnegative")
        total = sum(fixed.values())
        if (
            total > 1
            or (trainable and total == 1)
            or (not trainable and not isclose(total, 1, abs_tol=1e-9))
        ):
            raise ValueError("Fixed weights must leave a positive trainable budget or sum to one")
        object.__setattr__(self, "trainable_weights", trainable)
        object.__setattr__(self, "fixed_weights", MappingProxyType(fixed))

    @property
    def trainable_budget(self):
        return 1.0 - sum(self.fixed_weights.values())

    def build_weights(self, values: Mapping[str, float]) -> FeatureWeights:
        """Require exact trainable names and the remaining simplex budget."""
        if set(values) != set(self.trainable_weights):
            raise ValueError("Supply exactly the trainable weight names")
        return FeatureWeights(**dict(self.fixed_weights), **dict(values))

    def from_vector(self, values) -> FeatureWeights:
        """Decode using the explicit profile order; no silent renormalization."""
        values = tuple(values)
        if len(values) != len(self.trainable_weights):
            raise ValueError("Weight vector length must match trainable_weights")
        return self.build_weights(dict(zip(self.trainable_weights, values, strict=True)))

    def metadata(self):
        return {
            "trainable_weights": list(self.trainable_weights),
            "fixed_weights": dict(self.fixed_weights),
            "trainable_budget": self.trainable_budget,
        }
