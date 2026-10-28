"""Generates an initial labeled routing dataset (Section 15C.3's "cold
start"): runs several controlled benchmark workflows of capability-routed
`lookup` tasks with exploration enabled, then reports what landed in
`routing_decisions`/`routing_outcomes` -- the data a future neural scorer
(Section 15C) would train on.

Run:
    python -m benchmarks.generate_routing_dataset
"""
from __future__ import annotations

import random
from collections import Counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from runtime.state import repository
from runtime.state.orm import Base
from runtime.training.datasets import run_cold_start_benchmark

N_WORKFLOWS = 20
TASKS_PER_WORKFLOW = 5


def main() -> None:
    engine = create_engine("sqlite:///./routing_dataset.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    rng = random.Random(0)

    for _ in range(N_WORKFLOWS):
        run_cold_start_benchmark(session, TASKS_PER_WORKFLOW, rng=rng)
    session.commit()

    dataset = repository.get_routing_dataset(session)
    session.close()

    by_tool = Counter(row["selected_tool"] for row in dataset)
    successes_by_tool = Counter(row["selected_tool"] for row in dataset if row["success"])
    exploration_rows = sum(1 for row in dataset if row["was_exploration"])

    print(f"Total routing decisions with an outcome: {len(dataset)}")
    print(f"Exploratory decisions: {exploration_rows} ({exploration_rows / len(dataset):.0%})")
    print()
    print(f"{'tool':16s} {'calls':>6s} {'successes':>10s} {'success rate':>13s}")
    for tool, calls in by_tool.items():
        successes = successes_by_tool[tool]
        print(f"{tool:16s} {calls:6d} {successes:10d} {successes / calls:12.0%}")


if __name__ == "__main__":
    main()
