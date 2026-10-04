"""Automatic collection with fictional wallets and a recording, non-network chain."""
import copy
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from bsv import PrivateKey, P2PKH, Transaction, TransactionInput, TransactionOutput
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import canonical, sha, message_hash
from custom_components.bsv_settlement.collection import transaction_shape
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from test_budget import invitation, consent, no_network
from test_mainnet import FictionalChain

pytestmark = pytest.mark.asyncio


class CollectionChain(FictionalChain):
    async def request(self, method, path, raw=False):
        if self.fail or not self.posts:
            raise WalletError("Fictional provider has no payment evidence")
        return self.posts[-1]


async def ready(tmp_path, amount="1.89"):
    hass, api, proxy, data, row = await invitation(tmp_path)
    driver = PrivateKey()
    await api.budgets.execute("accept_session_budget", {
        "budget_id": row["terms"]["budget_id"], "receipt": consent(row, driver)}, "admin")
    stored = api.saved["session_budgets"][row["terms"]["budget_id"]]
    proxy.data["latest_session"].update(ended_at=now().isoformat(), net_cost_aud_unrounded=amount)
    api.chain = CollectionChain(driver.address())
    return hass, api, proxy, stored, driver


def claim_data(item, row, driver, token="a" * 43):
    payload = canonical({"version": 1, "action": "claim_session_collection",
        "budget_id": row["terms"]["budget_id"], "quote_hash": item["quote"]["hash"],
        "attempt_token_hash": sha(token), "driver_identity": driver.public_key().hex()})
    key = driver.derive_child(PrivateKey(1).public_key(),
                             f"2-ev session spending-{row['terms']['budget_id']}")
    return {"attempt_token": token, "proof": {
        "payload": payload, "signature": key.sign(payload.encode(), hasher=message_hash).hex()}}


def payment(api, driver, amount=189, fee=5, recipient=None):
    source = Transaction.from_hex(api.chain.raw)
    tx = Transaction([TransactionInput(source_transaction=source, source_output_index=0,
                        unlocking_script_template=P2PKH().unlock(driver))], [
        TransactionOutput(P2PKH().lock(recipient or api.identity["address"]), satoshis=amount),
        TransactionOutput(P2PKH().lock(driver.address()), satoshis=50000-amount-fee)])
    tx.sign()
    return tx


async def authorised(tmp_path):
    hass, api, proxy, row, driver = await ready(tmp_path)
    item = await api.collections.status(row)
    args = claim_data(item, row, driver)
    await api.collections.claim(row, args)
    tx = payment(api, driver)
    permit = await api.collections.authorise(row, args | {"draft": transaction_shape(tx)})
    assert permit["submit_once"] is True and permit["fee_sats"] == 5
    return hass, api, proxy, row, driver, args, tx


async def test_estimated_tariff_debit_quote_discloses_warning_without_claiming_wallet(tmp_path):
    _, api, proxy, row, _ = await ready(tmp_path, "0.24")
    proxy.data["latest_session"].update(
        estimated_rate_import_wh="500", quality_flags=["import:estimated_tariff"])
    result = await api.collections.status(row)
    assert result["state"] == "ready"
    quote = json.loads(result["quote"]["payload"])
    assert quote["amount_sats"] == 24
    assert "import:estimated_tariff" in quote["account"]["quality_flags"]
    assert not result.get("claimed_at") and not result.get("txid")
    assert not api.chain.posts


async def test_frozen_quote_exact_amount_and_no_duplicate_broadcast(tmp_path):
    hass, api, proxy, row, driver, args, tx = await authorised(tmp_path)
    hass.states.async_set("sensor.demo_rate", "200", {"unit_of_measurement": "sat/AUD"})
    quote = json.loads(api.collections.get(row)["quote"]["payload"])
    assert quote["amount_sats"] == 189 and quote["satoshis_per_aud"] == "100"
    with pytest.raises(WalletError, match="already"):
        await api.collections.authorise(row, args | {"draft": transaction_shape(tx)})
    sent = await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert sent["state"] == "provider_confirmed" and sent["txid"] == tx.txid()
    assert len(api.chain.posts) == 1
    assert "signed_raw" not in json.dumps(sent)
    assert args["attempt_token"] not in json.dumps(api.saved)
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    saved = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    await restored.collections.report(saved, args | {"raw_tx": tx.hex()})
    assert len(api.chain.posts) == 1
    with pytest.raises(WalletError):
        await restored.collections.claim(saved, args)


