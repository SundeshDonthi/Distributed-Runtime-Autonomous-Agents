"""Generates a cold-start routing dataset and exports it as JSON Lines with
reproducible train/val/test splits (Section 15J Phase 6: "feature snapshots
and training exports, held-out splits... data can be reproduced and audited
without target leakage") -- the actual file a Phase 7 training script would
read, as opposed to `generate_routing_dataset.py`, which just reports summary
stats from the live tables.

Run:
    python -m benchmarks.export_training_dataset
"""
from __future__ import annotations

import json
import random
from collections import Counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from runtime.state.orm import Base
from runtime.training.datasets import (
    export_training_rows,
    run_cold_start_benchmark,
    selected_candidate_features,
)

N_WORKFLOWS = 30
TASKS_PER_WORKFLOW = 5
OUTPUT_PATH = "training_dataset.jsonl"
SEED = 0


def main() -> None:
    engine = create_engine("sqlite:///./routing_dataset.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    rng = random.Random(SEED)

    for _ in range(N_WORKFLOWS):
        run_cold_start_benchmark(session, TASKS_PER_WORKFLOW, rng=rng)
    session.commit()

    rows = export_training_rows(session, seed=SEED)
    session.close()

    with open(OUTPUT_PATH, "w") as f:
        for row in rows:
            f.write(
                json.dumps(
                    {
                        "decision_id": row["decision_id"],
                        "workflow_id": row["workflow_id"],
                        "capability": row["capability"],
                        "selected_tool": row["selected_tool"],
                        "was_exploration": row["was_exploration"],
                        "features": selected_candidate_features(row),
                        "label_success": row["success"],
                        "latency_ms": row["latency_ms"],
                        "split": row["split"],
                    },
                    default=str,
                )
                + "\n"
            )

    split_counts = Counter(row["split"] for row in rows)
    workflows_per_split = {
        split: len({r["workflow_id"] for r in rows if r["split"] == split}) for split in split_counts
    }

    print(f"Wrote {len(rows)} labeled rows to {OUTPUT_PATH}")
    print()
    print(f"{'split':6s} {'rows':>6s} {'workflows':>10s}")
    for split, count in split_counts.items():
        print(f"{split:6s} {count:6d} {workflows_per_split[split]:10d}")


if __name__ == "__main__":
    main()
