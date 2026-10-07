"""Unified recovery never guesses a target or converts uncertainty to a retry."""
import copy
import json
import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.settlement_recovery import execute, prepare, CONFIRMATIONS
from custom_components.bsv_settlement.session_review import now
from test_adjustment_renewal import expired
from test_collection_recovery import reserved
from test_collection import authorised
from test_budget import no_network

pytestmark = pytest.mark.asyncio


def approval(plan):
    return {"record_type": plan["record_type"], "record_id": plan["record_id"],
            "expected_recovery_hash": plan["expected_recovery_hash"],
            "evidence_reference": "fictional wallet and recipient checks",
            **dict.fromkeys(CONFIRMATIONS, True)}


async def test_unified_adjustment_plan_is_read_only_and_executes_same_record(tmp_path, monkeypatch):
    _, api, _, _, review = await expired(tmp_path, monkeypatch)
    data = {"record_type": "review", "record_id": review["review_id"]}
    before = copy.deepcopy(api.saved)
    plan = await execute(api, "prepare_settlement_recovery", data, "admin")
    assert api.saved == before and plan["executable"]
    assert plan["recovery_action"] == "renew_never_started_adjustment"
    result = await execute(api, "execute_settlement_recovery", approval(plan), "admin")
    assert result["review_id"] == review["review_id"]
    assert len(api.saved["session_reviews"]) == len(before["session_reviews"])
    assert not api.chain.posts
    with pytest.raises(WalletError):
        await execute(api, "execute_settlement_recovery", approval(plan), "admin")


async def test_reserved_budget_uses_existing_guarded_recovery(tmp_path):
    _, api, _, row, _, _ = await reserved(tmp_path)
    data = {"record_type": "budget", "record_id": row["terms"]["budget_id"]}
    before = copy.deepcopy(api.saved)
    plan = prepare(api, data)
    assert api.saved == before and plan["recovery_action"] == "release_prepermit_attempt"
    old_expiry = json.loads(api.collections.get(row)["quote"]["payload"])["expires_at"]
    await execute(api, "execute_settlement_recovery", approval(plan), "admin")
    item = api.collections.get(row)
    assert item["state"] == "recovery_ready"
    assert json.loads(item["quote"]["payload"])["expires_at"] == old_expiry
    assert not api.chain.posts


async def test_signing_permit_is_not_released(tmp_path):
    _, api, _, row, _, _, _ = await authorised(tmp_path)
    plan = prepare(api, {"record_type": "budget", "record_id": row["terms"]["budget_id"]})
    assert not plan["executable"] and plan["recovery_action"] == "inspect_authorised_wallet_attempt"
    with pytest.raises(WalletError):
        await execute(api, "execute_settlement_recovery", approval(plan), "admin")


async def test_expired_budget_is_not_extended_by_generic_recovery(tmp_path):
    _, api, _, row, _, _ = await reserved(tmp_path)
    row["terms"]["expires_at"] = (now() - timedelta(seconds=1)).isoformat()
    before = copy.deepcopy(api.saved)
    plan = prepare(api, {"record_type": "budget", "record_id": row["terms"]["budget_id"]})
    assert not plan["executable"]
    assert api.saved == before


async def test_coordinator_preparation_does_not_refresh_or_project_health(tmp_path, monkeypatch):
    from custom_components.bsv_settlement.coordinator import SettlementCoordinator
    _, api, _, _, review = await expired(tmp_path, monkeypatch)
    coordinator = object.__new__(SettlementCoordinator)
    coordinator.api, coordinator.mode, coordinator.lock = api, "embedded_mainnet", asyncio.Lock()
    monkeypatch.setattr(api, "status", Mock(side_effect=AssertionError("must not project health")))
    plan = await coordinator.execute("prepare_settlement_recovery", {
        "record_type": "review", "record_id": review["review_id"]}, "admin")
    assert plan["read_only"] and plan["executable"]


@pytest.mark.parametrize("state,confirmations,accepted,expected", [
    ("broadcast_unknown", None, False, "reconcile_existing_transaction"),
    ("provider_unconfirmed", 0, False, "await_existing_confirmation"),
    ("provider_unconfirmed", 0, True, "await_existing_confirmation"),
    ("provider_confirmed", 1, False, "sync_original_receipt"),
    ("provider_confirmed", 1, True, "none"),
    ("provider_confirmed", None, True, "reconcile_existing_transaction"),
])
async def test_credit_lifecycle_has_no_payment_retry(tmp_path, monkeypatch, state, confirmations, accepted, expected):
    _, api, _, _, _ = await expired(tmp_path, monkeypatch)
    api.saved["payments"]["test-draft"] = {
        "state": state, "txid": "ab" * 32, "confirmations": confirmations,
        "amount_sats": 52, "signed_raw": "DO_NOT_EXPOSE",
        **({"wallet_receipt_ack": {"reported_at": "fixture"}} if accepted else {}),
    }
    before = copy.deepcopy(api.saved)
    plan = prepare(api, {"record_type": "payment", "record_id": "test-draft"})
    assert plan["recovery_action"] == expected and not plan["executable"]
    assert "DO_NOT_EXPOSE" not in json.dumps(plan)
    with pytest.raises(WalletError):
        await execute(api, "execute_settlement_recovery", approval(plan), "admin")
    assert api.saved == before and not api.chain.posts


async def test_manual_confirmed_review_is_recognised_without_reset(tmp_path, monkeypatch):
    _, api, _, _, review = await expired(tmp_path, monkeypatch)
    review.update(state="driver_payment_provider_confirmed",
                  receipt={"txid": "ab" * 32, "confirmations": 1})
    plan = prepare(api, {"record_type": "review", "record_id": review["review_id"]})
    assert plan["recovery_action"] == "none" and not plan["executable"]


async def test_stale_plan_missing_approval_and_unknown_id_fail_closed(tmp_path, monkeypatch):
    _, api, _, _, review = await expired(tmp_path, monkeypatch)
    data = {"record_type": "review", "record_id": review["review_id"]}
    plan = prepare(api, data)
    for change in ({"expected_recovery_hash": "old"}, {"confirm_recovery": False}):
        with pytest.raises(WalletError):
            await execute(api, "execute_settlement_recovery", approval(plan) | change, "admin")
    for change in ({"record_id": "unknown"}, {"record_type": "session_latest"}):
        with pytest.raises(WalletError):
            prepare(api, data | change)
    with pytest.raises(WalletError):
        await execute(api, "prepare_settlement_recovery", data, None)
    assert not api.chain.posts


async def test_unified_services_are_registered_and_admin_only(tmp_path, monkeypatch):
    hass, api, _, _, review = await expired(tmp_path, monkeypatch)
    await async_setup(hass, {})
    data = {"record_type": "review", "record_id": review["review_id"]}
    plan = prepare(api, data)
    for action in ("prepare_settlement_recovery", "execute_settlement_recovery"):
        assert hass.services.has_service("bsv_settlement", action)
        args = data if action == "prepare_settlement_recovery" else approval(plan)
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call("bsv_settlement", action,
                args | {"config_entry_id": api.entry.entry_id}, blocking=True, context=Context())
