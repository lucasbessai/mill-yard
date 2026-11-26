"""Human decisions share the engine's reservation and feasibility rules."""

from dataclasses import asdict

import pytest

from mill_yard.policies.human_controlled import (
    HumanControlledPolicy,
    HumanDecision,
    TerminalHumanPolicy,
    prompt_for_decision,
)
from mill_yard.simulation.engine import YardSimulation, run_yard


def test_draft_updates_feasible_choices_without_changing_live_state(tiny_yard):
    simulation = YardSimulation(tiny_yard, HumanControlledPolicy())
    simulation.step()
    before = asdict(simulation.state)
    decision = HumanDecision(simulation.pending_decision)
    first = decision.options[0]
    decision.add(first, 3)
    assert all(a.loader_id != first.loader_id for a in decision.options)
    with pytest.raises(ValueError, match="infeasible"):
        decision.add(first)
    decision.undo()
    assert len(decision.options) == 2
    assert asdict(simulation.state) == before


def test_terminal_prompt_supports_partial_quantity_undo_and_joint_commit(tiny_yard):
    simulation = YardSimulation(tiny_yard, HumanControlledPolicy())
    simulation.step()
    commands = iter(["1 3", "undo", "1", "1", "done"])
    choices = prompt_for_decision(
        simulation.pending_decision, read=lambda _: next(commands), write=lambda _: None
    )
    assert len(choices) == 2
    assert sum(a.quantity for a in choices) == 10
    simulation.submit_decision(choices)
    while not simulation.finished:
        simulation.step()
    assert len(simulation.completed_trucks) == 1


def test_terminal_policy_can_finish_a_run(tiny_yard, monkeypatch):
    commands = iter(["1", "1", "done"])
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    result = run_yard(tiny_yard, TerminalHumanPolicy(), validate=True)
    assert result.state.time == 6
    assert len(result.completed_trucks) == 1


def test_terminal_wait_and_quit(tiny_yard):
    simulation = YardSimulation(tiny_yard, HumanControlledPolicy())
    simulation.step()
    assert (
        prompt_for_decision(
            simulation.pending_decision, read=lambda _: "wait", write=lambda _: None
        )
        == []
    )
    with pytest.raises(EOFError, match="Operator ended"):
        prompt_for_decision(
            simulation.pending_decision, read=lambda _: "quit", write=lambda _: None
        )
