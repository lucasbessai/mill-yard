"""Optimization profiles omit fixed weights rather than searching them."""

from pathlib import Path

import pytest

from mill_yard.config.policy import load_feature_weight_space
from mill_yard.optimization.weight_space import FeatureWeightSpace


def test_four_and_five_profiles_have_explicit_named_vector_dimensions():
    root = Path(__file__).resolve().parents[2] / "configs/optimization"
    four = load_feature_weight_space(root / "feature_scored__4trainable_weights.yaml")
    five = load_feature_weight_space(root / "feature_scored__5trainable_weights.yaml")
    assert four.trainable_weights == ("truck", "mill", "inventory", "cost")
    assert "bay_release" not in four.trainable_weights
    assert four.from_vector([0.25, 0.4, 0.15, 0.2]).bay_release == 0
    assert five.from_vector([0.2, 0.35, 0.1, 0.15, 0.2]).bay_release == 0.2
    with pytest.raises(ValueError, match="length"):
        four.from_vector([0.2] * 5)
    with pytest.raises(ValueError, match="exactly"):
        four.build_weights({"bay_release": 0, "truck": 1})


def test_nonzero_fixed_weight_reserves_its_simplex_budget():
    space = FeatureWeightSpace(fixed_weights={"bay_release": 0.2})
    assert space.trainable_budget == 0.8
    assert space.from_vector([0.2] * 4).bay_release == 0.2
    with pytest.raises(ValueError, match="sum to one"):
        space.from_vector([0.25] * 4)
    metadata = space.metadata()
    metadata["fixed_weights"]["bay_release"] = 1
    assert space.fixed_weights["bay_release"] == 0.2


@pytest.mark.parametrize(
    "content",
    [
        "trainable_weights: [truck, truck]\nfixed_weights: {}",
        "trainable_weights: [truck, mill, inventory, cost, typo]\nfixed_weights: {}",
        "trainable_weights: [truck, mill, inventory, cost]\nfixed_weights: {bay_release: -.1}",
        "trainable_weights: [truck, mill, inventory, cost]\nfixed_weights: {bay_release: 1}",
        "trainable_weights: [truck, mill, inventory, cost]\nfixed_weights: {bay_release: .nan}",
        "trainable_weights: [truck, mill, inventory, cost]\nfixed_weights: {bay_release: .2}\nextra: 1",
    ],
)
def test_invalid_weight_spaces_are_rejected(tmp_path, content):
    path = tmp_path / "weights.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid feature weight space"):
        load_feature_weight_space(path)
