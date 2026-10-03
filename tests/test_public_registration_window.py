"""Explicit operator enrolment windows preserve historical settlement authority."""
import asyncio
import copy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import sha
from custom_components.bsv_settlement.enrolment import available, context_hash, token
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from test_budget import consent, no_network
from test_driver_http import reservation, client_for, access

pytestmark = pytest.mark.asyncio


async def setup(tmp_path):
    hass, api, proxy, data, pending = await reservation(tmp_path)
    historical = next(r for r in api.saved["session_budgets"].values()
                      if r["terms"]["budget_id"] != pending["terms"]["budget_id"])
    await api.budgets.accept(historical, consent(historical), "driver")
    # An older receiving record is enough to reproduce the legacy QR blockage.
    historical["credit_destination"] = {"address": "fictional-history"}
    await api.auto_credits.configure(True, "admin")
    fresh = await api.budgets.execute("create_session_budget",
        data | {"multi_session": True, "valid_minutes": 10080}, "admin")
    row = api.saved["session_budgets"][fresh["terms"]["budget_id"]]
    args = {"budget_id": row["terms"]["budget_id"],
            "expected_invitation_hash": sha(row["invitation"]["payload"]),
            "expected_context_hash": context_hash(api, row),
            "confirm_public_registration": True}
    return hass, api, row, historical, args


async def test_open_preserves_history_private_links_and_disabled_policy(tmp_path):
    _, api, row, historical, args = await setup(tmp_path)
    before = copy.deepcopy(api.saved)
    old_token = api.budgets.link_token(historical["terms"]["budget_id"])
    assert available(api) is None
    assert not api.ongoing_credits.policy["enabled"]
    opened = await api.budgets.execute("open_public_registration", args, "admin")
    assert opened["public_registration"]["available"]
    assert available(api) is row and not api.chain.posts
    assert api.budgets.link_token(historical["terms"]["budget_id"]) == old_token
    assert {k:v for k,v in api.saved.items() if k != "public_registration_windows"} == before
    again = await api.budgets.execute("open_public_registration", args, "admin")
    assert again == opened
    restored = MainnetWalletAPI(api.hass, api.entry)
    await restored.load()
    assert available(restored)["terms"]["budget_id"] == row["terms"]["budget_id"]
    assert token(restored, available(restored)) == token(api, row)


@pytest.mark.parametrize("change", ["no_admin", "no_confirmation", "wrong_hash", "stale_context",
                                  "approved", "bound", "old_latest", "expired", "revoked", "v2"])
async def test_refuses_unreviewed_or_ineligible_window(tmp_path, change):
    _, api, row, _, args = await setup(tmp_path)
    user = "admin"
    if change == "no_admin":
        user = None
    elif change == "no_confirmation":
        args["confirm_public_registration"] = False
    elif change == "wrong_hash":
        args["expected_invitation_hash"] = "wrong"
    elif change == "stale_context":
        args["expected_context_hash"] = "wrong"
    elif change == "approved":
        await api.budgets.accept(row, consent(row), "driver")
    elif change == "bound":
        row["binding"] = {"session_id": "already-bound"}
    elif change == "old_latest":
        api.saved["latest_session_budget"] = "another"
    elif change == "expired":
        row["terms"]["expires_at"] = (now()-timedelta(seconds=1)).isoformat()
    elif change == "revoked":
        row["state"] = "revoked"
    else:
        row["terms"]["version"] = 2
    before = copy.deepcopy(api.saved)
    with pytest.raises(WalletError):
        await api.budgets.execute("open_public_registration", args, user)
    assert api.saved == before and not api.chain.posts


async def test_expiry_changed_registration_close_and_reopen_rotate_public_not_private(tmp_path, monkeypatch):
    _, api, row, historical, args = await setup(tmp_path)
    await api.budgets.execute("open_public_registration", args, "admin")
    old_public, private = token(api, row), api.budgets.link_token(row["terms"]["budget_id"])
    clock = now()
    monkeypatch.setattr("custom_components.bsv_settlement.enrolment.now",
                        lambda: clock + timedelta(minutes=16))
    assert available(api) is None
    monkeypatch.setattr("custom_components.bsv_settlement.enrolment.now", lambda: clock)
    historical["state"] = "revoked"
    assert available(api) is None
    historical["state"] = "spending_authorised_wallet_permission_required"
    await api.budgets.execute("close_public_registration", args, "admin")
    assert available(api) is None
    args["expected_context_hash"] = context_hash(api, row)
    await api.budgets.execute("open_public_registration", args, "admin")
    assert token(api, row) != old_public
    assert api.budgets.link_token(row["terms"]["budget_id"]) == private


