"""Expired never-started adjustment renewal. Fictional wallets only."""
import copy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.bsv_settlement import async_setup, adjustment_renewal, session_review
from custom_components.bsv_settlement.adjustment_collection import AdjustmentCollections
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from test_adjustment_collection import ready
from test_budget import no_network

pytestmark = pytest.mark.asyncio


async def expired(tmp_path, monkeypatch):
    hass, api, registration, driver, data, public = await ready(tmp_path)
    clock = session_review.now() + timedelta(minutes=11)
    monkeypatch.setattr(adjustment_renewal, "now", lambda: clock)
    monkeypatch.setattr(session_review, "now", lambda: clock)
    review = api.reviews.get(public["review_id"])
    return hass, api, registration, driver, review


def approved(review):
    return {"review_id": review["review_id"], "expected_review_hash": session_review.digest(review),
            "evidence_reference": "fixture wallet and receiving history checked",
            **dict.fromkeys(adjustment_renewal.CONFIRMATIONS, True)}


async def test_read_only_review_and_same_debt_renewal_requires_fresh_claim(tmp_path, monkeypatch):
    hass, api, _, driver, review = await expired(tmp_path, monkeypatch)
    before = copy.deepcopy(api.saved)
    result = await api.reviews.execute("prepare_adjustment_renewal", {"review_id": review["review_id"]}, "admin")
    assert result["eligible"] and result["requires_fresh_driver_claim"]
    assert not result["payment_sent"] and api.saved == before
    old_terms = copy.deepcopy(review["frozen_terms"])
    renewed = await api.reviews.execute("renew_expired_adjustment", approved(review), "admin")
    assert renewed["state"] == "awaiting_driver_payment"
    assert renewed["review_id"] == review["review_id"]
    assert review["terms_hash"] != session_review.digest(old_terms)
    assert {k:v for k,v in review["frozen_terms"].items() if k != "expires_at"} == {
        k:v for k,v in old_terms.items() if k != "expires_at"}
    assert review["adjustment_renewals"][0]["previous_frozen_terms"] == old_terms
    assert "adjustment_renewals" not in renewed
    for key in ("session_budgets", "energy_adjustment_requests", "payments"):
        assert api.saved[key] == before[key]
    assert not api.chain.posts and review.get("wallet_collection") is None
    worker = AdjustmentCollections(api)
    row = worker.row(review)
    quote = await worker.status(row)
    assert quote["state"] == "ready"
    with pytest.raises(WalletError):
        await worker.claim(row, {})  # Admin renewal does not grant driver consent.
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    assert restored.reviews.get(review["review_id"])["adjustment_renewals"] == review["adjustment_renewals"]


@pytest.mark.parametrize("state", [
    "ready", "wallet_attempt_reserved", "submission_authorised",
    "broadcast_unknown", "provider_unconfirmed", "provider_confirmed",
])
async def test_every_existing_collection_record_blocks_renewal(tmp_path, monkeypatch, state):
    _, api, _, _, review = await expired(tmp_path, monkeypatch)
    review["wallet_collection"] = {"state": state}
    before = copy.deepcopy(api.saved)
    with pytest.raises(WalletError, match="reconciliation"):
        await api.reviews.execute("renew_expired_adjustment", approved(review), "admin")
    assert api.saved == before and not api.chain.posts


@pytest.mark.parametrize("field", ["receipt", "credit_draft_id", "driver_payment_raw", "txid"])
async def test_payment_artifacts_are_never_cleared(tmp_path, monkeypatch, field):
    _, api, _, _, review = await expired(tmp_path, monkeypatch)
    review[field] = "existing"
    with pytest.raises(WalletError, match="reconciliation"):
        await api.reviews.execute("prepare_adjustment_renewal", {"review_id": review["review_id"]}, "admin")


@pytest.mark.parametrize("field", adjustment_renewal.CONFIRMATIONS)
async def test_requires_every_evidence_attestation(tmp_path, monkeypatch, field):
    _, api, _, _, review = await expired(tmp_path, monkeypatch)
    args = approved(review)
    args.pop(field)
    before = copy.deepcopy(review)
    with pytest.raises(WalletError, match="Check wallet"):
        await api.reviews.execute("renew_expired_adjustment", args, "admin")
    assert review == before


async def test_stale_hash_repeat_and_save_failure_do_not_renew(tmp_path, monkeypatch):
    _, api, _, _, review = await expired(tmp_path, monkeypatch)
    args = approved(review)
    with pytest.raises(WalletError, match="changed"):
        await api.reviews.execute("renew_expired_adjustment", args | {"expected_review_hash": "old"}, "admin")
    before = copy.deepcopy(review)
    save = api.reviews.save
    monkeypatch.setattr(api.reviews, "save", AsyncMock(side_effect=OSError("disk")))
    with pytest.raises(OSError):
        await api.reviews.execute("renew_expired_adjustment", args, "admin")
    assert review == before
    monkeypatch.setattr(api.reviews, "save", save)
    await api.reviews.execute("renew_expired_adjustment", args, "admin")
    after = copy.deepcopy(review)
    with pytest.raises(WalletError, match="expired"):
        await api.reviews.execute("renew_expired_adjustment", args, "admin")
    assert review == after and not api.chain.posts


@pytest.mark.parametrize("change", ["manual", "amount", "request", "revoked"])
async def test_legacy_changed_terms_and_changed_registration_rejected(tmp_path, monkeypatch, change):
    _, api, registration, _, review = await expired(tmp_path, monkeypatch)
    if change == "manual":
        review["payment_request"]["format"] = "manual_bsv_payment_request_v1"
    elif change == "amount":
        review["amount_sats"] += 1
    elif change == "request":
        review["payment_request"]["amount_sats"] += 1
    else:
        registration["state"] = "revoked"
    with pytest.raises(WalletError):
        await api.reviews.execute("renew_expired_adjustment", approved(review), "admin")
    assert not api.chain.posts


async def test_services_registered_and_reject_context_free_calls(tmp_path, monkeypatch):
    hass, api, _, _, review = await expired(tmp_path, monkeypatch)
    await async_setup(hass, {})
    for action in ("prepare_adjustment_renewal", "renew_expired_adjustment"):
        assert hass.services.has_service("bsv_settlement", action)
        data = approved(review) if action == "renew_expired_adjustment" else {"review_id": review["review_id"]}
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call("bsv_settlement", action,
                data | {"config_entry_id": api.entry.entry_id}, blocking=True, context=Context())
