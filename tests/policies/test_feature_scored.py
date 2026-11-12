"""Proposal equations, deliberate corrections, and dispatch integration."""

from copy import deepcopy
from dataclasses import asdict, replace
from math import inf
from pathlib import Path

import pytest

from mill_yard.model.feasibility import candidates, reserve, validate_joint
from mill_yard.model.state import Assignment, InventoryState, LoaderState, TruckState, YardState
from mill_yard.model.types import Point
from mill_yard.policies.base import PolicyContext
from mill_yard.policies.factory import create_policy
from mill_yard.policies.feature_scored import FeatureScoredParameters, FeatureScoredPolicy
from mill_yard.policies.features import (
    DispatchFeatures,
    FeatureParameters,
    FeatureWeights,
    extract_features,
    pile_target,
    time_to_stockout,
)
from mill_yard.policies.scored import ScoredParameters, ScoredPolicy
from mill_yard.simulation.engine import run_yard


@pytest.fixture
def observation(tiny_yard):
    scenario = replace(
        tiny_yard,
        horizon=120,
        piles=(replace(tiny_yard.piles[0], initial=10),),
        infeeds=(replace(tiny_yard.infeeds[0], initial=0, consumption_rate=1),),
    )
    state = YardState(
        time=15,
        trucks={"t": TruckState("t", 0, 10, stop_id="bay")},
        queues={"main": []},
        stops={"bay": "t"},
        piles={"pile": InventoryState(10)},
        infeeds={"mill": InventoryState(0)},
        loaders={loader.id: LoaderState(loader.id, loader.location) for loader in scenario.loaders},
    )
    return scenario, state, PolicyContext.from_scenario(scenario)


def test_proposal_table_7_scores_are_reproduced():
    weights = FeatureWeights()
    assert DispatchFeatures(0.8, 0, 0.7, 0.2).score(weights) == pytest.approx(0.265)
    assert DispatchFeatures(0.8, 0.9, 0, 0.3).score(weights) == pytest.approx(0.500)
    assert DispatchFeatures(0, 0.9, 0, 0.15).score(weights) == pytest.approx(0.330)
    assert DispatchFeatures(0, 0, 0, 0).score(weights) == 0


@pytest.mark.parametrize(
    "weights",
    [
        {"truck": -0.1},
        {"truck": 1},
        {"mill": float("nan")},
        {"cost": float("inf")},
    ],
)
def test_invalid_simplex_weights_are_rejected(weights):
    with pytest.raises(ValueError, match="Feature weights"):
        FeatureWeights(**weights)


@pytest.mark.parametrize(
    "parameters",
    [
        {"wait_reference": 0},
        {"queue_reference": -1},
        {"mill_stockout_share": 1.1},
        {"holding_cost": -1},
        {"residence_time": float("nan")},
        {"inventory_reference": 0},
    ],
)
def test_invalid_fixed_feature_parameters_are_rejected(parameters):
    with pytest.raises(ValueError, match=r"must|shares"):
        FeatureParameters(**parameters)


def test_truck_feature_uses_wait_excluding_service_and_scales_partial_batches(observation):
    _, state, context = observation
    a = Assignment("l1", "truck", "t", "pile", "pile", 5)
    features = extract_features(a, state, context, feature_version=2)
    assert features.truck == pytest.approx(0.5 * 0.75 * (15 / 30))
    state.trucks["t"].pickup_service_time = 4
    assert extract_features(a, state, context, feature_version=2).truck == pytest.approx(
        0.5 * 0.75 * (11 / 30)
    )
    assert extract_features(
        replace(a, quantity=2.5), state, context, feature_version=2
    ).truck == pytest.approx(features.truck * 11 / 15 / 2)
    state.queues["main"] = [f"q{i}" for i in range(5)]
    state.trucks.update({key: TruckState(key, 0, 10) for key in state.queues["main"]})
    assert extract_features(a, state, context, feature_version=2).truck == pytest.approx(
        0.5 * (0.75 * 11 / 30 + 0.25)
    )


def test_cost_combines_distance_active_time_and_holding_without_mutation(observation):
    _, state, context = observation
    before = asdict(state)
    a = Assignment("l1", "truck", "t", "pile", "pile", 5)
    f = extract_features(a, state, context)
    assert f.cost == pytest.approx((0.01 * 10 + 1 * 3 + 0.001 * 5 * 60) / 30)
    assert f.mill == 0
    assert asdict(state) == before


def test_stockout_matches_same_time_settlement_and_zero_demand():
    assert time_to_stockout(5, 1, [(5, 5)]) == 10
    assert time_to_stockout(5, 1, [(6, 5)]) == 5
    assert time_to_stockout(0, 1, [(1, 5)]) == 0
    assert time_to_stockout(0, 1, [(0, 5)]) == 5
    assert time_to_stockout(5, 1, [(5, 2), (5, 3)]) == 10
    assert time_to_stockout(0, 0, []) == inf


