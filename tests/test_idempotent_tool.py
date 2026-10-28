from __future__ import annotations

from runtime.state import repository
from runtime.tools.registry import get_tool


def test_idempotent_charge_suppresses_a_duplicate_call(db_session):
    charge = get_tool("idempotent_charge")

    first = charge({"idempotency_key": "order-1", "amount": 10.0})
    second = charge({"idempotency_key": "order-1", "amount": 10.0})

    assert first == {"charged": 10.0, "duplicate_suppressed": False}
    assert second == {"charged": 10.0, "duplicate_suppressed": True}
    assert repository.count_external_charges(db_session) == 1


def test_idempotent_charge_treats_different_keys_independently(db_session):
    charge = get_tool("idempotent_charge")

    charge({"idempotency_key": "order-1", "amount": 10.0})
    charge({"idempotency_key": "order-2", "amount": 25.0})

    assert repository.count_external_charges(db_session) == 2


def test_naive_charge_has_no_duplicate_protection(db_session):
    # The honest counter-example (Section 15K: "exactly-once behavior is
    # limited by external tool semantics"): calling this twice for what a
    # caller intends as "the same" logical operation charges twice, because
    # the tool itself tracks nothing. This is the real limitation an
    # idempotency key on OUR side can't fix by itself -- only the external
    # system's own cooperation (idempotent_charge, above) can.
    charge = get_tool("naive_charge")

    charge({"amount": 10.0})
    charge({"amount": 10.0})

    assert repository.count_external_charges(db_session) == 2
