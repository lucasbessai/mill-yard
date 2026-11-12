"""Feature policy YAML validation and reproducible defaults."""

from pathlib import Path

import pytest

from mill_yard.config.policy import load_feature_policy_parameters, load_fifo_policy_parameters
from mill_yard.policies.feature_scored import FeatureScoredParameters
from mill_yard.policies.fifo import FifoParameters


def test_example_feature_configuration_matches_python_defaults():
    path = Path(__file__).resolve().parents[2] / "configs/policies/feature_scored__v4_4weights.yaml"
    assert load_feature_policy_parameters(path) == FeatureScoredParameters()


def test_five_feature_and_legacy_examples_record_distinct_definitions():
    root = Path(__file__).resolve().parents[2] / "configs/policies"
    five = load_feature_policy_parameters(root / "feature_scored__v4_5weights.yaml")
    legacy = load_feature_policy_parameters(root / "legacy/feature_scored__v2_4weights.yaml")
    assert five.feature_version == 4
    assert five.weights.bay_release == 0.2
    assert legacy.feature_version == 2
    assert legacy.weights.bay_release == 0


def test_previous_local_queue_profiles_remain_available():
    root = Path(__file__).resolve().parents[2] / "configs/policies"
    for filename, release in (
        ("legacy/feature_scored__v3_4weights.yaml", 0),
        ("legacy/feature_scored__v3_5weights.yaml", 0.2),
    ):
        p = load_feature_policy_parameters(root / filename)
        assert p.feature_version == 3
        assert p.weights.bay_release == release


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "other: 1",
        "features: {typo: 2}",
        "weights: {truck: 1}",
        "features: {wait_reference: 0}",
        "features: []",
        "weights: {truck: .nan}",
        "bad: [",
    ],
)
def test_invalid_policy_yaml_is_rejected(tmp_path, content):
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=r"Feature policy|Invalid feature"):
        load_feature_policy_parameters(path)


def test_fifo_example_configuration_matches_python_defaults():
    path = Path(__file__).resolve().parents[2] / "configs/policies/fifo.yaml"
    assert load_fifo_policy_parameters(path) == FifoParameters()


@pytest.mark.parametrize(
    "content", ["[]", "other: 1", "low_fraction: 0.8", "buffer_cycles: -1", "bad: ["]
)
def test_invalid_fifo_policy_yaml_is_rejected(tmp_path, content):
    path = tmp_path / "fifo.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=r"FIFO|Invalid FIFO"):
        load_fifo_policy_parameters(path)