def test_slower_loader_never_receives_a_larger_mill_feature(observation):
    scenario, state, _ = observation
    scenario = replace(
        scenario,
        loaders=(replace(scenario.loaders[0], speed=100), replace(scenario.loaders[1], speed=1)),
    )
    context = PolicyContext.from_scenario(scenario)
    state.infeeds["mill"].amount = 5
    a = Assignment("l1", "pile", "pile", "infeed", "mill", 5)
    assert (
        extract_features(a, state, context).mill
        > extract_features(replace(a, loader_id="l2"), state, context).mill
    )


def test_committed_deliveries_remove_duplicate_shortage_credit(observation):
    _, state, context = observation
    p = FeatureParameters(mill_time_reference=5)
    task = Assignment("l2", "pile", "pile", "infeed", "mill", 5)
    before = extract_features(task, state, context, p).mill
    assert before > 0
    loader = state.loaders["l1"]
    loader.assignment = replace(task, loader_id="l1")
    loader.phase, loader.carried, loader.phase_completes_at = "deposit", 5, state.time + 1
    state.infeeds["mill"].reserved_incoming = 5
    assert extract_features(task, state, context, p).mill == 0
    loader.phase_completes_at = state.time + 100
    assert extract_features(task, state, context, p).mill == pytest.approx(before)


def test_zero_rate_and_beyond_horizon_jobs_receive_no_mill_credit(observation):
    scenario, state, context = observation
    a = Assignment("l1", "pile", "pile", "infeed", "mill", 5)
    state.time = context.horizon - 1
    assert extract_features(a, state, context).mill == 0
    scenario = replace(scenario, infeeds=(replace(scenario.infeeds[0], consumption_rate=0),))
    state.time = 15
    assert extract_features(a, state, PolicyContext.from_scenario(scenario)).mill == 0


def test_inventory_uses_committed_levels_and_avoids_repeated_target_credit(observation):
    _, state, context = observation
    state.piles["pile"].amount = 0
    fixed = FeatureParameters(pile_lead_time=0, yard_safety_stock=5)
    policy = FeatureScoredPolicy(FeatureScoredParameters(FeatureWeights(0, 0, 1, 0), fixed))
    first = Assignment("l1", "truck", "t", "pile", "pile", 5)
    assert policy.features(first, state, context).inventory == pytest.approx(0.25)
    trial = deepcopy(state)
    reserve(trial, context, first)
    assert policy.features(replace(first, loader_id="l2"), trial, context).inventory == 0
    selected = policy.choose(state, context)
    assert selected == [first]
    assert state.piles["pile"].reserved_incoming == 0
    validate_joint(state, context, selected)


def test_multiple_piles_share_demand_coverage_instead_of_duplicating_it(observation):
    scenario, _, _ = observation
    scenario = replace(
        scenario,
        piles=(
            replace(scenario.piles[0], capacity=100),
            replace(scenario.piles[0], id="p2", capacity=300),
        ),
    )
    p = FeatureParameters(pile_lead_time=30, yard_safety_stock=10)
    assert [pile_target(spec, scenario, p) for spec in scenario.piles] == [10, 30]


def test_joint_search_and_canonical_ties_ignore_yaml_loader_order(observation):
    scenario, state, _ = observation
    state.piles["pile"].amount = 0
    state.loaders["l1"].location = Point(100, 0)
    params = FeatureScoredParameters(
        FeatureWeights(0, 0, 1, 0), FeatureParameters(pile_lead_time=0, yard_safety_stock=5)
    )
    policy = FeatureScoredPolicy(params)
    chosen = policy.choose(state, PolicyContext.from_scenario(scenario))
    assert [a.loader_id for a in chosen] == ["l2"]
    assert (
        policy.choose(
            state, PolicyContext.from_scenario(replace(scenario, loaders=scenario.loaders[::-1]))
        )
        == chosen
    )
    state.loaders["l1"].location = Point(0, 0)
    assert policy.choose(state, scenario)[0].loader_id == "l1"


def test_negative_and_zero_scores_choose_idle(observation):
    _, state, context = observation
    for features in (
        FeatureParameters(),
        FeatureParameters(distance_cost=0, active_time_cost=0, holding_cost=0),
    ):
        policy = FeatureScoredPolicy(FeatureScoredParameters(FeatureWeights(0, 0, 0, 1), features))
        assert policy.choose(state, context) == []


def test_factory_keeps_parameter_families_separate_when_gui_changes_policy():
    feature = FeatureScoredParameters(weights=FeatureWeights(1, 0, 0, 0))
    original = ScoredParameters(truck_weight=4)
    assert isinstance(create_policy("feature-scored", parameters=original), FeatureScoredPolicy)
    assert create_policy("feature-scored", parameters=feature).parameters == feature
    assert isinstance(create_policy("scored", parameters=feature), ScoredPolicy)
    assert create_policy("scored", parameters=original).parameters == original


