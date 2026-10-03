"""Display-only frozen quote projection does not leak signing material."""
import json
from types import SimpleNamespace

import pytest

from custom_components.bsv_settlement.mainnet import MainnetWalletAPI, collection_display_terms


def item(**changes):
    q = {"budget_id": "b", "account": {"session_id": "s"}, "amount_sats": 19,
         "max_fee_sats": 10, "satoshis_per_aud": "100.0", "expires_at": "expiry",
         "driver_identity": "private-display-field", "recipient_address": "omit"}
    q.update(changes)
    return {"quote": {"payload": json.dumps(q), "signature": "do-not-expose"}}


def test_display_terms_only_whitelist_and_unchanged_quote():
    data = item()
    before = json.dumps(data)
    assert collection_display_terms(data, "b", "s") == {
        "amount_sats": 19, "max_fee_sats": 10,
        "satoshis_per_aud": "100.0", "expires_at": "expiry"}
    assert json.dumps(data) == before


@pytest.mark.parametrize("change", [
    {"budget_id": "other"}, {"account": {"session_id": "other"}},
    {"amount_sats": None}, {"amount_sats": True}, {"amount_sats": "19"},
    {"amount_sats": -1}, {"amount_sats": 0},
])
def test_invalid_or_mismatched_quote_withheld(change):
    assert collection_display_terms(item(**change), "b", "s") == {}


@pytest.mark.parametrize("value", [{}, {"quote": None}, {"quote": {"payload": "bad"}}])
def test_missing_or_malformed_quote_withheld(value):
    assert collection_display_terms(value, "b", "s") == {}


def test_payment_summary_exposes_amount_for_the_matching_driver_collection():
    data = item() | {"state": "ready"}
    api = SimpleNamespace(
        saved={"driver_collections": {"b": data},
               "session_budgets": {"b": {"terms": {"transaction_id": "tx"}}}},
        collections=SimpleNamespace(session_id=lambda row: "s"),
    )
    rows = MainnetWalletAPI.payment_summary(api)
    assert rows[0]["amount_sats"] == 19
    assert rows[0]["direction"] == "driver_to_operator"
    assert rows[0]["transaction_id"] == "tx"
    assert rows[0]["state"] == "ready"
    assert "quote" not in rows[0] and "signature" not in rows[0]
