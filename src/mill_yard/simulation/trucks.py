"""Truck arrivals and FIFO bay admission within independent unloading areas."""

from mill_yard.model.state import TruckState


class TruckAdmission:
    """Keep queue routing separate from loader dispatch and handling resources."""

    def __init__(self, engine):
        self.engine = engine

    def arrivals(self):
        engine = self.engine
        for arrival in sorted(engine.scenario.arrivals, key=lambda a: (a.time, a.id)):
            yield engine.env.timeout(arrival.time - engine.env.now)
            engine._advance()
            state = engine.state
            state.trucks[arrival.id] = TruckState(
                arrival.id, arrival.time, arrival.quantity, area_id=arrival.area_id
            )
            state.queues[arrival.area_id].append(arrival.id)
            state.arrived_quantity += arrival.quantity
            state.next_arrival_index += 1
            engine._emit(
                "arrival", truck=arrival.id, quantity=arrival.quantity, area_id=arrival.area_id
            )
            engine._wake()

    def admit(self):
        engine = self.engine
        for stop in engine.scenario.stops:
            queue = engine.state.queues[stop.area_id]
            if engine.state.stops[stop.id] is None and queue:
                truck_id = queue.pop(0)
                truck = engine.state.trucks[truck_id]
                truck.stop_id = stop.id
                truck.entered_stop_at = engine.env.now
                engine.state.stops[stop.id] = truck_id
                engine._emit("stop_enter", truck=truck_id, stop=stop.id, area_id=stop.area_id)
