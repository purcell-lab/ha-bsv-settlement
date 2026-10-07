"""Fictional wallet/provider tests. No live HA, chain, driver or funds."""
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from bsv import PrivateKey
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.portal import history, credit_owner
from custom_components.bsv_settlement.receipt_ack import acknowledge
from custom_components.bsv_settlement.session_review import now
from test_auto_credit import ready
from test_budget import no_network
from test_receipt_ack import report
from test_session_review import review_approval

pytestmark = pytest.mark.asyncio


async def setup(tmp_path, price="0.25"):
    hass, api, proxy, row, driver, _ = await ready(tmp_path)
    for entity in proxy.sources.values():
        hass.states.async_set(entity, price, {
            "unit_of_measurement": "$/kWh", "estimate": False,
            "start_time": (now() - timedelta(minutes=1)).isoformat(),
            "end_time": (now() + timedelta(minutes=5)).isoformat()})
    data = {"proxy_config_entry_id": "proxy-entry", "conversion_rate_entity": "sensor.demo_rate",
            "energy_direction": "export", "request_id": str(uuid4())}
    return hass, api, proxy, row, driver, data


@pytest.mark.parametrize("direction,price,expected", [
    ("export", "0.25", "operator_to_driver"), ("import", "0.25", "driver_to_operator"),
    ("export", "-0.25", "driver_to_operator"), ("import", "-0.25", "operator_to_driver"),
])
async def test_five_kwh_frozen_separate_direction_and_retry(tmp_path, direction, price, expected):
    hass, api, proxy, row, driver, data = await setup(tmp_path, price)
    data["energy_direction"] = direction
    metering, budgets = copy.deepcopy(proxy.data), copy.deepcopy(api.saved["session_budgets"])
    review = await api.reviews.execute("prepare_energy_adjustment", data, "admin")
    assert review["amount_sats"] == 125 and review["direction"] == expected
    assert review["account"]["adjustment_kwh"] == "5"
    assert review["account"]["import_kwh"] is None and review["account"]["export_kwh"] is None
    assert review["account"]["session_id"].startswith("adjustment:")
    assert review["driver"]["driver_public_identity"] == driver.public_key().hex()
    assert review["account_kind"] == "manual_energy_adjustment"
    # Fresh click and lost-response retry both return the same outstanding account.
    for request in (data, data | {"request_id": str(uuid4())}):
        assert (await api.reviews.execute("prepare_energy_adjustment", request, "admin")) == review
    assert proxy.data == metering and api.saved["session_budgets"] == budgets
    assert not api.chain.posts and not api.saved.get("driver_collections")
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    assert (await restored.reviews.execute("prepare_energy_adjustment", data, "admin")) == review
    with pytest.raises(WalletError, match="different terms"):
        await api.reviews.execute("prepare_energy_adjustment", data | {
            "energy_direction": "import" if direction == "export" else "export"}, "admin")


@pytest.mark.parametrize("price", ["unknown", "NaN", "Infinity", "0", "0.00001", "2"])
async def test_invalid_zero_subsat_and_over_limit_price_blocks(tmp_path, price):
    _, api, _, _, _, data = await setup(tmp_path, price)
    with pytest.raises(WalletError):
        await api.reviews.execute("prepare_energy_adjustment", data, "admin")
    assert not api.saved["session_reviews"] and not api.chain.posts


@pytest.mark.parametrize("changes", [
    {"unit_of_measurement": "c/kWh"},
    {"end_time": "2020-01-01T00:00:00+00:00"},
    {"start_time": "2099-01-01T00:00:00+00:00"},
    {"start_time": "2026-10-05T00:00:00"},
])
async def test_tariff_validity_blocks(tmp_path, changes):
    hass, api, proxy, _, _, data = await setup(tmp_path)
    entity = proxy.sources["export_price"]
    state = hass.states.get(entity)
    hass.states.async_set(entity, state.state, dict(state.attributes) | changes)
    with pytest.raises(WalletError, match="tariff"):
        await api.reviews.execute("prepare_energy_adjustment", data, "admin")
    assert not api.chain.posts


async def test_changed_or_revoked_current_driver_cannot_send(tmp_path):
    _, api, _, row, _, data = await setup(tmp_path)
    review = await api.reviews.execute("prepare_energy_adjustment", data, "admin")
    row["state"] = "revoked"
    with pytest.raises(WalletError):
        await api.reviews.approve(review_approval(review), "admin")
    assert not api.chain.posts


