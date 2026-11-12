"""Complete feasible assignment-set search using proposal-based state features."""

from copy import copy, deepcopy
from dataclasses import asdict, dataclass, field
from math import fsum

from mill_yard.model.feasibility import EPS, candidates
from mill_yard.model.timing import route
from mill_yard.policies.features import (
    FeatureParameters,
    FeatureWeights,
    ObservationFeatures,
    aggregate_features,
    extract_features,
)


def _reserved_branch(state, context, assignment):
    """Reserve a candidate just generated on the parent; copy only changed fields.

    Candidate generation has already checked feasibility. Engine validation still
    checks the returned complete set before committing any live reservations.
    """
    a = assignment
    trial = copy(state)
    trial.loaders = dict(state.loaders)
    trial.loaders[a.loader_id] = copy(state.loaders[a.loader_id])
    trial.loaders[a.loader_id].assignment = a
    if a.source_kind == "truck":
        trial.trucks = dict(state.trucks)
        trial.trucks[a.source_id] = copy(state.trucks[a.source_id])
        trial.trucks[a.source_id].reserved_quantity += a.quantity
    else:
        trial.piles = dict(state.piles)
        trial.piles[a.source_id] = copy(state.piles[a.source_id])
        trial.piles[a.source_id].reserved_outgoing += a.quantity
    if a.destination_kind == "pile":
        trial.piles = dict(state.piles)
        trial.piles[a.destination_id] = copy(state.piles[a.destination_id])
        trial.piles[a.destination_id].reserved_incoming += a.quantity
    else:
        trial.infeeds = dict(state.infeeds)
        trial.infeeds[a.destination_id] = copy(state.infeeds[a.destination_id])
        trial.infeeds[a.destination_id].reserved_incoming += a.quantity
    return trial


@dataclass(frozen=True)
class FeatureScoredParameters:
    """Separate optimized weights from fixed, scenario-dependent feature choices."""

    weights: FeatureWeights = field(default_factory=FeatureWeights)
    features: FeatureParameters = field(default_factory=FeatureParameters)
    feature_version: int = 4

    def __post_init__(self):
        if not isinstance(self.weights, FeatureWeights) or not isinstance(
            self.features, FeatureParameters
        ):
            raise ValueError("Expected FeatureWeights and FeatureParameters instances")
        if type(self.feature_version) is not int or self.feature_version not in (2, 3, 4):
            raise ValueError(
                "feature_version must be 2 (legacy), 3 (local backlog), or 4 (area wait)"
            )
        if self.feature_version == 2 and self.weights.bay_release != 0:
            raise ValueError("Legacy feature_version 2 requires bay_release weight zero")


class FeatureScoredPolicy:
    """Search complete feasible assignment sets, including idle choices.

    Reservations build feasible branches only. Each leaf's score uses the same
    original observation and aggregates interacting inventory/delivery benefits.
    Quantity choices are maximal feasible batches in canonical loader order;
    arbitrary continuous batch sizes and future decisions are not searched.
    """

    def __init__(self, parameters=None):
        self.parameters = parameters or FeatureScoredParameters()
        if not isinstance(self.parameters, FeatureScoredParameters):
            raise ValueError("FeatureScoredPolicy requires FeatureScoredParameters")
        self._evaluation = None

    def metadata(self):
        """Persist the exact feature definitions and all effective parameters."""
        return {
            "dispatch_rule": "complete_feasible_sets",
            "candidate_quantities": "maximal_in_canonical_loader_order",
            **asdict(self.parameters),
        }

    def features(self, assignment, state, context):
        """Expose one pair's features for inspection or policy optimization."""
        return extract_features(
            assignment,
            state,
            context,
            self.parameters.features,
            feature_version=self.parameters.feature_version,
        )

    def score(self, assignment, state, context):
        """Return the configured four- or five-feature score for a feasible pair."""
        return self.features(assignment, state, context).score(self.parameters.weights)

    def joint_features(self, assignments, state, context):
        """Return order-independent feature totals for a feasible assignment set."""
        if (
            self._evaluation is not None
            and self._evaluation.state is state
            and self._evaluation.context is context
        ):
            return self._evaluation.joint(assignments)
        return aggregate_features(
            assignments,
            state,
            context,
            self.parameters.features,
            feature_version=self.parameters.feature_version,
        )

    def score_joint(self, assignments, state, context):
        """Score combined effects without claiming the same benefit twice."""
        values = self.joint_features(assignments, state, context)
        weights = self.parameters.weights
        return (
            weights.truck * values["truck"]
            + weights.mill * values["mill"]
            + weights.inventory * values["inventory"]
            - weights.cost * values["cost"]
            + weights.bay_release * values["bay_release"]
        )

    def choose(self, state, context):
        """Compare every generated complete set before committing any live work."""
        ids = sorted(
            loader.id for loader in context.loaders if state.loaders[loader.id].assignment is None
        )

        def assignment_key(a):
            return (
                a.loader_id,
                a.source_kind,
                a.source_id,
                a.destination_kind,
                a.destination_id,
                a.quantity,
            )

        def tie_key(assignments):
            distances = []
            for a in assignments:
                source, destination = route(a, state, context)
                distances.append(
                    state.loaders[a.loader_id].location.distance_to(source)
                    + source.distance_to(destination)
                )
            return (
                fsum(distances),
                len(assignments),
                tuple(sorted(map(assignment_key, assignments))),
            )

        best, best_score, best_tie = [], 0.0, (0.0, 0, ())

        def search(index, trial, chosen):
            nonlocal best, best_score, best_tie
            if index == len(ids):
                value = self.score_joint(chosen, state, context)
                key = tie_key(chosen)
                if value > best_score + EPS or (abs(value - best_score) <= EPS and key < best_tie):
                    best, best_score, best_tie = list(chosen), value, key
                return
            search(index + 1, trial, chosen)  # This loader remains idle.
            options = sorted(
                (a for a in candidates(trial, context) if a.loader_id == ids[index]),
                key=assignment_key,
            )
            for a in options:
                next_state = _reserved_branch(trial, context, a)
                search(index + 1, next_state, [*chosen, a])

        self._evaluation = ObservationFeatures(
            state, context, self.parameters.features, self.parameters.feature_version
        )
        try:
            search(0, deepcopy(state), [])
        finally:
            self._evaluation = None
        return best

    def wake_levels(self, state, context):
        """Use existing low-inventory crossings when policy wake mode is selected."""
        return {i.id: i.low for i in context.infeeds if i.consumption_rate > 0}
