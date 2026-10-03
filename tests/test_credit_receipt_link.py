"""Read-only original-link recovery; fictional keys, no real transactions."""
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.credit_receipt_link import retrieve
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from test_auto_credit import ready
from test_budget import no_network
from test_driver_http import client_for
from test_ongoing_credit import setup

pytestmark = pytest.mark.asyncio


async def confirmed(tmp_path, ongoing=False):
    hass, api, proxy, row, driver, _ = await (setup(tmp_path) if ongoing else ready(tmp_path))
    worker = api.ongoing_credits if ongoing else api.auto_credits
    await worker.tick()
    await worker.tick()
    item = next(iter(api.saved["automatic_credits"].values()))
    hass.config.external_url = "https://charger.example"
    data = {"credit_id": item["budget_id"], "expected_txid": item["txid"],
            "expected_recipient_address": item["recipient_address"],
            "confirm_private_link_disclosure": True}
    return hass, api, row, item, data


@pytest.mark.parametrize("ongoing", [False, True])
async def test_original_link_read_only_repeatable_and_persistent(tmp_path, ongoing):
    hass, api, row, item, data = await confirmed(tmp_path, ongoing)
    before = copy.deepcopy(api.saved)
    api.store.async_save = AsyncMock(side_effect=AssertionError("No writes allowed"))
    api.chain = SimpleNamespace()  # Any network request fails this test.
    result = retrieve(api, data, "admin")
    assert result == retrieve(api, data, "admin")
    fragment = urlsplit(result["driver_url"]).fragment
    token = parse_qs(fragment)["token"][0]
    assert parse_qs(fragment)["budget"] == [row["terms"]["budget_id"]]
    assert result["driver_url"].startswith("https://charger.example/bsv_settlement/driver/index.html#")
    assert api.budgets.driver_access(row["terms"]["budget_id"], token, allow_terminal=True) is row
    assert result["same_link"] and not result["payment_sent"] and not result["authority_changed"]
    assert result["txid"] == item["txid"] and result["chain_status_refreshed"] is False
    assert token not in json.dumps(api.saved)
    assert token not in json.dumps(api.budgets.summary())
    assert token not in json.dumps(api.auto_credits.summary())
    assert token not in json.dumps(api.budgets.driver_view(row))
    assert api.saved == before
    api.store.async_save.assert_not_called()
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    assert retrieve(restored, data, "admin") == result


@pytest.mark.parametrize("case", [
    "no_admin", "no_confirm", "wrong_txid", "wrong_recipient", "unknown",
    "unconfirmed", "uncertain", "no_confirmations", "wrong_owner",
    "no_receipt", "bad_consent", "bad_invitation", "bad_destination",
    "bad_proof", "legacy", "hash_mismatch", "weekly", "missing_registration",
    "held_collection", "ready_collection", "reserved_collection",
])
async def test_invalid_disclosure_fails_without_mutation(tmp_path, case):
    _, api, row, item, data = await confirmed(tmp_path)
    if case == "no_confirm": data["confirm_private_link_disclosure"] = False
    if case == "wrong_txid": data["expected_txid"] = "00"*32
    if case == "wrong_recipient": data["expected_recipient_address"] = "different"
    if case == "unknown": data["credit_id"] = "missing"
    if case == "unconfirmed": item["state"] = "provider_unconfirmed"
    if case == "uncertain": item["state"] = "broadcast_unknown"
    if case == "no_confirmations": item["confirmations"] = 0
    if case == "wrong_owner": api.saved["automatic_credit_index"].clear()
    if case == "no_receipt": row["receipt"] = None
    if case == "bad_consent": row["receipt"]["signature"] = "00"*70
    if case == "bad_invitation": row["invitation"]["signature"] = "00"*70
    if case == "bad_destination": row["credit_destination"]["address"] = "different"
    if case == "bad_proof": row["credit_destination"]["proof"]["signature"] = "00"*70
    if case == "legacy": row.pop("driver_link_scheme")
    if case == "hash_mismatch": row["driver_token_hash"] = "0"*64
    if case == "weekly": row["terms"]["version"] = 3
    if case == "missing_registration": api.saved["session_budgets"].pop(row["terms"]["budget_id"])
    if case in ("held_collection", "ready_collection", "reserved_collection"):
        api.saved["driver_collections"][row["terms"]["budget_id"]] = {
            "state": {"held_collection":"wallet_attempt_reserved", "ready_collection":"ready",
                      "reserved_collection":"submission_authorised"}[case]}
    before = copy.deepcopy(api.saved)
    with pytest.raises(WalletError):
        retrieve(api, data, None if case=="no_admin" else "admin")
    assert api.saved == before
    assert len(api.chain.posts) == 1


@pytest.mark.parametrize("origin", [None, "http://charger.example", "https://user:secret@charger.example",
    "https://charger.example/path", "https://charger.example?redirect=elsewhere", "https://127.0.0.1"])