async def test_debit_issues_manual_request_not_charging_consent(tmp_path):
    _, api, proxy, _, driver, data = await setup(tmp_path)
    review = await api.reviews.execute("prepare_energy_adjustment", data | {"energy_direction": "import"}, "admin")
    before = copy.deepcopy(api.saved["session_budgets"])
    approved = await api.reviews.approve(review_approval(review), "admin")
    assert approved["state"] == "awaiting_driver_payment"
    assert approved["payment_request"]["amount_sats"] == 125
    assert approved["recipient_address"] == api.identity["address"]
    assert api.saved["session_budgets"] == before and not api.saved.get("driver_collections")
    assert not api.chain.posts
    rows = history(api, driver.public_key().hex())
    adjustment = next(r for r in rows if r["session_id"] == review["account"]["session_id"])
    assert adjustment["account_kind"] == "manual_energy_adjustment"
    assert adjustment["import_kwh"] is None


async def test_credit_explicit_fee_quote_broadcast_once_receipt_and_owner_isolation(tmp_path):
    hass, api, proxy, row, driver, data = await setup(tmp_path)
    metering = copy.deepcopy(proxy.data)
    review = await api.reviews.execute("prepare_energy_adjustment", data, "admin")
    await api.reviews.approve(review_approval(review), "admin")
    draft = await api.reviews.execute("prepare_adjustment_credit", {
        "review_id": review["review_id"], "terms_hash": review["terms_hash"]}, "admin")
    p = draft["credit_draft"]
    assert p["amount_sats"] == 125 and p["fee_sats"] == 10 and not api.chain.posts
    assert p["recipient_address"] == row["credit_destination"]["address"]
    send = review_approval(review) | {"draft_id": p["draft_id"], "fee_sats": p["fee_sats"],
                                     "confirm_mainnet_payment": True}
    with pytest.raises(WalletError):
        await api.reviews.broadcast_credit(send | {"confirm_mainnet_payment": False}, "admin")
    await api.reviews.broadcast_credit(send, "admin")
    await api.reviews.broadcast_credit(send, "admin")
    assert len(api.chain.posts) == 1 and proxy.data == metering
    cid = "adjustment:" + review["review_id"]
    registered, item, route = credit_owner(api, driver.public_key().hex(), cid)
    assert route["adjustment"] and registered is row
    with pytest.raises(WalletError):
        credit_owner(api, PrivateKey().public_key().hex(), cid)
    receipt = await api.auto_credits.receipt_for_item(row, item)
    assert receipt["remittance"] == row["terms"]["credit_receiving"]
    assert receipt["energy_account"]["import_kwh"] is None
    # Exercise the actual authenticated transport. A direct acknowledgement
    # test misses a malformed envelope rejected by the browser before import.
    import asyncio
    import json
    from types import SimpleNamespace
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from test_portal import ORIGIN, DriverPortalView, PairingHub, PAIRING_KEY, login, post
    hass.config.external_url = ORIGIN
    hass.data["bsv_settlement"]["wallet"] = SimpleNamespace(
        mode="embedded_mainnet", api=api, lock=asyncio.Lock())
    view = DriverPortalView(hass)
    hub = hass.data[PAIRING_KEY] = PairingHub(hass)
    app = web.Application()
    app.router.add_post(view.url, view.post)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        cookie, _, _ = await login(client, view, driver)
        response = await post(client, view, "credit_receipt", cookie, credit_id=cid)
        assert response.status == 200
        envelope = await response.json()
        terms = json.loads(envelope["invitation"]["payload"])
        assert envelope["receipt"]["budget_id"] == terms["budget_id"]
        assert envelope["receipt"]["credit_id"] == cid
        assert envelope["receipt"]["remittance"] == terms["credit_receiving"]
        assert envelope["receipt"]["recipient_address"] == item["recipient_address"]
        assert item["budget_id"] == cid  # Never rewrite the stored payment.
        assert "wallet_receipt_ack" not in item
        response = await post(client, view, "acknowledge_credit_receipt", cookie,
                              credit_id=cid, **report(api, row, item, driver))
        assert response.status == 200
        assert (await response.json())["wallet_receipt_status"] == "wallet_reported_accepted"
    finally:
        await hub.close_all()
        await client.close()
    assert len(api.chain.posts) == 1