async def test_uncertain_broadcast_only_reconciles_never_resubmits(tmp_path):
    hass, api, _, row, _, args, tx = await authorised(tmp_path)
    api.chain.fail = True
    result = await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert result["state"] == "broadcast_unknown"
    await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert len(api.chain.posts) == 1
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    restored_row = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    assert (await restored.collections.report(restored_row, args | {"raw_tx": tx.hex()}))["state"] == "broadcast_unknown"
    assert len(api.chain.posts) == 1
    api.chain.fail = False
    assert (await api.collections.reconcile(row))["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1


@pytest.mark.parametrize("stop", ["revoked", "expired", "account_changed", "broadcast_disabled"])
async def test_last_check_after_wallet_signing_blocks_submission(tmp_path, stop):
    _, api, proxy, row, _, args, tx = await authorised(tmp_path)
    if stop == "revoked":
        row["state"] = "revoked"
    elif stop == "expired":
        row["terms"]["expires_at"] = (now()-timedelta(seconds=1)).isoformat()
    elif stop == "account_changed":
        proxy.data["latest_session"]["net_cost_aud_unrounded"] = "2.00"
    else:
        api.hass.config_entries.async_update_entry(api.entry, data={**api.entry.data, "enable_broadcast": False})
    with pytest.raises(WalletError):
        await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert not api.chain.posts


async def test_save_failure_before_network_never_broadcasts(tmp_path):
    _, api, _, row, _, args, tx = await authorised(tmp_path)
    api.store.async_save = AsyncMock(side_effect=OSError("fictional disk unavailable"))
    with pytest.raises(OSError):
        await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert not api.chain.posts


@pytest.mark.parametrize("amount,state", [("-2.10", "operator_credit_review_required"),
    ("0", "no_payment_due"), ("10", "collection_blocked"), ("0.001", "no_payment_due")])
async def test_negative_zero_and_over_limit_accounts_do_not_collect(tmp_path, amount, state):
    _, api, _, row, _ = await ready(tmp_path, amount)
    assert (await api.collections.status(row))["state"] == state
    assert not api.collections.get(row) and not api.chain.posts


async def test_waiting_quality_legacy_and_wrong_wallet_gates(tmp_path):
    _, api, proxy, row, driver = await ready(tmp_path)
    closed = proxy.data["latest_session"]["ended_at"]
    proxy.data["latest_session"]["ended_at"] = None
    assert (await api.collections.status(row))["state"] == "waiting_for_session_end"
    proxy.data["latest_session"]["ended_at"] = closed
    proxy.data["latest_session"]["unpriced_import_wh"] = "1"
    assert (await api.collections.status(row))["state"] == "collection_blocked"
    proxy.data["latest_session"]["unpriced_import_wh"] = "0"
    item = await api.collections.status(row)
    with pytest.raises(WalletError, match="approved driver"):
        await api.collections.claim(row, claim_data(item, row, PrivateKey()))
    await api.collections.claim(row, claim_data(item, row, driver))
    with pytest.raises(WalletError):
        await api.collections.claim(row, claim_data(item, row, driver))
    assert not api.chain.posts


@pytest.mark.parametrize("change", ["fee", "amount", "recipient", "duplicate_input"])
async def test_unsigned_draft_checks_before_permit(tmp_path, change):
    _, api, _, row, driver = await ready(tmp_path)
    args = claim_data(await api.collections.status(row), row, driver)
    await api.collections.claim(row, args)
    tx = payment(api, driver, fee=11 if change == "fee" else 5,
                 amount=190 if change == "amount" else 189,
                 recipient=PrivateKey().address() if change == "recipient" else None)
    shape = transaction_shape(tx)
    if change == "duplicate_input":
        shape["inputs"].append(copy.deepcopy(shape["inputs"][0]))
    with pytest.raises(WalletError):
        await api.collections.authorise(row, args | {"draft": shape})
    assert api.collections.get(row)["state"] == "wallet_attempt_reserved"
    assert not api.chain.posts


async def test_signed_transaction_cannot_change_authorised_outputs(tmp_path):
    _, api, _, row, driver, args, tx = await authorised(tmp_path)
    changed = payment(api, driver, amount=190)
    with pytest.raises(WalletError, match="differs"):
        await api.collections.report(row, args | {"raw_tx": changed.hex()})
    assert not api.chain.posts


async def test_manual_and_automatic_flows_are_mutually_exclusive(tmp_path):
    _, api, _, row, _ = await ready(tmp_path)
    data = {"proxy_config_entry_id": "proxy-entry", "session_id": "session-1",
            "conversion_rate_entity": "sensor.demo_rate"}
    review = await api.reviews.prepare(data, "admin")
    assert (await api.collections.status(row))["state"] == "collection_blocked"
    await api.reviews.cancel({"review_id": review["review_id"]})
    assert (await api.collections.status(row))["state"] == "ready"
    with pytest.raises(WalletError, match="already owns"):
        await api.reviews.prepare(data, "admin")


async def test_legacy_invitation_never_creates_collection(tmp_path):
    _, api, _, row, _ = await ready(tmp_path)
    row["terms"]["version"] = 1
    row["state"] = "consent_verified_not_payment_authority"
    assert (await api.collections.status(row))["state"] == "collection_blocked"
    assert not api.collections.get(row)


async def test_http_serialises_claims_and_rejects_capability_only_payment(tmp_path):
    import asyncio
    from test_driver_http import client_for
    _, api, _, row, driver = await ready(tmp_path)
    token = "b" * 43
    row["driver_token_hash"] = sha(token)
    client, view = await client_for(api.hass, api)
    try:
        args = {"budget_id": row["terms"]["budget_id"], "token": token}
        r = await client.post(view.url, json=args | {"action": "collection_status"})
        assert r.status == 200
        proof = claim_data(await r.json(), row, driver)
        bad = await client.post(view.url, json=args | {"action": "claim_collection"})
        assert bad.status == 400
        results = await asyncio.gather(*[
            client.post(view.url, json=args | {"action": "claim_collection", **proof}) for _ in range(2)])
        assert sorted(r.status for r in results) == [200, 400]
        bad = await client.post(view.url, json=args | {"action": "report_collection",
                                                      "raw_tx": payment(api, driver).hex()})
        assert bad.status == 400
        assert not api.chain.posts
    finally:
        await client.close()
