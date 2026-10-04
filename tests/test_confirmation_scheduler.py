"""Offline scheduling budgets and manual/automatic confirmation parity."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bsv import P2PKH, Transaction, TransactionInput, TransactionOutput

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.confirmation import read_transaction
from custom_components.bsv_settlement.confirmation_scheduler import DriverConfirmationScheduler
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from test_budget import no_network
from test_collection import authorised
from test_mainnet import setup_wallet
from test_session_review import source, session, prepare_data, review_approval

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 4, 4, 30, tzinfo=timezone.utc)


def scheduler_fixture(monkeypatch, count=5):
    clock = [NOW]
    monkeypatch.setattr(
        "custom_components.bsv_settlement.confirmation_scheduler.utcnow", lambda: clock[0])
    saved = {"driver_collections": {}, "session_budgets": {}, "session_reviews": {}}
    api = SimpleNamespace(saved=saved, store=SimpleNamespace(blocked=False, async_save=AsyncMock()),
                          collections=SimpleNamespace(reconcile=AsyncMock()),
                          reviews=SimpleNamespace(reconcile_driver_payment=AsyncMock()))
    for i in range(count):
        key = str(i)
        saved["driver_collections"][key] = {
            "txid": "a" * 64, "signed_raw": "fictional", "state": "provider_unconfirmed"}
        saved["session_budgets"][key] = {"terms": {"budget_id": key}, "state": "revoked"}
        saved["session_reviews"][key] = {
            "review_id": key, "direction": "driver_to_operator",
            "state": "driver_payment_provider_unconfirmed", "receipt": {"txid": "b" * 64}}
    return api, DriverConfirmationScheduler(api), clock


@pytest.mark.parametrize("count", [5, 51])
async def test_budget_fairness_and_cursor_survive_restart(monkeypatch, count):
    api, worker, clock = scheduler_fixture(monkeypatch, count=count)
    visited = []
    async def automatic(row):
        visited.append("collection:" + row["terms"]["budget_id"])
    async def manual(rid):
        visited.append("manual:" + rid)
    api.collections.reconcile.side_effect = automatic
    api.reviews.reconcile_driver_payment.side_effect = manual
    for _ in range(count):
        before = len(visited)
        await worker.tick()
        assert len(visited) - before == 2
        # Even continuously due/outage records cannot starve the other path.
        worker = DriverConfirmationScheduler(api)
        await worker.tick()
        assert len(visited) - before == 2
        clock[0] += timedelta(seconds=15)
    assert len(set(visited)) == 2 * count
    assert api.store.async_save.await_count == count


@pytest.mark.parametrize("stamp", [None, "", "invalid", 1, "2026-10-04T04:30:00",
                                      "2099-01-01T00:00:00+00:00"])
async def test_bad_or_future_timestamp_is_due(monkeypatch, stamp):
    api, worker, _ = scheduler_fixture(monkeypatch, count=1)
    api.saved["driver_collections"]["0"]["checked_at"] = stamp
    api.saved["session_reviews"]["0"]["receipt"]["checked_at"] = stamp
    await worker.tick()
    api.collections.reconcile.assert_awaited_once()
    api.reviews.reconcile_driver_payment.assert_awaited_once()


@pytest.mark.parametrize("confirmed,seconds,due", [
    (False, 59, False), (False, 60, True), (True, 299, False), (True, 300, True),
])
async def test_pending_and_confirmed_cooldowns(monkeypatch, confirmed, seconds, due):
    api, worker, _ = scheduler_fixture(monkeypatch, count=1)
    item = api.saved["driver_collections"]["0"]
    review = api.saved["session_reviews"]["0"]
    if confirmed:
        item["state"] = "provider_confirmed"
        review["state"] = "driver_payment_provider_confirmed"
    stamp = (NOW - timedelta(seconds=seconds)).isoformat()
    item["checked_at"] = review["receipt"]["checked_at"] = stamp
    await worker.tick()
    assert api.collections.reconcile.await_count == int(due)
    assert api.reviews.reconcile_driver_payment.await_count == int(due)


@pytest.mark.parametrize("state", [
    "ready", "wallet_attempt_reserved", "submission_authorised", "recovery_ready",
    "waived", "collection_blocked",
])
async def test_unsigned_held_or_waived_collection_not_advanced(monkeypatch, state):
    api, worker, _ = scheduler_fixture(monkeypatch, count=1)
    api.saved["session_reviews"].clear()
    api.saved["driver_collections"]["0"]["state"] = state
    await worker.tick()
    api.collections.reconcile.assert_not_awaited()


async def test_manual_without_receipt_and_missing_budget_are_not_discovered(monkeypatch):
    api, worker, _ = scheduler_fixture(monkeypatch, count=1)
    api.saved["session_budgets"].clear()
    api.saved["session_reviews"]["0"]["receipt"] = None
    await worker.tick()
    api.store.async_save.assert_not_awaited()
    api.reviews.reconcile_driver_payment.assert_not_awaited()


async def test_checkpoint_and_cursor_save_failure_prevent_network(monkeypatch):
    api, worker, _ = scheduler_fixture(monkeypatch)
    api.store.blocked = True
    with pytest.raises(WalletError, match="checkpoint"):
        await worker.tick()
    api.store.blocked = False
    api.store.async_save.side_effect = OSError("fictional disk failure")
    with pytest.raises(OSError):
        await worker.tick()
    api.collections.reconcile.assert_not_awaited()
    api.reviews.reconcile_driver_payment.assert_not_awaited()


async def test_bounded_reads_timeout_and_external_cancellation(monkeypatch):
    monkeypatch.setattr("custom_components.bsv_settlement.confirmation.READ_TIMEOUT_SECONDS", .01)
    async def stalled(*args, **kwargs):
        await asyncio.sleep(100)
    chain = SimpleNamespace(request=AsyncMock(side_effect=stalled), details=AsyncMock())
    with pytest.raises(WalletError, match="timed out"):
        await read_transaction(chain, "a" * 64)
    chain.details.assert_not_awaited()
    chain.request.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await read_transaction(chain, "a" * 64)


async def manual_receipt(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    source(hass, session())
    r = await api.reviews.prepare(prepare_data(), "admin")
    await api.reviews.approve(review_approval(r), "admin")
    tx = Transaction([TransactionInput(source_txid="22" * 32)],
                     [TransactionOutput(P2PKH().lock(r["recipient_address"]), satoshis=189)])
    api.chain.request = AsyncMock(return_value=tx.hex())
    await api.reviews.verify_driver_payment({
        "review_id": r["review_id"], "txid": tx.txid(), "output_index": 0,
        "confirm_driver_payment_reference": True}, "admin")
    return hass, entry, api, api.reviews.get(r["review_id"]), tx


@pytest.mark.parametrize("failure", [
    "outage", "missing_count", "null_count", "boolean_count", "negative_count",
    "wrong_id", "malformed_raw", "old_time", "bad_time", "nan_time", "overflow_time",
])
async def test_manual_confirmation_loss_recovery_and_immutable_ownership(tmp_path, failure):
    hass, entry, api, review, tx = await manual_receipt(tmp_path)
    try:
        original = copy.deepcopy(review)
        ownership = copy.deepcopy(api.saved["received_outpoints"])
        detail = {"txid": tx.txid(), "confirmations": 1}
        if failure == "outage":
            api.chain.request.side_effect = WalletError("fictional outage")
        elif failure == "malformed_raw":
            api.chain.request.return_value = "00"
        elif failure == "missing_count":
            detail.pop("confirmations")
        elif failure in ("null_count", "boolean_count", "negative_count"):
            detail["confirmations"] = {"null_count": None, "boolean_count": True, "negative_count": -1}[failure]
        elif failure == "wrong_id":
            detail["txid"] = "0" * 64
        else:
            detail["blocktime"] = {"old_time": 1, "bad_time": "invalid",
                                   "nan_time": float("nan"), "overflow_time": 10**400}[failure]
        api.chain.details = AsyncMock(return_value=detail)
        result = await api.reviews.reconcile_driver_payment(review["review_id"])
        assert result["state"] == "driver_payment_evidence_unavailable"
        assert result["receipt"]["confirmations"] is None
        assert result["receipt"]["verification_error"] is True
        assert api.saved["received_outpoints"] == ownership
        for key in ("frozen_terms", "terms_hash", "payment_request", "driver_payment_raw"):
            assert review[key] == original[key]
        assert "driver_payment_raw" not in result
        restored = MainnetWalletAPI(hass, entry)
        await restored.load()
        restored.chain = api.chain
        restored.chain.request = AsyncMock(return_value=tx.hex())
        restored.chain.details = AsyncMock(return_value={"txid": tx.txid(), "confirmations": 0})
        result = await restored.reviews.reconcile_driver_payment(review["review_id"])
        assert result["state"] == "driver_payment_provider_unconfirmed"
        restored.chain.details.return_value["confirmations"] = 2
        result = await restored.reviews.reconcile_driver_payment(review["review_id"])
        assert result["state"] == "driver_payment_provider_confirmed"
        assert not api.chain.posts and restored.saved["received_outpoints"] == ownership
    finally:
        await hass.async_stop(force=True)


async def test_manual_complete_unmined_metadata_and_legacy_raw_adoption(tmp_path):
    hass, _, api, review, tx = await manual_receipt(tmp_path)
    try:
        review.pop("driver_payment_raw")
        api.chain.details = AsyncMock(return_value={
            "txid": tx.txid(), "hash": tx.txid(), "version": tx.version,
            "locktime": tx.locktime, "size": len(tx.hex()) // 2,
            "vin": [{}], "vout": [{}]})
        result = await api.reviews.reconcile_driver_payment(review["review_id"])
        assert result["state"] == "driver_payment_provider_unconfirmed"
        assert review["driver_payment_raw"] == tx.hex()
        api.chain.details.return_value["blockhash"] = "a" * 64
        result = await api.reviews.reconcile_driver_payment(review["review_id"])
        assert result["state"] == "driver_payment_evidence_unavailable"
        assert not api.chain.posts
    finally:
        await hass.async_stop(force=True)


async def test_manual_conflicting_output_retains_owner_without_provider_read(tmp_path):
    hass, _, api, review, _ = await manual_receipt(tmp_path)
    try:
        point = review["receipt"]["outpoint"]
        api.saved["received_outpoints"][point] = "different-owner"
        api.chain.request.reset_mock()
        result = await api.reviews.reconcile_driver_payment(review["review_id"])
        assert result["state"] == "driver_payment_evidence_unavailable"
        assert api.saved["received_outpoints"][point] == "different-owner"
        api.chain.request.assert_not_awaited()
    finally:
        await hass.async_stop(force=True)


@pytest.mark.parametrize("field,value", [
    ("created_at", "broken"), ("expires_at", "broken"),
    ("created_at", "2026-10-04T04:30:00"), ("expires_at", None),
])
async def test_manual_bad_record_timestamp_downgrades_without_network(tmp_path, field, value):
    hass, _, api, review, _ = await manual_receipt(tmp_path)
    try:
        review[field] = value
        api.chain.request.reset_mock()
        result = await api.reviews.reconcile_driver_payment(review["review_id"])
        assert result["state"] == "driver_payment_evidence_unavailable"
        assert result["receipt"]["confirmations"] is None
        api.chain.request.assert_not_awaited()
    finally:
        await hass.async_stop(force=True)


async def test_coordinator_runs_checks_under_lock(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        coord = SettlementCoordinator(hass, entry, api)
        await coord.load()
        async def check():
            assert coord.lock.locked()
        api.driver_confirmations.tick = AsyncMock(side_effect=check)
        await coord._async_update_data()
        api.driver_confirmations.tick.assert_awaited_once()
        assert not api.chain.posts
    finally:
        await hass.async_stop(force=True)


async def test_background_rechecks_revoked_signed_collection_without_resend(tmp_path):
    hass, api, _, row, _, args, tx = await authorised(tmp_path)
    try:
        await api.collections.report(row, args | {"raw_tx": tx.hex()})
        item = api.collections.get(row)
        row["state"] = "revoked"
        item["checked_at"] = "bad timestamp"
        before = copy.deepcopy(item)
        ownership = copy.deepcopy(api.saved["received_outpoints"])
        api.chain.fail = True
        await api.driver_confirmations.tick()
        assert item["state"] == "broadcast_unknown" and item["confirmations"] is None
        api.saved["driver_confirmation_schedule"]["last_run"] = None
        item["checked_at"] = None
        api.chain.fail = False
        await api.driver_confirmations.tick()
        assert item["state"] == "provider_confirmed"
        for key in ("txid", "signed_raw", "quote", "attempt_token_hash", "draft_hash"):
            assert item[key] == before[key]
        assert len(api.chain.posts) == 1
        assert api.saved["received_outpoints"] == ownership
    finally:
        await hass.async_stop(force=True)
