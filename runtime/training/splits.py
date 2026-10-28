from __future__ import annotations

import hashlib
from typing import Literal

Split = Literal["train", "val", "test"]


def assign_split(
    workflow_id: str, seed: int = 0, train_frac: float = 0.7, val_frac: float = 0.15
) -> Split:
    """Deterministic, reproducible split assignment by *workflow*, not by
    individual decision/task (Section 15D.2: "partition train/validation/
    test by workflow or task family, not individual attempts from the same
    workflow" -- otherwise two decisions from the same workflow could leak
    across splits via whatever correlation that workflow's objective or
    conditions introduce).

    Pure hash-based rather than a persisted assignment table: the same
    `(workflow_id, seed, train_frac, val_frac)` always produces the same
    split, so a dataset export is reproducible and auditable (Section 15J's
    "done when data can be reproduced and audited") without needing to store
    or migrate split assignments anywhere.
    """
    if not 0.0 <= train_frac <= 1.0 or not 0.0 <= val_frac <= 1.0 or train_frac + val_frac > 1.0:
        raise ValueError("train_frac and val_frac must be in [0, 1] and sum to at most 1")

    digest = hashlib.sha256(f"{seed}:{workflow_id}".encode()).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF
    if fraction < train_frac:
        return "train"
    if fraction < train_frac + val_frac:
        return "val"
    return "test"
