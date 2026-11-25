"""Queue-local urgency and marginal, joint bay-admission benefit."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from mill_yard.model.feasibility import candidates, reserve, validate_joint
from mill_yard.model.state import Assignment, InventoryState, LoaderState, TruckState, YardState
from mill_yard.model.types import Point
from mill_yard.policies.bay_release import BayReleaseForecast
from mill_yard.policies.feature_scored import (
    FeatureScoredParameters,
    FeatureScoredPolicy,
    _reserved_branch,
)
from mill_yard.policies.features import (
    FeatureParameters,
    FeatureWeights,
    ObservationFeatures,
    aggregate_features,
    extract_features,
)


@pytest.fixture
def bay_observation(tiny_yard):
    yard = replace(tiny_yard, horizon=120)
    state = YardState(
        time=30,
        trucks={"t": TruckState("t", 0, 10, stop_id="bay"), "q": TruckState("q", 10, 10)},
        queues={"main": ["q"]},
        stops={"bay": "t"},
        piles={"pile": InventoryState(0)},
        infeeds={"mill": InventoryState(0)},
        loaders={s.id: LoaderState(s.id, Point(0, 0)) for s in yard.loaders},
    )
    return yard, state


def test_two_partial_pickups_jointly_release_exactly_one_bay(bay_observation):
    yard, state = bay_observation
    first = Assignment("l1", "truck", "t", "pile", "pile", 5)
    second = replace(first, loader_id="l2")
    policy = FeatureScoredPolicy(FeatureScoredParameters(FeatureWeights(0, 0, 0, 0, 1)))
    validate_joint(state, yard, [first, second])
    before = asdict(state)
    assert policy.features(first, state, yard).bay_release == 0
    forecast = BayReleaseForecast(state, yard, FeatureParameters())
    expected = (30 - forecast.pickup_time(first)) / (30 * 5)
    assert policy.joint_features([first, second], state, yard)["bay_release"] == pytest.approx(
        expected
    )
    assert policy.score_joint([first, second], state, yard) == policy.score_joint(
        [second, first], state, yard
    )
    assert policy.choose(state, yard) == [first, second]
    assert asdict(state) == before
    assert policy._evaluation is None


def test_release_uses_last_pickup_not_deposit_and_no_queue_means_no_credit(bay_observation):
    yard, state = bay_observation
    task = Assignment("l1", "truck", "t", "pile", "pile", 10)
    p = FeatureParameters()
    forecast = BayReleaseForecast(state, yard, p)
    fast = forecast.benefit([task])
    slow_deposit = replace(
        yard, loaders=(replace(yard.loaders[0], deposit_rate=0.001), *yard.loaders[1:])
    )
    assert BayReleaseForecast(state, slow_deposit, p).benefit([task]) == fast > 0
    state.queues["main"].clear()
    assert BayReleaseForecast(state, yard, p).benefit([task]) == 0


def test_committed_pickup_sets_release_time_and_is_not_rewarded_twice(bay_observation):
    yard, state = bay_observation
    task = Assignment("l2", "truck", "t", "pile", "pile", 5)
    loader = state.loaders["l1"]
    loader.assignment = replace(task, loader_id="l1")
    loader.phase, loader.phase_completes_at = "pickup", state.time + 12
    state.trucks["t"].reserved_quantity = 5
    p = FeatureParameters()
    assert BayReleaseForecast(state, yard, p).benefit([task]) == pytest.approx((30 - 12) / 150)
    state.trucks["t"].reserved_quantity = 10
    loader.assignment = replace(loader.assignment, quantity=10)
    assert BayReleaseForecast(state, yard, p).benefit([]) == 0


@pytest.mark.parametrize(
    ("phase", "remaining"), [("pickup", 2), ("travel_to_source", 4), ("waiting_pickup", 0)]
)
def test_running_pickup_phase_estimates(bay_observation, phase, remaining):
    yard, state = bay_observation
    loader = state.loaders["l1"]
    loader.assignment = Assignment("l1", "truck", "t", "pile", "pile", 5)
    loader.phase, loader.phase_completes_at = phase, state.time + remaining
    expected = remaining if phase == "pickup" else remaining + 5 / yard.loaders[0].pickup_rate
    assert BayReleaseForecast(state, yard, FeatureParameters()).committed["t"][0][1] == expected


def test_late_release_and_exhausted_horizon_receive_zero_credit(bay_observation):
    yard, state = bay_observation
    task = Assignment("l1", "truck", "t", "pile", "pile", 10)
    state.loaders["l1"].location = Point(10000, 0)
    assert BayReleaseForecast(state, yard, FeatureParameters()).benefit([task]) == 0
    state.time = yard.horizon
    assert BayReleaseForecast(state, yard, FeatureParameters()).benefit([task]) == 0


def test_waiting_trucks_use_existing_free_bays_before_new_release(bay_observation):
    yard, state = bay_observation
    yard = replace(yard, stops=(*yard.stops, replace(yard.stops[0], id="free")))
    state.stops["free"] = None
    task = Assignment("l1", "truck", "t", "pile", "pile", 10)
    assert BayReleaseForecast(state, yard, FeatureParameters()).benefit([task]) == 0


def test_admission_credit_is_local_and_bounded_by_actual_waiters(multi_area_yard):
    yard = multi_area_yard
    state = YardState(
        time=30,
        trucks={
            "n": TruckState("n", 0, 10, stop_id="north-1", area_id="north"),
            "s": TruckState("s", 0, 10, stop_id="south-1", area_id="south"),
            "q": TruckState("q", 0, 10, area_id="south"),
        },
        queues={"north": [], "south": ["q"]},
        stops={"north-1": "n", "north-2": None, "south-1": "s"},
        piles={p.id: InventoryState(0) for p in yard.piles},
        infeeds={i.id: InventoryState(0) for i in yard.infeeds},
        loaders={s.id: LoaderState(s.id, s.location) for s in yard.loaders},
    )
    north = Assignment("l1", "truck", "n", "pile", "p", 10)
    south = replace(north, loader_id="l2", source_id="s")
    forecast = BayReleaseForecast(state, yard, FeatureParameters())
    assert forecast.benefit([north]) == 0
    assert forecast.benefit([south]) > 0
    assert forecast.benefit([north, south]) == forecast.benefit([south])


def test_queue_local_smooth_urgency_retains_long_wait_distinctions(bay_observation):
    yard, state = bay_observation
    a = Assignment("l1", "truck", "t", "pile", "pile", 5)
    f = extract_features(a, state, yard)
    assert f.truck == pytest.approx(0.5 * (0.60 * 30 / 60 + 0.20 * 2 / 7 + 0.20 * 30 / 60))
    state.time = 90
    assert extract_features(a, state, yard).truck > f.truck
    state.trucks["q"].reserved_quantity = 10
    lower = extract_features(a, state, yard).truck
    state.trucks["q"].reserved_quantity = 0
    assert extract_features(a, state, yard).truck > lower
    previous = deepcopy(state)
    state.queues["other"] = ["remote"]
    state.trucks["remote"] = TruckState("remote", 0, 100, area_id="other")
    assert extract_features(a, state, yard).truck == extract_features(a, previous, yard).truck


def test_legacy_profile_has_old_normalization_and_rejects_bay_weight(bay_observation):
    yard, state = bay_observation
    a = Assignment("l1", "truck", "t", "pile", "pile", 5)
    legacy = FeatureScoredPolicy(FeatureScoredParameters(feature_version=2))
    assert legacy.features(a, state, yard).truck == pytest.approx(0.5 * (0.75 + 0.25 / 5))
    assert legacy.features(a, state, yard).bay_release == 0
    with pytest.raises(ValueError, match="Legacy"):
        FeatureScoredParameters(FeatureWeights(0, 0, 0, 0, 1), feature_version=2)


@pytest.mark.parametrize("name", ["bay_time_reference", "bay_queue_reference"])
def test_bay_reference_scales_must_be_positive(name):
    with pytest.raises(ValueError, match="positive"):
        FeatureParameters(**{name: 0})


def test_reservation_branches_match_deepcopy_and_do_not_mutate_parents(bay_observation):
    yard, state = bay_observation
    state.piles["pile"].amount = 10
    original = asdict(state)
    for a in candidates(state, yard):
        branch = _reserved_branch(state, yard, a)
        expected = deepcopy(state)
        reserve(expected, yard, a)
        assert asdict(branch) == asdict(expected)
        parent = asdict(branch)
        for b in candidates(branch, yard):
            _reserved_branch(branch, yard, b)
            assert asdict(branch) == parent
        assert asdict(state) == original


def test_cached_features_match_uncached_sets_across_shared_effects(bay_observation):
    yard, state = bay_observation
    state.piles["pile"].amount = 10
    for version in (2, 3, 4):
        cache = ObservationFeatures(state, yard, FeatureParameters(), version)
        for first in candidates(state, yard):
            branch = _reserved_branch(state, yard, first)
            for chosen in [[first], *([first, b] for b in candidates(branch, yard))]:
                expected = aggregate_features(chosen, state, yard, feature_version=version)
                assert cache.joint(chosen) == pytest.approx(expected)
                assert cache.joint(chosen[::-1]) == pytest.approx(expected)
