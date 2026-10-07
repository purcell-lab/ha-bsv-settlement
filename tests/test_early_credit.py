"""No live wallets: bounded ancestry, opt-in, ownership and receipt-state isolation."""
import copy
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bsv import PrivateKey, Transaction

from custom_components.bsv_settlement import early_credit
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import canonical, message_hash
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.portal import history
from custom_components.bsv_settlement.receipt_ack import acknowledge, acceptance_payload, PROTOCOL
from test_auto_credit import ready
from test_budget import no_network
from test_operator_credit_unconfirmed import unmined
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_portal import ORIGIN, DriverPortalView, PairingHub, PAIRING_KEY, login, post

pytestmark = pytest.mark.asyncio


async def pending(tmp_path, opt_in=True):
    hass, api, _, row, driver, _ = await ready(tmp_path)
    if opt_in:
        await early_credit.configure(api, True, "admin")
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    tx = Transaction.from_hex(item["signed_raw"])
    api.chain.details = AsyncMock(return_value=unmined(tx))
    parent_raw = api.chain.raw
    parent_id = Transaction.from_hex(parent_raw).txid()
    api.chain.source = AsyncMock(return_value=parent_raw)

    async def request(method, path, raw=False):
        assert method == "GET"  # Receipt delivery never submits another payment.
        if path == f"/tx/{item['txid']}/hex":
            return item["signed_raw"]
        if path == f"/tx/{parent_id}/proof/tsc":
            return [{"txOrId": parent_id, "index": 0, "nodes": [], "target": "22"*32}]
        if path == f"/block/hash/{'22'*32}":
            return {"hash": "22"*32, "height": 800000, "merkleroot": parent_id}
        raise AssertionError(path)

    api.chain.request = request
    await api.auto_credits.reconcile(item)
    return hass, api, row, driver, item


def signed_report(api, row, driver, item, stage=early_credit.MODE):
    payload = acceptance_payload(api, row, item, stage)
    key = driver.derive_child(PrivateKey(1).public_key(), f"2-{PROTOCOL}-{row['terms']['budget_id']}")
    return {"delivery_stage": stage, "acknowledgement": {
        "payload": payload, "signature": key.sign(payload.encode(), hasher=message_hash).hex()}}


async def test_disabled_default_and_enable_does_not_adopt_older_submissions(tmp_path):
    _, api, row, _, item = await pending(tmp_path, opt_in=False)
    assert not early_credit.eligible(api, item)
    with pytest.raises(early_credit.Unavailable):
        await api.auto_credits.receipt_for_item(row, item, allow_early=True)
    await early_credit.configure(api, True, "admin")
    assert not early_credit.eligible(api, item)
    assert len(api.chain.posts) == 1


async def test_single_proven_parent_and_signed_acceptance_before_confirmation(tmp_path):
    hass, api, row, driver, item = await pending(tmp_path)
    before = {k: copy.deepcopy(item[k]) for k in ("state", "confirmations", "signed_raw", "txid", "amount_sats")}
    receipt = await api.auto_credits.receipt_for_item(row, item, allow_early=True)
    assert receipt["delivery_stage"] == early_credit.MODE
    assert len(receipt["ancestry"]) == 1 and "proof" not in receipt
    assert receipt["ancestry"][0]["txid"] == item["source_txid"]
    assert item["early_receipt_offer"]["txid"] == item["txid"]
    result = await acknowledge(api, row, signed_report(api, row, driver, item))
    assert result["wallet_receipt_status"] == "wallet_reported_accepted"
    assert result["state"] == "provider_unconfirmed" and result["confirmations"] == 0
    assert result["wallet_accepted_reported_at"]
    assert {k: item[k] for k in before} == before
    assert await acknowledge(api, row, signed_report(api, row, driver, item)) == result
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    assert restored.auto_credits.public(restored.auto_credits.get(row)) == result
    records = history(api, driver.public_key().hex())
    public = next(t for s in records for t in s["transactions"] if t["txid"] == item["txid"])
    assert public["wallet_receipt_status"] == "wallet_reported_accepted"
    assert "ancestry" not in json.dumps(records) and "signed_raw" not in json.dumps(records)
    api.chain.details = AsyncMock(return_value={"txid": item["txid"], "confirmations": 1})
    await api.auto_credits.reconcile(item, force=True)
    assert item["state"] == "provider_confirmed"
    assert item["wallet_receipt_ack"]["reported_at"] == result["wallet_imported_at"]
    assert len(api.chain.posts) == 1