async def test_credit_total_cap_and_expired_review(tmp_path):
    _, api, _, _, _, data = await setup(tmp_path, "1.99")
    review = await api.reviews.execute("prepare_energy_adjustment", data, "admin")
    await api.reviews.approve(review_approval(review), "admin")
    with pytest.raises(WalletError, match="1000"):
        await api.reviews.execute("prepare_adjustment_credit", {
            "review_id": review["review_id"], "terms_hash": review["terms_hash"]}, "admin")
    assert not api.saved["payments"] and not api.chain.posts
    saved = api.reviews.get(review["review_id"])
    saved["expires_at"] = (now() - timedelta(seconds=1)).isoformat()
    with pytest.raises(WalletError, match="expired"):
        await api.reviews.prepare_credit({
            "review_id": review["review_id"], "terms_hash": review["terms_hash"], "fee_sats": 1})


async def test_services_reject_context_free_automation(tmp_path):
    hass, api, _, _, _, data = await setup(tmp_path)
    await async_setup(hass, {})
    with pytest.raises(HomeAssistantError, match="administrator"):
        await hass.services.async_call("bsv_settlement", "prepare_energy_adjustment",
            data | {"config_entry_id": api.entry.entry_id}, blocking=True, context=Context())
    assert not api.saved["session_reviews"]


@pytest.mark.parametrize("direction,price,credit", [
    ("export", "0.25", True), ("import", "-0.25", True),
    ("import", "0.25", False), ("export", "-0.25", False)])
async def test_one_click_sends_credit_or_issues_debit_request(tmp_path, direction, price, credit):
    _, api, proxy, _, _, data = await setup(tmp_path, price)
    data |= {"energy_direction": direction, "confirm_mainnet_payment": True}
    metering, budgets = copy.deepcopy(proxy.data), copy.deepcopy(api.saved["session_budgets"])
    result = await api.reviews.execute("pay_energy_adjustment", data, "admin")
    assert result["state"] == ("credit_submitted" if credit else "awaiting_driver_payment")
    assert result["amount_sats"] == 125
    assert result["identity_verification"] == "registered_wallet_verified_and_operator_one_click"
    assert "one_click_authorised_by" not in result
    for retry in (data, data | {"request_id": str(uuid4())}):
        again = await api.reviews.execute("pay_energy_adjustment", retry, "admin")
        assert again["review_id"] == result["review_id"]
    assert len(api.chain.posts) == int(credit)
    assert proxy.data == metering and api.saved["session_budgets"] == budgets
    assert not api.saved.get("driver_collections")


async def test_one_click_never_adopts_existing_manual_review(tmp_path):
    _, api, _, _, _, data = await setup(tmp_path)
    original = await api.reviews.execute("prepare_energy_adjustment", data, "admin")
    result = await api.reviews.execute("pay_energy_adjustment", data | {
        "request_id": str(uuid4()), "confirm_mainnet_payment": True}, "admin")
    assert result["review_id"] == original["review_id"]
    assert result["state"] == "awaiting_account_approval"
    assert not api.chain.posts


async def test_one_click_failed_attempt_cannot_auto_resume_and_retains_total_cap(tmp_path):
    _, api, _, _, _, data = await setup(tmp_path, "1.99")
    data["confirm_mainnet_payment"] = True
    with pytest.raises(WalletError, match="1000"):
        await api.reviews.execute("pay_energy_adjustment", data, "admin")
    result = await api.reviews.execute("pay_energy_adjustment", data, "admin")
    assert result["one_click_stopped"] is True
    assert result["state"] == "credit_review_approved"
    assert not api.chain.posts


async def test_one_click_authorisation_and_context_required(tmp_path):
    hass, api, _, _, _, data = await setup(tmp_path)
    with pytest.raises(WalletError, match="authorisation"):
        await api.reviews.execute("pay_energy_adjustment", data, "admin")
    await async_setup(hass, {})
    with pytest.raises(HomeAssistantError, match="administrator"):
        await hass.services.async_call("bsv_settlement", "pay_energy_adjustment",
            data | {"config_entry_id": api.entry.entry_id, "confirm_mainnet_payment": True},
            blocking=True, context=Context())
    assert not api.saved["session_reviews"] and not api.chain.posts


async def test_one_click_replay_survives_restart(tmp_path):
    hass, api, _, _, _, data = await setup(tmp_path)
    data["confirm_mainnet_payment"] = True
    result = await api.reviews.execute("pay_energy_adjustment", data, "admin")
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    again = await restored.reviews.execute("pay_energy_adjustment", data, "admin")
    assert again["review_id"] == result["review_id"]
    assert len(api.chain.posts) == 1