async def test_no_unsafe_or_caller_selected_origin(tmp_path, origin):
    hass, api, _, _, data = await confirmed(tmp_path)
    hass.config.external_url = origin
    with pytest.raises(WalletError, match="public HTTPS"):
        retrieve(api, data | {"origin": "https://other.example"}, "admin")


async def test_expiry_revocation_and_disabled_policy_do_not_change_recovered_link(tmp_path, monkeypatch):
    _, api, row, _, data = await confirmed(tmp_path)
    original = retrieve(api, data, "admin")
    monkeypatch.setattr("custom_components.bsv_settlement.budget.now", lambda: now()+timedelta(days=2))
    assert api.budgets.public(row)["state"] == "expired"
    assert retrieve(api, data, "admin") == original
    row["state"] = "revoked"
    api.auto_credits.policy["enabled"] = False
    assert retrieve(api, data, "admin") == original
    assert row["state"] == "revoked" and not api.auto_credits.policy["enabled"]


async def test_several_credits_recover_same_original_link_not_latest_driver(tmp_path):
    _, api, row, item, data = await confirmed(tmp_path, True)
    first = retrieve(api, data, "admin")
    route = next(iter(api.ongoing_credits.routes.values()))
    other_route = copy.deepcopy(route)
    other_route.update(route_id=route["route_id"]+"-other", session_id="other-session",
                       transaction_id="other-proxy-id")
    api.ongoing_credits.routes[other_route["route_id"]] = other_route
    other_item = copy.deepcopy(item)
    other_item.update(budget_id=other_route["route_id"], session_id="other-session",
                      transaction_id="other-proxy-id", txid="ab"*32)
    api.saved["automatic_credits"][other_item["budget_id"]] = other_item
    api.saved["automatic_credit_index"][row["proxy_config_entry_id"]+"|other-session"] = other_item["budget_id"]
    # The original account is closed/waived; retrieval never initiates collection.
    api.saved["closed_sessions"][api.collections.key(row)] = {"state":"waived"}
    api.ongoing_credits.latest = lambda **_: (_ for _ in ()).throw(AssertionError("Never use latest driver"))
    second = retrieve(api, data | {"credit_id":other_item["budget_id"], "expected_txid":other_item["txid"]}, "admin")
    assert second["driver_url"] == first["driver_url"]
    api.saved["closed_sessions"].clear()
    with pytest.raises(WalletError, match="terminal evidence"):
        retrieve(api, data | {"credit_id":other_item["budget_id"], "expected_txid":other_item["txid"]}, "admin")
    other_route["recipient"]["budget_id"] = "another"
    with pytest.raises(WalletError):
        retrieve(api, data | {"credit_id":other_item["budget_id"], "expected_txid":other_item["txid"]}, "admin")


async def test_public_http_never_discloses_recovered_capability(tmp_path):
    hass, api, row, _, data = await confirmed(tmp_path)
    client, view = await client_for(hass, api)
    try:
        response = await client.post(view.url, json={**data, "action":"get_credit_receipt_link",
            "budget_id":row["terms"]["budget_id"], "token":api.budgets.link_token(row["terms"]["budget_id"])})
        assert response.status == 400
        assert "driver_url" not in await response.text()
    finally:
        await client.close()


async def test_real_ha_admin_service_does_not_tick_or_refresh(tmp_path):
    hass, api, _, _, data = await confirmed(tmp_path)
    coord = SettlementCoordinator(hass, api.entry, api)
    await async_setup(hass, {})
    hass.data["bsv_settlement"][api.entry.entry_id] = coord
    data = data | {"config_entry_id":api.entry.entry_id}
    coord._async_update_data = AsyncMock(side_effect=AssertionError("No update"))
    api.auto_credits.tick = AsyncMock(side_effect=AssertionError("No settlement"))
    api.ongoing_credits.tick = AsyncMock(side_effect=AssertionError("No settlement"))
    with pytest.raises(HomeAssistantError, match="administrator"):
        await hass.services.async_call("bsv_settlement","get_credit_receipt_link",data,
                                      blocking=True,return_response=True)
    hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
    with pytest.raises(HomeAssistantError, match="administrator"):
        await hass.services.async_call("bsv_settlement","get_credit_receipt_link",data,
            blocking=True,return_response=True,context=Context(user_id="ordinary-user"))
    hass.auth.async_get_user.return_value.is_admin = True
    result = await hass.services.async_call("bsv_settlement","get_credit_receipt_link",data,
        blocking=True,return_response=True,context=Context(user_id="admin"))
    assert result["same_link"]
    coord._async_update_data.assert_not_called()
    api.auto_credits.tick.assert_not_called()
    api.ongoing_credits.tick.assert_not_called()
