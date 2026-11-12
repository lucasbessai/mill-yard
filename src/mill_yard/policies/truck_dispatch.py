"""Replaceable unloading-pair rules shared by the operator-style heuristics."""

from mill_yard.model.queues import area_wait_key, queue_work_counts, unassigned_trucks


def _head(state, area_id=None):
    trucks = [t for t in unassigned_trucks(state, area_id) if t.stop_id is not None]
    return min(trucks, key=lambda t: (t.arrived_at, t.id), default=None)


def _distance(loader_id, truck, state, context):
    stop = next(s for s in context.stops if s.id == truck.stop_id)
    return state.loaders[loader_id].location.distance_to(stop.location)


def _eligible_heads(options, state):
    """Do not overtake a blocked head truck within an unloading area."""
    eligible = {a.source_id for a in options if a.source_kind == "truck"}
    return {
        area: head
        for area in state.queues
        if (head := _head(state, area)) is not None and head.id in eligible
    }


def _nearest_pair(truck, options, state, context):
    loaders = {a.loader_id for a in options if a.source_kind == "truck" and a.source_id == truck.id}
    if not loaders:
        return None
    return min(loaders, key=lambda key: (_distance(key, truck, state, context), key)), truck.id


class FifoTruckRule:
    """Global FIFO among stopped trucks, preserving the original operator rule."""

    name = "fifo"

    def select_pair(self, options, state, context):
        head = _head(state)
        return _nearest_pair(head, options, state, context) if head is not None else None

    def on_reserved(self, assignment, state):
        """FIFO has no queue affinity memory."""

    def get_state(self):
        return {}


class LongestQueueFirstRule(FifoTruckRule):
    """Largest unassigned truck count; deterministic wait/arrival/ID tie breaks."""

    name = "longest_queue_first"

    def select_pair(self, options, state, context):
        heads = _eligible_heads(options, state)
        if not heads:
            return None
        counts = queue_work_counts(state)
        area = min(heads, key=lambda key: (-counts[key], *area_wait_key(key, state)))
        return _nearest_pair(heads[area], options, state, context)


class EmptyQueueFirstRule(FifoTruckRule):
    """Each loader retains its queue until its remaining work is allocated."""

    name = "empty_queue_first"

    def __init__(self):
        self.preferred_areas = {}

    def select_pair(self, options, state, context):
        proposals = []
        loaders = sorted({a.loader_id for a in options if a.source_kind == "truck"})
        for loader_id in loaders:
            available = [a for a in options if a.loader_id == loader_id]
            heads = _eligible_heads(available, state)
            if not heads:
                continue
            preferred = self.preferred_areas.get(loader_id)
            area = (
                preferred
                if preferred in heads
                else min(heads, key=lambda key: area_wait_key(key, state))
            )
            proposals.append((loader_id, heads[area]))
        if not proposals:
            return None
        loader, truck = min(
            proposals,
            key=lambda pair: (
                pair[1].arrived_at,
                _distance(pair[0], pair[1], state, context),
                pair[0],
                pair[1].id,
            ),
        )
        return loader, truck.id

    def on_reserved(self, assignment, state):
        counts = queue_work_counts(state)
        self.preferred_areas = {
            loader: area for loader, area in self.preferred_areas.items() if counts.get(area, 0) > 0
        }
        if assignment.source_kind == "truck" and assignment.loader_id not in self.preferred_areas:
            area = state.trucks[assignment.source_id].area_id
            if counts[area] > 0:
                self.preferred_areas[assignment.loader_id] = area

    def get_state(self):
        return {"preferred_areas": dict(self.preferred_areas)}