@pytest.mark.parametrize(
    "filename",
    [
        "archive/legacy_single_bay_14h/experiment.yaml",
        "experiments/validation/1area_multi_mill__004trucks_mixed_stock_120min.yaml",
        "experiments/traffic/1area_shared__003trucks_1loader_180min.yaml",
        "experiments/traffic/1area_shared__003trucks_2loaders_180min.yaml",
        "experiments/traffic/1area_shared__015trucks_1loader_180min.yaml",
        "experiments/traffic/1area_shared__015trucks_2loaders_180min.yaml",
        "experiments/traffic/1area_shared__045trucks_1loader_180min.yaml",
        "experiments/traffic/1area_shared__045trucks_2loaders_180min.yaml",
    ],
)
def test_feature_policy_runs_scenarios_with_physical_invariants(filename):
    from mill_yard.config.loader import load_yard_scenario

    scenario = load_yard_scenario(Path(__file__).resolve().parents[2] / "configs" / filename)
    result = run_yard(scenario, FeatureScoredPolicy(), record="summary", validate=True)
    assert result.state.time == scenario.horizon
    assert result.policy_parameters["weights"] == {
        "truck": 0.25,
        "mill": 0.4,
        "inventory": 0.15,
        "cost": 0.2,
        "bay_release": 0.0,
    }
    assert result.policy_parameters["feature_version"] == 4
    assert result.policy_parameters["dispatch_rule"] == "complete_feasible_sets"


def test_all_extracted_features_are_bounded(observation):
    _, state, context = observation
    for a in candidates(state, context):
        assert all(0 <= value <= 1 for value in extract_features(a, state, context).as_vector())


def test_complete_set_search_finds_the_documented_better_loader_combination(observation):
    _, state, context = observation
    state.trucks["t"].remaining_quantity = 5
    state.piles["pile"].amount = 5
    state.infeeds["mill"].amount = 5

    class IllustrativePolicy(FeatureScoredPolicy):
        """Independent task benefits isolate search from the production features."""

        def score(self, assignment, state, context):
            return {
                ("l1", "truck"): 0.40,
                ("l1", "pile"): 0.35,
                ("l2", "truck"): 0.30,
                ("l2", "pile"): 0.05,
            }[assignment.loader_id, assignment.source_kind]

        def score_joint(self, assignments, state, context):
            return sum(self.score(a, state, context) for a in assignments)

    policy = IllustrativePolicy()
    trial, sequential = deepcopy(state), []
    while options := candidates(trial, context):
        chosen = max(options, key=lambda a: policy.score(a, trial, context))
        reserve(trial, context, chosen)
        sequential.append(chosen)
    joint = policy.choose(state, context)
    assert {(a.loader_id, a.source_kind) for a in sequential} == {("l1", "truck"), ("l2", "pile")}
    assert {(a.loader_id, a.source_kind) for a in joint} == {("l1", "pile"), ("l2", "truck")}
    assert policy.score_joint(sequential, state, context) == pytest.approx(0.45)
    assert policy.score_joint(joint, state, context) == pytest.approx(0.65)
    validate_joint(state, context, joint)
    assert state.trucks["t"].reserved_quantity == 0


def test_joint_score_is_permutation_invariant_and_groups_simultaneous_deliveries(observation):
    _, state, context = observation
    policy = FeatureScoredPolicy(
        FeatureScoredParameters(
            FeatureWeights(0, 1, 0, 0), FeatureParameters(mill_time_reference=8)
        )
    )
    first = Assignment("l1", "pile", "pile", "infeed", "mill", 5)
    second = replace(first, loader_id="l2")
    validate_joint(state, context, [first, second])
    # One 5-t delivery already supplies the remaining four minutes after arrival.
    assert policy.score_joint([first, second], state, context) == pytest.approx(
        policy.score(first, state, context)
    )
    assert policy.joint_features([first, second], state, context) == policy.joint_features(
        [second, first], state, context
    )
    assert len(policy.choose(state, context)) == 1


def test_joint_inventory_feature_uses_net_change_not_positive_prefixes(observation):
    _, state, context = observation
    policy = FeatureScoredPolicy(
        FeatureScoredParameters(
            FeatureWeights(0, 0, 1, 0), FeatureParameters(pile_lead_time=0, yard_safety_stock=15)
        )
    )
    unload = Assignment("l1", "truck", "t", "pile", "pile", 5)
    refill = Assignment("l2", "pile", "pile", "infeed", "mill", 5)
    validate_joint(state, context, [unload, refill])
    assert policy.score(unload, state, context) > 0
    assert policy.score_joint([unload, refill], state, context) == 0
    assert policy.joint_features([unload, refill], state, context) == policy.joint_features(
        [refill, unload], state, context
    )


def test_search_respects_shared_material_and_includes_remainder_batches(observation):
    _, state, context = observation
    state.trucks["t"].remaining_quantity = 7
    state.piles["pile"].amount = 0
    state.infeeds["mill"].amount = 10
    policy = FeatureScoredPolicy(FeatureScoredParameters(FeatureWeights(1, 0, 0, 0)))
    before = asdict(state)
    chosen = policy.choose(state, context)
    assert sorted(a.quantity for a in chosen) == [2, 5]
    validate_joint(state, context, chosen)
    assert asdict(state) == before


def test_singleton_joint_score_matches_pair_score(observation):
    _, state, context = observation
    policy = FeatureScoredPolicy()
    for assignment in candidates(state, context):
        assert policy.score_joint([assignment], state, context) == pytest.approx(
            policy.score(assignment, state, context)
        )
