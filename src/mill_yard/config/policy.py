"""Strict YAML loading for the proposal-based feature policy."""

from pathlib import Path

import yaml

from mill_yard.optimization.weight_space import FeatureWeightSpace
from mill_yard.policies.feature_scored import FeatureScoredParameters
from mill_yard.policies.features import FeatureParameters, FeatureWeights
from mill_yard.policies.fifo import FifoParameters


def load_feature_policy_parameters(path):
    """Load weights/features, rejecting misspelled fields rather than ignoring them."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid feature policy YAML: {exc}") from exc
    if not isinstance(data, dict) or set(data) - {"weights", "features", "feature_version"}:
        raise ValueError("Feature policy config permits weights, features, and feature_version")
    try:
        return FeatureScoredParameters(
            weights=FeatureWeights(**data.get("weights", {})),
            features=FeatureParameters(**data.get("features", {})),
            feature_version=data.get("feature_version", 4),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid feature policy config: {exc}") from exc


def load_fifo_policy_parameters(path):
    """Load a flat mapping of FIFO baseline rules with strict field validation."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid FIFO policy YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("FIFO policy config must be a mapping")
    try:
        return FifoParameters(**data)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid FIFO policy config: {exc}") from exc


def load_feature_weight_space(path):
    """Load an optimization search space separately from runtime policy settings."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"trainable_weights", "fixed_weights"}:
            raise ValueError("Expected trainable_weights and fixed_weights")
        if not isinstance(data["trainable_weights"], list) or not isinstance(
            data["fixed_weights"], dict
        ):
            raise ValueError("Expected a trainable list and fixed mapping")
        return FeatureWeightSpace(**data)
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid feature weight space: {exc}") from exc