async def test_failed_save_rolls_back_window(tmp_path, monkeypatch):
    _, api, row, _, args = await setup(tmp_path)
    async def fail(_):
        raise OSError("fictional storage failure")
    monkeypatch.setattr(api.store, "async_save", fail)
    with pytest.raises(OSError):
        await api.budgets.execute("open_public_registration", args, "admin")
    assert available(api) is None and not api.chain.posts


@pytest.mark.parametrize("is_admin", [None, False, True])
async def test_real_ha_service_requires_authenticated_administrator(tmp_path, monkeypatch, is_admin):
    hass, api, row, _, args = await setup(tmp_path)
    await async_setup(hass, {})
    coordinator = SettlementCoordinator(hass, api.entry, api)
    hass.data["bsv_settlement"][api.entry.entry_id] = coordinator
    monkeypatch.setattr(hass, "auth", SimpleNamespace(async_get_user=AsyncMock(
        return_value=SimpleNamespace(is_admin=is_admin) if is_admin is not None else None)),
        raising=False)
    context = Context(user_id="fictional-admin" if is_admin is not None else None)
    async def invoke():
        return await hass.services.async_call("bsv_settlement", "open_public_registration",
            args | {"config_entry_id": api.entry.entry_id},
            blocking=True, return_response=True, context=context)
    if is_admin:
        assert (await invoke())["public_registration"]["available"]
    else:
        with pytest.raises(HomeAssistantError, match="administrator"):
            await invoke()
        assert available(api) is None
    assert not api.chain.posts


async def test_closing_overrides_automatic_no_driver_discovery(tmp_path):
    _, api, row, _, args = await setup(tmp_path)
    for other in api.saved["session_budgets"].values():
        if other is not row:
            other["state"] = "revoked"
    assert available(api) is row
    await api.budgets.execute("close_public_registration", args, "admin")
    assert available(api) is None
    assert api.budgets.driver_access(row["terms"]["budget_id"],
                                    api.budgets.link_token(row["terms"]["budget_id"])) is row


async def test_public_handoff_one_winner_and_no_history_or_admin_window_exposure(tmp_path):
    hass, api, row, historical, args = await setup(tmp_path)
    history = copy.deepcopy(historical)
    await api.budgets.execute("open_public_registration", args, "admin")
    client, view = await client_for(hass, api)
    fields = {"join": row["terms"]["budget_id"], "key": token(api, row)}
    try:
        read = await client.post(view.url, json=fields | {"action": "public_read"})
        assert set(await read.json()) == {"invitation", "state", "prices", "public_enrolment"}
        # Operator controls are never callable through the anonymous endpoint.
        forbidden = await client.post(view.url, json=fields | {"action": "open_public_registration"})
        assert forbidden.status == 400
        receipts = [consent(row), consent(row)]
        results = await asyncio.gather(*[
            client.post(view.url, json=fields | {"action": "public_approve", "receipt": receipt})
            for receipt in receipts])
        assert sorted(r.status for r in results) == [200, 400]
        winner = next(i for i, response in enumerate(results) if response.status == 200)
        body = await results[winner].json()
        assert "private_link_fragment" in body
        assert "public_registration" not in body and "public_registration_windows" not in body
        retry = await client.post(view.url, json=fields | {
            "action": "public_approve", "receipt": receipts[winner]})
        assert retry.status == 200
        assert available(api) is None and historical == history
        with pytest.raises(WalletError, match="Already approved"):
            await api.budgets.execute("close_public_registration", args, "admin")
        stale = await client.post(view.url, json=fields | {"action": "public_read"})
        assert stale.status == 400 and not api.chain.posts
    finally:
        await client.close()
