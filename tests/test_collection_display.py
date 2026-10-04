"""Display-only frozen quote projection does not leak signing material."""
import json
from copy import deepcopy
from pathlib import Path
import subprocess
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
    assert rows[0]["budget_id"] == "b"
    assert rows[0]["direction"] == "driver_to_operator"
    assert rows[0]["transaction_id"] == "tx"
    assert rows[0]["state"] == "ready"
    assert "quote" not in rows[0] and "signature" not in rows[0]


@pytest.mark.parametrize("weekly_child", [False, True])
@pytest.mark.parametrize("quote_kind", ["valid", "missing", "mismatched"])
def test_summary_id_comes_from_saved_collection_not_quote(weekly_child, quote_kind):
    data = item(budget_id="untrusted-quote" if quote_kind == "mismatched" else "b")
    if quote_kind == "missing":
        data = {}
    data["state"] = "broadcast_unknown"
    budget = {"terms": {"session_id": "parent" if weekly_child else "s",
                        "transaction_id": "parent-tx" if weekly_child else "tx"}}
    if weekly_child:
        budget["binding"] = {"session_id": "s", "transaction_id": "tx"}
    api = SimpleNamespace(
        saved={"driver_collections": {"b": data}, "session_budgets": {"b": budget}},
        collections=SimpleNamespace(
            session_id=lambda row: (row.get("binding") or row["terms"])["session_id"]),
    )
    before = deepcopy(api.saved)
    row = MainnetWalletAPI.payment_summary(api)[0]
    assert (row["budget_id"], row["session_id"], row["transaction_id"]) == ("b", "s", "tx")
    assert ("amount_sats" in row) == (quote_kind == "valid")
    assert not {"quote", "payload", "signature", "driver_identity", "recipient_address"} & row.keys()
    assert api.saved == before


@pytest.mark.parametrize("state,expires,actions,title", [
    ("ready", "2026-10-03T00:00:00Z", ["approval", "waiver"], "Approval expired"),
    ("broadcast_unknown", "2026-10-11T00:00:00Z",
     ["check", "recovery"], "Payment outcome uncertain"),
])
def test_real_backend_summary_routes_owner_actions(state, expires, actions, title):
    """Pass actual Python projection into JS; do not hand-add missing fields."""
    data = item(expires_at=expires) | {"state": state}
    api = SimpleNamespace(
        saved={"driver_collections": {"b": data},
               "session_budgets": {"b": {
                   "terms": {"session_id": "weekly-parent", "transaction_id": "parent"},
                   "binding": {"session_id": "s", "transaction_id": "tx"}}}},
        collections=SimpleNamespace(session_id=lambda row: row["binding"]["session_id"]),
    )
    health = {"session_payments": MainnetWalletAPI.payment_summary(api),
              "driver_approvals": [{"session_id": "s", "budget_id": "newer-budget",
                                    "state": "awaiting_driver_consent",
                                    "expires_at": "2026-10-12T00:00:00Z"}]}
    module = Path(__file__).resolve().parents[1] / "frontend" / "owner-actions.js"
    code = f"""
        import {{ownerActionRows}} from {json.dumps(module.as_uri())};
        let input = ''; for await (const c of process.stdin) input += c;
        const [v] = ownerActionRows(JSON.parse(input), [], Date.parse('2026-10-05T00:00:00Z'));
        console.log(JSON.stringify({{budgetId:v.budgetId, title:v.title,
            approvalId:v.approval?.budget_id || null, actions:v.actions.map(a=>a.id)}}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", code], input=json.dumps(health),
        text=True, capture_output=True, check=True, timeout=15,
    )
    assert json.loads(result.stdout) == {
        "budgetId": "b", "title": title, "approvalId": None, "actions": actions}
