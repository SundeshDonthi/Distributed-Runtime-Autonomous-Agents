from __future__ import annotations

import random
import uuid

from sqlalchemy.orm import Session

from runtime.models.task import Task
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.training.splits import assign_split

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


def export_training_rows(
    session: Session,
    seed: int = 0,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    workflow_id: str | None = None,
) -> list[dict]:
    """Section 15D's actual training export: every labeled routing decision
    (Section 15D's dataset), each tagged with a reproducible train/val/test
    split assigned by *workflow* (Section 15D.2), so a later training run
    doesn't need to reimplement splitting or risk leaking a workflow across
    splits. Each row carries the full candidate set with its feature
    snapshot and the outcome for whichever candidate was actually selected.
    """
    rows = repository.get_routing_dataset(session, workflow_id=workflow_id)
    for row in rows:
        row["split"] = assign_split(row["workflow_id"], seed, train_frac, val_frac)
    return rows


def selected_candidate_features(row: dict) -> dict | None:
    """The feature snapshot for whichever candidate a dataset row's decision
    actually selected -- the (features, label=row["success"]) pair Section
    15C.2's "binary success predictor" trains on. None if the selected tool
    is somehow missing from the logged candidate set (shouldn't happen, but
    an explicit None beats an IndexError two phases from now).
    """
    for candidate in row["candidates"]:
        if candidate["tool_name"] == row["selected_tool"]:
            return candidate["features"]
    return None
