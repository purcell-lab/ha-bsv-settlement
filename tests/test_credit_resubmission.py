"""Operator-approved resubmission of a stuck credit's identical signed bytes."""
import copy

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.credit_resubmission import MAX_RESUBMISSIONS, inspect, resubmit
from custom_components.bsv_settlement.sensor import WALLET_SUMMARY_KEYS
from test_auto_credit import ready
from test_budget import no_network  # noqa: F401  Autouse: no live provider sessions.

pytestmark = pytest.mark.asyncio


async def stuck(tmp_path):
    """A credit whose submission timed out and that the provider cannot show."""
    hass, api, _, row, _, _ = await ready(tmp_path)
    api.chain.fail = True
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "broadcast_unknown"
    api.chain.fail = False
    known, original = set(), api.chain.details

    async def details(txid):
        if txid == item["txid"] and txid not in known:
            raise WalletError("Chain provider HTTP 404; payment state must be reconciled")
        return await original(txid)
    api.chain.details = details
    api.chain.known = known
    # The signed transaction reserved this outpoint; the provider still lists it unspent.
    api.chain.rows = [{"tx_hash": item["source_txid"], "tx_pos": item["source_index"], "value": 50000,
                       "height": 1, "isSpentInMempoolTx": False}]
    return hass, api, row, item


def request(review, **extra):
    return {"credit_id": review["credit_id"], "expected_txid": review["txid"],
            "expected_review_hash": review["review_hash"],
            "confirm_resubmit_identical_signed_bytes": True} | extra


async def test_inspection_is_read_only_and_resubmission_sends_identical_bytes(tmp_path):
    hass, api, row, item = await stuck(tmp_path)
    credit_id, posts, before = item["budget_id"], len(api.chain.posts), copy.deepcopy(api.saved)
    review = await inspect(api, credit_id)
    assert review["resubmission_allowed"] is True
    assert review["provider_has_transaction"] is False and review["funding_unspent_and_confirmed"] is True
    assert review["funding_outpoint"] == {"txid": item["source_txid"], "index": item["source_index"],
                                          "value_sats": 50000}
    assert len(api.chain.posts) == posts and api.saved == before
    result = await resubmit(api, request(review), "admin")
    assert result["outcome"] == "provider_accepted" and result["state"] == "submitted"
    assert api.chain.posts[-1] == item["signed_raw"] and len(api.chain.posts) == posts + 1
    assert item["txid"] == before["automatic_credits"][credit_id]["txid"]  # Same transaction, never re-signed.
    assert [r["outcome"] for r in item["resubmissions"]] == ["provider_accepted"]
    api.chain.known.add(item["txid"])
    await api.auto_credits.tick()
    assert item["state"] == "provider_confirmed" and len(api.chain.posts) == posts + 1


async def test_refuses_without_confirmation_admin_or_matching_review(tmp_path):
    hass, api, row, item = await stuck(tmp_path)
    review = await inspect(api, item["budget_id"])
    for data, user, match in [
            (request(review), None, "administrator"),
            (request(review, confirm_resubmit_identical_signed_bytes=False), "admin", "administrator"),
            (request(review, expected_review_hash="0" * 64), "admin", "changed"),
            (request(review, expected_txid="0" * 64), "admin", "changed")]:
        with pytest.raises(WalletError, match=match):
            await resubmit(api, data, user)
    assert item["state"] == "broadcast_unknown" and "resubmissions" not in item


async def test_spent_funding_or_known_transaction_blocks_resubmission(tmp_path):
    hass, api, row, item = await stuck(tmp_path)
    review = await inspect(api, item["budget_id"])
    api.chain.rows = []  # The reserved outpoint is spent or no longer confirmed.
    blocked = await inspect(api, item["budget_id"])
    assert blocked["resubmission_allowed"] is False and "Do not resubmit" in blocked["next_step"]
    with pytest.raises(WalletError, match="changed"):  # Funding evidence is part of the review hash.
        await resubmit(api, request(review), "admin")
    with pytest.raises(WalletError, match="Do not resubmit"):
        await resubmit(api, request(blocked), "admin")
    api.chain.rows = [{"tx_hash": item["source_txid"], "tx_pos": item["source_index"], "value": 50000,
                       "height": 1, "isSpentInMempoolTx": False}]
    api.chain.known.add(item["txid"])
    known = await inspect(api, item["budget_id"])
    assert known["provider_has_transaction"] is True and known["resubmission_allowed"] is False
    with pytest.raises(WalletError, match="normal reconciliation"):
        await resubmit(api, request(known), "admin")
    assert "resubmissions" not in item


async def test_uncertain_outcomes_are_bounded_and_tampering_is_refused(tmp_path):
    hass, api, row, item = await stuck(tmp_path)
    api.chain.fail = True
    for _ in range(MAX_RESUBMISSIONS):
        result = await resubmit(api, request(await inspect(api, item["budget_id"])), "admin")
        assert result["outcome"] == "uncertain" and item["state"] == "broadcast_unknown"
    with pytest.raises(WalletError, match="limit"):
        await resubmit(api, request(await inspect(api, item["budget_id"])), "admin")
    item["recipient_address"] = PrivateKey(5).public_key().address()
    with pytest.raises(WalletError, match="do not match"):
        await inspect(api, item["budget_id"])


async def test_only_broadcast_unknown_credits_qualify(tmp_path):
    hass, api, _, row, _, _ = await ready(tmp_path)
    await api.auto_credits.tick()
    with pytest.raises(WalletError, match="broadcast_unknown"):
        await inspect(api, api.auto_credits.get(row)["budget_id"])
    with pytest.raises(WalletError, match="not found"):
        await inspect(api, "missing")


def test_status_sensor_records_the_inbox_delivery_policy():
    assert "messagebox_delivery" in WALLET_SUMMARY_KEYS
