"""Fictional wallets only: no live endpoint, no real payment."""
import copy
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.adjustment_collection import AdjustmentCollections, FORMAT
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.collection import transaction_shape
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.portal import history
from custom_components.bsv_settlement.portal_debits import jobs, handle
from custom_components.bsv_settlement.session_review import now
from test_budget import no_network
from test_collection import CollectionChain, claim_data, payment
from test_energy_adjustment import setup

pytestmark = pytest.mark.asyncio


async def ready(tmp_path, direction="import", price="0.25"):
    hass, api, proxy, registration, driver, data = await setup(tmp_path, price)
    data.update(energy_direction=direction, confirm_mainnet_payment=True)
    review = await api.reviews.execute("pay_energy_adjustment", data, "admin")
    api.chain = CollectionChain(driver.address())
    return hass, api, registration, driver, data, review


@pytest.mark.parametrize("direction,price", [("import", "0.25"), ("export", "-0.25")])
async def test_button_debit_discovered_signed_and_paid_once_without_weekly_budget(tmp_path, direction, price):
    _, api, registration, driver, data, review = await ready(tmp_path, direction, price)
    before = copy.deepcopy(api.saved["session_budgets"])
    assert review["payment_request"]["format"] == FORMAT
    identity = driver.public_key().hex()
    queue = (await jobs(api, identity))["jobs"]
    job = next(j for j in queue if j.get("review_id") == review["review_id"])
    assert job["kind"] == "adjustment"
    result = await handle(api, identity, {"action": "debit_status", **job})
    worker = AdjustmentCollections(api)
    row = worker.row(api.reviews.get(review["review_id"]))
    args = claim_data(result["collection"], row, driver)
    quote = json.loads(result["collection"]["quote"]["payload"])
    assert quote["amount_sats"] == 125 and quote["max_total_sats"] == 1000
    assert quote["account"]["import_kwh"] is None
    with pytest.raises(WalletError):
        await handle(api, identity, {"action": "debit_claim", **job})  # Login is not payer consent.
    assert (await handle(api, identity, {"action": "debit_claim", **job, **args}))["claimed"]
    tx = payment(api, driver, 125, 5)
    assert (await handle(api, identity, {"action": "debit_authorise", **job, **args,
                                        "draft": transaction_shape(tx)}))["submit_once"]
    sent = await handle(api, identity, {"action": "debit_report", **job, **args, "raw_tx": tx.hex()})
    assert sent["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1
    await handle(api, identity, {"action": "debit_report", **job, **args, "raw_tx": tx.hex()})
    assert len(api.chain.posts) == 1
    assert api.saved["session_budgets"] == before
    public = api.reviews.public(api.reviews.get(review["review_id"]))
    assert public["state"] == "driver_payment_provider_confirmed"
    assert "signed_raw" not in json.dumps(public) and "attempt_token_hash" not in json.dumps(public)
    summary = next(p for p in api.payment_summary() if p.get("review_id") == review["review_id"])
    assert summary["txid"] == tx.txid() and summary["state"] == "provider_confirmed"
    account = next(p for p in history(api, identity) if p["session_id"] == job["session_id"])
    assert account["adjustment_direction"] == direction
    assert account["adjustment_price_aud_per_kwh"] == price
    assert account["transactions"][0]["state"] == "provider_confirmed"
    assert not any(j.get("review_id") == review["review_id"] for j in (await jobs(api, identity))["jobs"])


async def test_legacy_manual_request_is_never_auto_migrated(tmp_path):
    _, api, _, _, driver, data = await setup(tmp_path)
    data["energy_direction"] = "import"
    review = await api.reviews.execute("prepare_energy_adjustment", data, "admin")
    from test_session_review import review_approval
    await api.reviews.approve(review_approval(review), "admin")
    result = await api.reviews.execute("pay_energy_adjustment", data | {
        "request_id": str(uuid4()), "confirm_mainnet_payment": True}, "admin")
    assert result["review_id"] == review["review_id"]
    assert result["payment_request"]["format"] == "manual_bsv_payment_request_v1"
    assert not result.get("wallet_collection_enabled")
    assert not any(j.get("kind") == "adjustment" for j in (await jobs(api, driver.public_key().hex()))["jobs"])
    assert not api.chain.posts


@pytest.mark.parametrize("change", ["other_wallet", "expired", "revoked", "changed_account", "changed_amount"])
async def test_scope_and_frozen_terms_block_before_wallet_attempt(tmp_path, change):
    _, api, registration, driver, _, review = await ready(tmp_path)
    identity = driver.public_key().hex()
    job = next(j for j in (await jobs(api, identity))["jobs"] if j.get("kind") == "adjustment")
    stored = api.reviews.get(review["review_id"])
    if change == "other_wallet":
        identity = PrivateKey(999).public_key().hex()
    elif change == "expired":
        stored["expires_at"] = (now() - timedelta(seconds=1)).isoformat()
    elif change == "revoked":
        registration["state"] = "revoked"
    elif change == "changed_amount":
        stored["amount_sats"] += 1
    else:
        stored["account"]["net_amount_aud"] = "0.01"
    with pytest.raises(WalletError):
        await handle(api, identity, {"action": "debit_status", **job})
    assert not api.chain.posts
    assert not stored.get("wallet_collection")


async def test_attempt_survives_restart_and_scheduler_reconciles_without_resend(tmp_path):
    hass, api, _, driver, _, review = await ready(tmp_path)
    identity = driver.public_key().hex()
    job = next(j for j in (await jobs(api, identity))["jobs"] if j.get("kind") == "adjustment")
    worker = AdjustmentCollections(api)
    row = worker.row(api.reviews.get(review["review_id"]))
    result = await handle(api, identity, {"action": "debit_status", **job})
    args = claim_data(result["collection"], row, driver)
    await handle(api, identity, {"action": "debit_claim", **job, **args})
    tx = payment(api, driver, 125, 5)
    await handle(api, identity, {"action": "debit_authorise", **job, **args, "draft": transaction_shape(tx)})
    api.chain.fail = True
    assert (await handle(api, identity, {"action": "debit_report", **job, **args,
                                       "raw_tx": tx.hex()}))["state"] == "broadcast_unknown"
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    with pytest.raises(WalletError):
        await handle(restored, identity, {"action": "debit_claim", **job, **args})
    # Uncertain attempts are not silently claimed, cancelled, or manually replaced.
    with pytest.raises(WalletError, match="manual replacement"):
        await restored.reviews.verify_driver_payment({"review_id": review["review_id"]}, "admin")
    restored.chain.fail = False
    from custom_components.bsv_settlement.confirmation_scheduler import DriverConfirmationScheduler
    scheduler = DriverConfirmationScheduler(restored)
    assert any(k == "adjustment:" + review["review_id"] for k, _, _ in scheduler.candidates())
    restored.reviews.get(review["review_id"])["wallet_collection"]["checked_at"] = None
    await scheduler.tick()
    assert restored.reviews.public(restored.reviews.get(review["review_id"]))["state"] == "driver_payment_provider_confirmed"
    assert len(api.chain.posts) == 1


async def test_wrong_recipient_excess_fee_and_changed_signed_draft_are_rejected(tmp_path):
    _, api, _, driver, _, review = await ready(tmp_path)
    identity = driver.public_key().hex()
    job = next(j for j in (await jobs(api, identity))["jobs"] if j.get("kind") == "adjustment")
    row = AdjustmentCollections(api).row(api.reviews.get(review["review_id"]))
    status = await handle(api, identity, {"action": "debit_status", **job})
    args = claim_data(status["collection"], row, driver)
    await handle(api, identity, {"action": "debit_claim", **job, **args})
    for tx in (payment(api, driver, 125, 900),
               payment(api, driver, 125, 5, PrivateKey(123).address())):
        with pytest.raises(WalletError):
            await handle(api, identity, {"action": "debit_authorise", **job, **args,
                                        "draft": transaction_shape(tx)})
    tx = payment(api, driver, 125, 5)
    await handle(api, identity, {"action": "debit_authorise", **job, **args, "draft": transaction_shape(tx)})
    with pytest.raises(WalletError):
        await handle(api, identity, {"action": "debit_report", **job, **args,
                                    "raw_tx": payment(api, driver, 126, 5).hex()})
    assert not api.chain.posts


async def test_http_adjustment_requires_owner_and_excludes_legacy_request(tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from types import SimpleNamespace
    from custom_components.bsv_settlement.const import DOMAIN
    from custom_components.bsv_settlement.portal import DriverPortalView
    from test_portal import login, post
    import asyncio
    hass, api, _, driver, _, review = await ready(tmp_path)
    hass.config.external_url = "https://charging.example.com"
    hass.data[DOMAIN][api.entry.entry_id] = SimpleNamespace(
        mode="embedded_mainnet", api=api, lock=asyncio.Lock())
    view = DriverPortalView(hass)
    app = web.Application()
    app.router.add_post(view.url, view.post)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        assert (await post(client, view, "debit_jobs")).status == 401
        cookie, _, _ = await login(client, view, driver)
        response = await post(client, view, "debit_jobs", cookie)
        queue = await response.json()
        job = next(j for j in queue["jobs"] if j.get("review_id") == review["review_id"])
        assert (await post(client, view, "debit_status", cookie, **job)).status == 200
        other, _, _ = await login(client, view, PrivateKey(999))
        assert (await post(client, view, "debit_status", other, **job)).status == 409
        assert not api.chain.posts
    finally:
        await client.close()


@pytest.mark.parametrize("change", ["revoked", "expired", "broadcast_disabled"])
async def test_last_check_after_wallet_signing_retains_hold_without_broadcast(tmp_path, change):
    _, api, registration, driver, _, review = await ready(tmp_path)
    identity = driver.public_key().hex()
    job = next(j for j in (await jobs(api, identity))["jobs"] if j.get("kind") == "adjustment")
    worker = AdjustmentCollections(api)
    stored = api.reviews.get(review["review_id"])
    row = worker.row(stored)
    status = await handle(api, identity, {"action": "debit_status", **job})
    args = claim_data(status["collection"], row, driver)
    await handle(api, identity, {"action": "debit_claim", **job, **args})
    tx = payment(api, driver, 125, 5)
    await handle(api, identity, {"action": "debit_authorise", **job, **args, "draft": transaction_shape(tx)})
    if change == "revoked":
        registration["state"] = "revoked"
    elif change == "expired":
        stored["expires_at"] = (now() - timedelta(seconds=1)).isoformat()
    else:
        api.hass.config_entries.async_update_entry(api.entry, data={**api.entry.data, "enable_broadcast": False})
    with pytest.raises(WalletError):
        await handle(api, identity, {"action": "debit_report", **job, **args, "raw_tx": tx.hex()})
    assert stored["wallet_collection"]["state"] == "submission_authorised"
    assert not api.chain.posts
