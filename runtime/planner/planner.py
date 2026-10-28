from __future__ import annotations

import re
from abc import ABC, abstractmethod

from runtime.models.task import Task

_ENTITY_SPLIT_RE = re.compile(r",|\band\b", flags=re.IGNORECASE)
_LEAD_VERB_RE = re.compile(
    r"^(analyze|research|compare)\s+(.*?)[.:]", flags=re.IGNORECASE
)


class Planner(ABC):
    """Converts a natural-language objective into a list of Tasks.

    Deliberately LLM-agnostic (Section 2 goal): any implementation that returns
    a valid task list can be swapped in without touching the scheduler.
    """

    @abstractmethod
    def generate_plan(self, workflow_id: str, objective: str) -> list[Task]:
        raise NotImplementedError

    @abstractmethod
    def generate_subplan(
        self, workflow_id: str, blocked_task_id: str, replan_index: int, context: str
    ) -> list[Task]:
        """Section 5.10: given a task that discovered mid-execution it needs
        something it wasn't given (`context`, read from the task's own error
        message), produces the additional task(s) that satisfy it. Returned
        tasks must have no dependencies of their own and ids that can't
        collide with anything already in the workflow -- `replan_index`
        (how many times this specific task has already triggered a replan)
        is there to make that easy to guarantee deterministically.
        """
        raise NotImplementedError


class RuleBasedPlanner(Planner):
    """Phase 1 placeholder planner.

    Looks for an "Analyze/Research/Compare A, B, and C" pattern and fans out
    one research task per entity followed by a single comparison task, mirroring
    the worked example in Section 3. Falls back to one task for any other
    objective. This keeps the DAG/scheduler/worker path testable before a real
    LLM-backed planner (Section 5.2) is wired in.
    """

    def generate_plan(self, workflow_id: str, objective: str) -> list[Task]:
        entities = self._extract_entities(objective)
        if not entities:
            return [
                Task(
                    id=f"{workflow_id}_task_0",
                    workflow_id=workflow_id,
                    description=objective,
                    dependencies=[],
                    required_capabilities=["echo"],
                    input={"tool": "echo", "args": {"text": objective}},
                )
            ]

        research_tasks = [
            Task(
                id=f"{workflow_id}_research_{i}",
                workflow_id=workflow_id,
                description=f"Research {entity}",
                dependencies=[],
                required_capabilities=["echo"],
                input={"tool": "echo", "args": {"text": f"Research {entity}"}},
            )
            for i, entity in enumerate(entities)
        ]

        compare_task = Task(
            id=f"{workflow_id}_compare",
            workflow_id=workflow_id,
            description=f"Compare findings across: {', '.join(entities)}",
            dependencies=[t.id for t in research_tasks],
            required_capabilities=["uppercase"],
            input={"tool": "uppercase", "args": {"text": f"compare: {', '.join(entities)}"}},
        )

        return [*research_tasks, compare_task]

    def generate_subplan(
        self, workflow_id: str, blocked_task_id: str, replan_index: int, context: str
    ) -> list[Task]:
        task_id = f"{blocked_task_id}_prereq_{replan_index}"
        return [
            Task(
                id=task_id,
                workflow_id=workflow_id,
                description=f"Resolve missing prerequisite: {context}",
                dependencies=[],
                required_capabilities=["lookup"],
                input={"capability": "lookup", "args": {"query": context}},
            )
        ]

    @staticmethod
    def _extract_entities(objective: str) -> list[str]:
        match = _LEAD_VERB_RE.match(objective.strip())
        if not match:
            return []
        raw = match.group(2)
        entities = [e.strip() for e in _ENTITY_SPLIT_RE.split(raw) if e.strip()]
        return entities
