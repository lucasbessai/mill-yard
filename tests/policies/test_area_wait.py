"""Area fairness within truck urgency, independently of bay admission benefit."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from mill_yard.model.feasibility import validate_joint
from mill_yard.model.queues import area_max_wait, truck_wait_time
from mill_yard.model.state import Assignment, InventoryState, LoaderState, TruckState, YardState
from mill_yard.model.types import Point
from mill_yard.policies.feature_scored import FeatureScoredParameters, FeatureScoredPolicy
from mill_yard.policies.features import FeatureParameters, FeatureWeights, extract_features


@pytest.fixture
def area_observation(multi_area_yard):
    yard = replace(
        multi_area_yard,
        stops=tuple(s for s in multi_area_yard.stops if s.id != "north-2"),
        loaders=(replace(multi_area_yard.loaders[0], location=Point(50, 0)),),
    )
    state = YardState(
        time=100,
        trucks={
            "z-north": TruckState(
                "z-north", 0, 10, stop_id="north-1", area_id="north", pickup_service_time=95
            ),
            "a-south": TruckState(
                "a-south", 0, 10, stop_id="south-1", area_id="south", pickup_service_time=95
            ),
            "north-old": TruckState("north-old", 10, 10, area_id="north"),
            "south-young": TruckState("south-young", 90, 10, area_id="south"),
        },
        queues={"north": ["north-old"], "south": ["south-young"]},
        stops={"north-1": "z-north", "south-1": "a-south"},
        piles={p.id: InventoryState(0) for p in yard.piles},
        infeeds={i.id: InventoryState(i.initial) for i in yard.infeeds},
        loaders={s.id: LoaderState(s.id, s.location) for s in yard.loaders},
    )
    return yard, state


def action(truck_id="z-north", quantity=10):
    return Assignment("l1", "truck", truck_id, "pile", "p", quantity)


def test_default_formula_is_bounded_three_way_mix_and_scales_partial_loads(area_observation):
    yard, state = area_observation
    expected = 0.60 * 5 / 35 + 0.20 * 2 / 7 + 0.20 * 90 / 120
    feature = extract_features(action(), state, yard)
    assert feature.truck == pytest.approx(expected)
    assert 0 <= feature.truck <= 1
    assert len(feature.as_vector()) == 5
    assert extract_features(action(quantity=5), state, yard).truck == pytest.approx(expected / 2)


def test_older_queued_truck_changes_area_priority_with_equal_selected_waits(area_observation):
    yard, state = area_observation
    before = asdict(state)
    params = FeatureScoredParameters(weights=FeatureWeights(1, 0, 0, 0))
    current = FeatureScoredPolicy(params)
    earlier = FeatureScoredPolicy(replace(params, feature_version=3))
    # Identical selected waits, counts, distances, quantities: version 3 uses ID tie.
    assert (
        earlier.features(action(), state, yard).truck
        == earlier.features(action("a-south"), state, yard).truck
    )
    assert earlier.choose(state, yard)[0].source_id == "a-south"
    chosen = current.choose(state, yard)
    assert chosen[0].source_id == "z-north"
    validate_joint(state, yard, chosen)
    assert asdict(state) == before


def test_area_age_is_local_and_reacts_when_a_queued_truck_waits_longer(area_observation):
    yard, state = area_observation
    before = extract_features(action(), state, yard).truck
    state.trucks["south-young"].arrived_at = -100
    assert extract_features(action(), state, yard).truck == before
    state.trucks["north-old"].arrived_at = 0
    assert extract_features(action(), state, yard).truck > before


def test_oldest_wait_is_not_sum_or_mean_wait(area_observation):
    yard, state = area_observation
    p = FeatureParameters(truck_area_wait_share=1)
    state.trucks["north-old"].arrived_at = 40  # One waiting 60 minutes.
    for index in range(10):
        key = f"s{index}"
        state.trucks[key] = TruckState(key, 90, 10, area_id="south")
        state.queues["south"].append(key)
    assert area_max_wait(state, "north") == 60
    assert area_max_wait(state, "south") == 10
    assert (
        extract_features(action(), state, yard, p).truck
        > extract_features(action("a-south"), state, yard, p).truck
    )


def test_service_time_excluded_and_only_unreserved_work_affects_area_wait(area_observation):
    _, state = area_observation
    oldest = state.trucks["north-old"]
    oldest.reserved_quantity = oldest.remaining_quantity
    stopped = state.trucks["z-north"]
    stopped.arrived_at = 0
    stopped.pickup_service_time = 20
    stopped.busy_since = 70
    stopped.reserved_quantity = 5  # Still has unreserved work.
    assert truck_wait_time(stopped, state.time) == 50
    assert area_max_wait(state, "north") == 50
    stopped.reserved_quantity = stopped.remaining_quantity
    assert area_max_wait(state, "north") == 0


def test_stopped_and_queued_trucks_both_contribute_to_maximum(area_observation):
    _, state = area_observation
    assert area_max_wait(state, "north") == 90
    state.trucks["z-north"].arrived_at = 0
    state.trucks["z-north"].pickup_service_time = 0
    assert area_max_wait(state, "north") == 100


def test_older_fully_reserved_truck_does_not_keep_area_priority(area_observation):
    yard, state = area_observation
    before = extract_features(action(), state, yard).truck
    state.trucks["north-old"].reserved_quantity = 10
    assert extract_features(action(), state, yard).truck < before
    state.trucks["north-old"].reserved_quantity = 9
    assert area_max_wait(state, "north") == 90


def test_zero_area_share_exactly_matches_version_three(area_observation):
    yard, state = area_observation
    params = FeatureScoredParameters(
        features=FeatureParameters(truck_area_wait_share=0),
        weights=FeatureWeights(1, 0, 0, 0),
    )
    current, previous = (
        FeatureScoredPolicy(params),
        FeatureScoredPolicy(replace(params, feature_version=3)),
    )
    for task in (action(), action("a-south")):
        assert current.features(task, state, yard) == previous.features(task, state, yard)
    assert current.choose(state, yard) == previous.choose(state, yard)


@pytest.mark.parametrize("version", [2, 3])
def test_previous_versions_ignore_new_area_parameters(area_observation, version):
    yard, state = area_observation
    params = FeatureScoredParameters(feature_version=version)
    old = FeatureScoredPolicy(params)
    other = FeatureScoredPolicy(
        replace(
            params, features=FeatureParameters(truck_area_wait_share=1, area_wait_reference=300)
        )
    )
    assert old.features(action(), state, yard) == other.features(action(), state, yard)


@pytest.mark.parametrize(
    "settings",
    [
        {"truck_area_wait_share": -0.1},
        {"truck_area_wait_share": 1.1},
        {"truck_area_wait_share": float("nan")},
        {"area_wait_reference": 0},
        {"area_wait_reference": float("inf")},
    ],
)
def test_invalid_area_feature_settings_are_rejected(settings):
    with pytest.raises(ValueError, match=r"finite|positive|shares"):
        FeatureParameters(**settings)


def test_area_reference_controls_sensitivity_without_affecting_input_state(area_observation):
    yard, state = area_observation
    before = deepcopy(state)
    small = extract_features(action(), state, yard, FeatureParameters(area_wait_reference=10))
    large = extract_features(action(), state, yard, FeatureParameters(area_wait_reference=100))
    assert small.truck > large.truck
    assert asdict(state) == asdict(before)
