"""Receipt acknowledgement fixtures never access real wallets or providers."""
import asyncio
import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import canonical, message_hash
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.receipt_ack import acknowledge, acceptance_payload, PROTOCOL
from custom_components.bsv_settlement.session_review import now
from test_auto_credit import ready
from test_budget import no_network
from test_driver_http import client_for
from test_ongoing_credit import setup

pytestmark = pytest.mark.asyncio


def report(api, row, item, driver, changes=None):
    body = json.loads(acceptance_payload(api, row, item))
    body.update(changes or {})
    payload = canonical(body)
    key = driver.derive_child(PrivateKey(1).public_key(), f"2-{PROTOCOL}-{row['terms']['budget_id']}")
    return {"acknowledgement": {"payload": payload,
        "signature": key.sign(payload.encode(), hasher=message_hash).hex()}}


async def confirmed(tmp_path, ongoing=False):
    hass, api, proxy, row, driver, _ = await (setup(tmp_path) if ongoing else ready(tmp_path))
    worker = api.ongoing_credits if ongoing else api.auto_credits
    await worker.tick()
    await worker.tick()
    item = next(iter(api.saved["automatic_credits"].values()))
    assert item["state"] == "provider_confirmed"
    return hass, api, row, driver, item


@pytest.mark.parametrize("ongoing", [False, True])
async def test_persist_idempotent_reload_and_no_payment_effect(tmp_path, ongoing):
    hass, api, row, driver, item = await confirmed(tmp_path, ongoing)
    before = copy.deepcopy(api.saved)
    data = report(api, row, item, driver)
    if ongoing:
        data["credit_id"] = item["budget_id"]
    result = await acknowledge(api, row, data)
    assert result["wallet_receipt_status"] == "wallet_reported_accepted"
    assert result["wallet_imported_at"]
    assert result["state"] == "provider_confirmed"
    assert "wallet_receipt_ack" not in result
    assert await acknowledge(api, row, data) == result
    # Even an alternate valid signature must retain the first server time.
    data.update(report(api, row, item, driver))
    assert await acknowledge(api, row, data) == result
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    assert restored.auto_credits.public(restored.saved["automatic_credits"][item["budget_id"]]) == result
    stripped = copy.deepcopy(api.saved)
    stripped["automatic_credits"][item["budget_id"]].pop("wallet_receipt_ack")
    assert stripped == before
    assert len(api.chain.posts) == 1


@pytest.mark.parametrize("changes", [
    {"txid": "00"*32}, {"output_index": 1}, {"amount_sats": 1},
    {"budget_id": "another"}, {"credit_id": "another"}, {"session_id": "another"},
    {"transaction_id": "another"}, {"recipient_address": "another"},
    {"driver_identity": "another"}, {"operator_identity": "another"},
    {"invitation_hash": "00"*32}, {"network": "testnet"},
    {"accepted": False}, {"accepted": "true"}, {"action": "authorise_spending"},
    {"version": True},
])
async def test_signed_but_mismatched_report_is_rejected(tmp_path, changes):
    _, api, row, driver, item = await confirmed(tmp_path)
    with pytest.raises(WalletError, match="Invalid wallet receipt"):
        await acknowledge(api, row, report(api, row, item, driver, changes))
    assert "wallet_receipt_ack" not in item
    assert len(api.chain.posts) == 1


async def test_wrong_signer_invalid_signature_and_cross_registration(tmp_path):
    _, api, row, driver, item = await confirmed(tmp_path, True)
    for data in [
        report(api, row, item, PrivateKey()),
        {"acknowledgement": None},
        {"acknowledgement": {"payload": acceptance_payload(api, row, item), "signature": "00"*70}},
    ]:
        data["credit_id"] = item["budget_id"]
        with pytest.raises(WalletError):
            await acknowledge(api, row, data)
    stranger = copy.deepcopy(row)
    stranger["terms"]["budget_id"] = "other"
    with pytest.raises(WalletError, match="does not belong"):
        await acknowledge(api, stranger, report(api, row, item, driver) | {"credit_id": item["budget_id"]})
    with pytest.raises(WalletError):
        await acknowledge(api, row, report(api, row, item, driver))  # route ID is required
    assert "wallet_receipt_ack" not in item


async def test_storage_failure_does_not_leave_false_success(tmp_path):
    _, api, row, driver, item = await confirmed(tmp_path)
    save = api.store.async_save
    async def fail(_):
        raise OSError("fictional storage failure")
    api.store.async_save = fail
    data = report(api, row, item, driver)
    with pytest.raises(OSError):
        await acknowledge(api, row, data)
    assert api.auto_credits.public(item)["wallet_receipt_status"] == "not_recorded"
    api.store.async_save = save
    assert (await acknowledge(api, row, data))["wallet_imported_at"]


async def test_no_confirmation_or_no_payment_is_not_acceptance(tmp_path):
    _, api, row, driver, item = await confirmed(tmp_path)
    data = report(api, row, item, driver)
    item["state"] = "provider_unconfirmed"
    with pytest.raises(WalletError, match="awaits provider confirmation"):
        await acknowledge(api, row, data)
    item["state"] = "provider_confirmed"
    assert api.auto_credits.public(item)["wallet_imported_at"] is None
    await acknowledge(api, row, data)
    item["state"] = "broadcast_unknown"  # Subsequent chain uncertainty is not erased.
    result = await acknowledge(api, row, data)
    assert result["state"] == "broadcast_unknown"
    assert result["wallet_receipt_status"] == "wallet_reported_accepted"


@pytest.mark.parametrize("terminal", ["expired", "revoked"])
async def test_capability_endpoint_historical_report_and_bad_token(tmp_path, terminal):
    hass, api, row, driver, item = await confirmed(tmp_path)
    data = report(api, row, item, driver)
    if terminal == "expired":
        row["terms"]["expires_at"] = (now()-timedelta(seconds=1)).isoformat()
        # Build the expected report before changing expiry: signed invitation is unchanged.
    else:
        row["state"] = "revoked"
    args = {"budget_id": row["terms"]["budget_id"],
            "token": api.budgets.link_token(row["terms"]["budget_id"]),
            "action": "acknowledge_credit_receipt", **data}
    client, view = await client_for(hass, api)
    try:
        response = await client.post(view.url, json=args | {"token": "x"*43})
        assert response.status == 400
        response = await client.post(view.url, json=args, headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status == 403
        response = await client.post(view.url, json=args)
        assert response.status == 200
        assert (await response.json())["wallet_receipt_status"] == "wallet_reported_accepted"
        response = await client.post(view.url, json=args | {"action": "collection_status"})
        assert (await response.json())["wallet_receipt_status"] == "wallet_reported_accepted"
        assert len(api.chain.posts) == 1
    finally:
        await client.close()


async def test_typescript_signature_verified_by_python(tmp_path):
    _, api, row, driver, item = await confirmed(tmp_path)
    script = Path(__file__).resolve().parents[1] / "frontend/driver/cross-receipt.cjs"
    proc = await asyncio.create_subprocess_exec("node", str(script),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate(json.dumps({
        "fictional_key": driver.hex(), "budget_id": row["terms"]["budget_id"],
        "payload": acceptance_payload(api, row, item)}).encode())
    assert proc.returncode == 0, stderr.decode()
    assert (await acknowledge(api, row, json.loads(stdout)))["wallet_imported_at"]
