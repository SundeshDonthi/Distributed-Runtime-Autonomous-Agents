"""Drops and recreates every table from the current ORM metadata against
DATABASE_URL. There's no real migration tooling yet (`Base.metadata.
create_all` only adds missing tables, not missing columns on existing ones --
see the README's "known scope boundary" under Phase 4), so a model change
needs this instead of a proper migration. Only ever run this against the
local dev database; it destroys all data in the target.

Usage: python -m scripts.reset_dev_db
"""
from __future__ import annotations

from runtime.state.db import DATABASE_URL, engine
from runtime.state.orm import Base


def main() -> None:
    answer = input(f"This will DROP ALL TABLES at {DATABASE_URL}. Type 'yes' to continue: ")
    if answer.strip().lower() != "yes":
        print("Aborted.")
        return
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("Schema reset.")


if __name__ == "__main__":
    main()
