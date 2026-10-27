from __future__ import annotations

from runtime.planner.planner import RuleBasedPlanner
from runtime.planner.validator import validate_plan


def test_multi_entity_objective_fans_out_and_converges():
    planner = RuleBasedPlanner()
    tasks = planner.generate_plan("wf_1", "Analyze NVIDIA, AMD, and Intel.")

    validate_plan(tasks)  # should not raise
    research = [t for t in tasks if t.description.startswith("Research")]
    compare = [t for t in tasks if t not in research]

    assert {t.description for t in research} == {
        "Research NVIDIA",
        "Research AMD",
        "Research Intel",
    }
    assert len(compare) == 1
    assert set(compare[0].dependencies) == {t.id for t in research}


def test_unrecognized_objective_falls_back_to_single_task():
    planner = RuleBasedPlanner()
    tasks = planner.generate_plan("wf_2", "Write a haiku about distributed systems")

    assert len(tasks) == 1
    assert tasks[0].dependencies == []
