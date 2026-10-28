from __future__ import annotations

import random
import uuid

from sqlalchemy.orm import Session

from runtime.models.task import Task
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository

DEFAULT_EXPLORE_PROBABILITY = 0.3


def generate_cold_start_workflow(
    session: Session, n_tasks: int, *, capability: str = "lookup"
) -> str:
    """Builds one workflow of `n_tasks` independent, capability-addressed
    tasks -- Section 15C.3's "controlled benchmark tasks where multiple
    eligible tools can be tested safely." Not real agent work; purely to
    generate routing decisions and outcomes to train or evaluate from.
    """
    workflow_id = f"coldstart_{uuid.uuid4().hex[:8]}"
    repository.create_workflow(session, workflow_id, f"cold-start benchmark: {capability}")
    tasks = [
        Task(
            id=f"{workflow_id}_t{i}",
            workflow_id=workflow_id,
            description=f"benchmark {capability} call {i}",
            input={"capability": capability, "args": {"query": f"query-{i}"}},
        )
        for i in range(n_tasks)
    ]
    repository.save_tasks(session, tasks)
    return workflow_id


def run_cold_start_benchmark(
    session: Session,
    n_tasks: int,
    *,
    capability: str = "lookup",
    explore_probability: float = DEFAULT_EXPLORE_PROBABILITY,
    rng: random.Random | None = None,
) -> str:
    """Generates and runs one cold-start workflow, returns its workflow id.
    Exploration is on by default here (unlike ordinary routing) specifically
    so both winning and losing candidates accumulate real outcome data
    instead of only ever observing the heuristic's own favorite -- otherwise
    a model trained on this data would never see what happens when the
    "wrong" tool gets picked (Section 15D.2's selection-bias concern).
    Call this repeatedly (fresh workflow each time) to build up a dataset.
    """
    workflow_id = generate_cold_start_workflow(session, n_tasks, capability=capability)
    run_workflow(session, workflow_id, explore_probability=explore_probability, rng=rng)
    return workflow_id