async def test_unconfirmed_ack_requires_issued_offer_and_stage_bound_signature(tmp_path):
    _, api, row, driver, item = await pending(tmp_path)
    with pytest.raises(WalletError, match="offered"):
        await acknowledge(api, row, signed_report(api, row, driver, item))
    await api.auto_credits.receipt_for_item(row, item, allow_early=True)
    with pytest.raises(WalletError, match="confirmation"):
        await acknowledge(api, row, signed_report(api, row, driver, item, None))
    bad = signed_report(api, row, driver, item)
    bad["delivery_stage"] = None
    with pytest.raises(WalletError):
        await acknowledge(api, row, bad)
    item["state"] = "broadcast_unknown"
    with pytest.raises(WalletError):
        await acknowledge(api, row, signed_report(api, row, driver, item))
    assert not item.get("wallet_receipt_ack")


@pytest.mark.parametrize("bad", ["parent", "proof", "cap", "unknown"])
async def test_invalid_ancestry_defers_without_offer_or_resend(tmp_path, bad):
    _, api, _, _, item = await pending(tmp_path)
    if bad == "parent":
        api.chain.source = AsyncMock(side_effect=WalletError("Parent unconfirmed"))
    elif bad == "proof":
        old = api.chain.request
        async def request(method, path, raw=False):
            result = await old(method, path, raw)
            if path.startswith("/block/"):
                result["merkleroot"] = "33"*32
            return result
        api.chain.request = request
    elif bad == "cap":
        item["fee_sats"] = 1000
    else:
        item["state"] = "broadcast_unknown"
    with pytest.raises(early_credit.Unavailable):
        await early_credit.envelope(api, item)
    assert not item.get("early_receipt_offer")
    assert len(api.chain.posts) == 1


async def test_policy_disable_preserves_payment_and_inflight_receipt_report(tmp_path):
    _, api, row, driver, item = await pending(tmp_path)
    await api.auto_credits.receipt_for_item(row, item, allow_early=True)
    before = copy.deepcopy(item)
    await early_credit.configure(api, False, "admin")
    assert item == before and not early_credit.eligible(api, item)
    assert (await acknowledge(api, row, signed_report(api, row, driver, item)))["state"] == "provider_unconfirmed"
    with pytest.raises(WalletError):
        await early_credit.configure(api, True, None)


async def test_authenticated_http_early_offer_and_ack_with_safe_deferral(tmp_path):
    hass, api, row, driver, item = await pending(tmp_path)
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
        response = await post(client, view, "credit_receipt", cookie,
                              credit_id=item["budget_id"], allow_unconfirmed=True)
        assert response.status == 200
        body = await response.json()
        assert body["receipt"]["delivery_stage"] == early_credit.MODE
        assert body["receipt"]["budget_id"] == row["terms"]["budget_id"]
        response = await post(client, view, "acknowledge_credit_receipt", cookie,
                              credit_id=item["budget_id"], **signed_report(api, row, driver, item))
        assert response.status == 200
        assert (await response.json())["state"] == "provider_unconfirmed"
        stranger, _, _ = await login(client, view, PrivateKey(77))
        response = await post(client, view, "credit_receipt", stranger,
                              credit_id=item["budget_id"], allow_unconfirmed=True)
        assert response.status == 401
        api.chain.source = AsyncMock(side_effect=WalletError("No parent proof"))
        response = await post(client, view, "credit_receipt", cookie,
                              credit_id=item["budget_id"], allow_unconfirmed=True)
        assert response.status == 200
        assert (await response.json())["deferred_until_confirmation"] is True
    finally:
        await hub.close_all()
        await client.close()
    assert len(api.chain.posts) == 1


async def test_operator_service_refuses_context_free_changes(tmp_path):
    from homeassistant.exceptions import HomeAssistantError
    _, api, _, _, _ = await pending(tmp_path, opt_in=False)
    with pytest.raises(HomeAssistantError):
        await api.hass.services.async_call("bsv_settlement", "configure_early_credit_delivery",
            {"config_entry_id": api.entry.entry_id, "enabled": True}, blocking=True)
    assert not early_credit.enabled(api)
