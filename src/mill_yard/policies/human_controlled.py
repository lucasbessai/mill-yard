"""Nonblocking human dispatch and a terminal adapter using the same decisions."""

from copy import deepcopy
from dataclasses import replace

from mill_yard.model.feasibility import EPS, candidates, reserve
from mill_yard.policies.base import DecisionRequest


def describe_assignment(assignment):
    """Format one feasible operation for graphical and terminal menus."""
    a = assignment
    return (
        f"{a.loader_id}: {a.source_kind} {a.source_id} → "
        f"{a.destination_kind} {a.destination_id} | up to {a.quantity:.2f} t"
    )


class HumanDecision:
    """Build a feasible assignment set on a detached reservation view."""

    def __init__(self, request):
        self.request = request
        self.assignments = []
        self.draft_state = deepcopy(request.state)

    @property
    def options(self):
        """Recompute choices after each tentative assignment."""
        return candidates(self.draft_state, self.request.context)

    def add(self, assignment, quantity=None):
        """Reserve a choice, optionally reducing its maximum quantity."""
        selected = assignment if quantity is None else replace(assignment, quantity=quantity)
        reserve(self.draft_state, self.request.context, selected)
        self.assignments.append(selected)

    def undo(self):
        """Remove the latest assignment and rebuild tentative reservations."""
        if self.assignments:
            self.assignments.pop()
        self.draft_state = deepcopy(self.request.state)
        for assignment in self.assignments:
            reserve(self.draft_state, self.request.context, assignment)

    def clear(self):
        """Leave every currently idle loader unassigned in this decision."""
        self.assignments = []
        self.draft_state = deepcopy(self.request.state)


class HumanControlledPolicy:
    """Pause for input whenever there is feasible dispatch work."""

    def choose(self, state, context):
        """Let the engine continue automatically when no work can be assigned."""
        return DecisionRequest(state, context) if candidates(state, context) else []

    def wake_levels(self, state, context):
        """Use infeed low thresholds when policy-driven wake-ups are selected."""
        return {
            spec.id: spec.low if state.infeeds[spec.id].amount > spec.low + EPS else 0.0
            for spec in context.infeeds
        }


def prompt_for_decision(request, *, read=input, write=print):
    """Collect a human assignment set from a terminal without moving the clock."""
    decision = HumanDecision(request)
    write(f"\nDispatch at {request.state.time:.3f} min")
    write(
        "Enter a task number and optional quantity: '2 10'. Use done, wait, undo, clear, or quit."
    )
    while True:
        options = decision.options
        for index, assignment in enumerate(options, 1):
            write(f"  {index}: {describe_assignment(assignment)}")
        write(f"Draft: {len(decision.assignments)} assignment(s)")
        command = read("dispatch> ").strip().lower()
        if command == "done":
            return list(decision.assignments)
        if command == "wait":
            return []
        if command == "undo":
            decision.undo()
        elif command == "clear":
            decision.clear()
        elif command in ("quit", "q", "exit"):
            raise EOFError("Operator ended the run")
        else:
            try:
                fields = command.split()
                if not 1 <= len(fields) <= 2:
                    raise ValueError("Use a task number followed by an optional quantity")
                index = int(fields[0]) - 1
                if not 0 <= index < len(options):
                    raise ValueError("Task number is outside the menu")
                decision.add(options[index], float(fields[1]) if len(fields) == 2 else None)
            except ValueError as exc:
                write(str(exc))


class TerminalHumanPolicy(HumanControlledPolicy):
    """Synchronously prompt the terminal at every feasible dispatch decision."""

    def choose(self, state, context):
        request = super().choose(state, context)
        return (
            prompt_for_decision(request, read=input)
            if isinstance(request, DecisionRequest)
            else request
        )
