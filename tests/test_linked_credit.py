"""Guarded CPFP tests: fictional transactions only; never live wallet keys."""
import copy
from decimal import Decimal, ROUND_CEILING
from unittest.mock import AsyncMock

import pytest
from bsv import PrivateKey, Transaction

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.linked_credit import evidence, linked_quote
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI, WoCClient, build_transaction
from test_budget import no_network
from test_ongoing_credit import setup
from test_operator_credit_recovery import approval

pytestmark = pytest.mark.asyncio


async def fixture(tmp_path, prepare=True):
    hass, api, proxy, row, _, _ = await setup(tmp_path, "-1.19")
    route = next(iter(api.ongoing_credits.routes.values()))
    recipient = route["recipient"]["address"]
    signed = build_transaction(api.identity["secret_hex"], api.chain.raw, 0, recipient, 38, 10, True)
    parent = {"budget_id": "parent-credit", "state": "provider_unconfirmed",
              "txid": signed["txid"], "signed_raw": signed["raw"], "amount_sats": 38,
              "fee_sats": 10, "source_txid": signed["source_txid"], "source_index": 0,
              "recipient_address": recipient, "session_id": "parent-session"}
    api.saved["automatic_credits"]["parent-credit"] = parent
    api.chain.fee_policy = AsyncMock(return_value={
        "fee_unit": "sat/KB", "fee": 100, "mempool_min_fee": 100})
    original_request = api.chain.request

    async def request(method, path, raw=False):
        if path == f"/tx/{parent['txid']}/hex":
            return signed["raw"]
        return await original_request(method, path, raw)

    api.chain.request = AsyncMock(side_effect=request)

    async def details(txid):
        return {"txid": txid, "confirmations": 0 if txid == parent["txid"] else 1}

    api.chain.details = AsyncMock(side_effect=details)
    api.chain.unconfirmed_unspent = AsyncMock(return_value=[{
        "tx_hash": parent["txid"], "tx_pos": 1, "value": signed["change_sats"],
        "isSpentInMempoolTx": False}])
    data = {"credit_id": route["route_id"], "parent_credit_id": "parent-credit"}
    review = await api.credit_recovery.prepare(data, "admin") if prepare else None
    return hass, api, proxy, row, route, parent, review, data


async def test_unsigned_review_exact_package_and_no_parent_changes(tmp_path):
    _, api, _, _, route, parent, review, data = await fixture(tmp_path)
    assert review["amount_sats"] == 119 and review["fee_sats"] == 36
    assert review["total_sats"] == 155
    assert review["package_total_sats"] == 203 and review["package_fee_sats"] == 46
    assert review["source_txid"] == parent["txid"] and review["source_index"] == 1
    assert review["linked_parent"]["amount_sats"] == 38
    assert review["linked_parent"]["fee_sats"] == 10
    assert review["funds_reserved"] is False and review["automatic_broadcast"] is False
    assert not api.chain.posts
    old = copy.deepcopy(parent)
    assert await api.credit_recovery.prepare(data, "admin") == review
    assert parent == old
    await api.ongoing_credits.tick()
    await api.auto_credits.tick()
    assert not api.chain.posts


async def test_exact_child_send_once_after_restart_preserves_parent_and_receipt(tmp_path):
    hass, api, _, row, route, parent, review, _ = await fixture(tmp_path)
    before = copy.deepcopy(parent)
    result = await api.credit_recovery.broadcast(approval(review), "admin")
    assert result["state"] == "submitted" and len(api.chain.posts) == 1
    child = Transaction.from_hex(api.chain.posts[0])
    assert child.inputs[0].source_txid == parent["txid"]
    assert child.inputs[0].source_output_index == 1
    assert child.outputs[0].satoshis == 119  # Not 157: parent already pays 38.
    assert parent == before
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    await restored.credit_recovery.broadcast(approval(review), "admin")
    await restored.ongoing_credits.tick()
    assert len(api.chain.posts) == 1
    receipt = await restored.ongoing_credits.driver_receipt(row, route["route_id"])
    assert receipt["amount_sats"] == 119 and receipt["fee_sats"] == 36
    assert receipt["energy_account"]["net_amount_aud"] == "-1.19"
    assert receipt["recipient_address"] == parent["recipient_address"]


@pytest.mark.parametrize("problem", [
    "recipient", "parent_fee", "parent_raw", "provider_raw", "confirmed", "missing_confirmation",
    "null_confirmation", "spent", "locally_used", "other_pending", "manual_pending",
    "quote_rise", "ancestor_missing", "account_change", "wrong_review", "wrong_fee",
])
async def test_changed_evidence_never_signs_or_broadcasts(tmp_path, problem):
    _, api, proxy, _, route, parent, review, _ = await fixture(tmp_path)
    data = approval(review)
    if problem == "recipient":
        parent["recipient_address"] = PrivateKey().address()
    elif problem == "parent_fee":
        parent["fee_sats"] = 11
    elif problem == "parent_raw":
        parent["signed_raw"] = "00"
    elif problem == "provider_raw":
        api.chain.request = AsyncMock(return_value="00")
    elif problem == "confirmed":
        api.chain.details = AsyncMock(return_value={"txid": parent["txid"], "confirmations": 1})
    elif problem == "missing_confirmation":
        api.chain.details = AsyncMock(return_value={"txid": parent["txid"]})
    elif problem == "null_confirmation":
        api.chain.details = AsyncMock(return_value={"txid": parent["txid"], "confirmations": None})
    elif problem == "spent":
        api.chain.unconfirmed_unspent = AsyncMock(return_value=[])
    elif problem == "locally_used":
        api.saved["payments"]["other"] = {"txid": "ab"*32, "state": "provider_confirmed",
                                        "source_txid": parent["txid"], "source_index": 1}
    elif problem == "other_pending":
        api.saved["automatic_credits"]["other"] = {"state": "broadcast_unknown"}
    elif problem == "manual_pending":
        api.saved["payments"]["other"] = {"state": "prepared"}
    elif problem == "quote_rise":
        api.chain.fee_policy = AsyncMock(return_value={"fee_unit": "sat/KB", "fee": 200, "mempool_min_fee": 200})
    elif problem == "ancestor_missing":
        api.chain.source = AsyncMock(side_effect=WalletError("Ancestor unconfirmed or missing"))
    elif problem == "account_change":
        proxy.data["latest_session"]["net_cost_aud_unrounded"] = "-1.20"
    elif problem == "wrong_review":
        data["expected_review_hash"] = "bad"
    else:
        data["fee_sats"] += 1
    with pytest.raises(WalletError):
        await api.credit_recovery.broadcast(data, "admin")
    assert not api.chain.posts
    assert api.credit_recovery._permit is None
    assert not api.saved["automatic_credits"][route["route_id"]].get("txid")


async def test_ordinary_recovery_cannot_ignore_pending_parent(tmp_path):
    _, api, _, _, route, _, _, _ = await fixture(tmp_path, prepare=False)
    with pytest.raises(WalletError, match="unresolved"):
        await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    await api.ongoing_credits.tick()
    assert not api.chain.posts


async def test_no_durable_permission_or_bypass_after_restart(tmp_path):
    hass, api, _, _, route, _, review, _ = await fixture(tmp_path)
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    wrapper = restored.ongoing_credits.wrapper(restored.ongoing_credits.routes[route["route_id"]])
    with pytest.raises(WalletError, match="exact approval"):
        restored.ongoing_credits.blocking_pending(wrapper)
    await restored.ongoing_credits.tick()
    assert not api.chain.posts
    assert restored.credit_recovery.public(restored.ongoing_credits.routes[route["route_id"]])["review_hash"] == review["review_hash"]


async def test_uncertain_child_never_rebroadcasts(tmp_path):
    _, api, _, _, _, parent, review, _ = await fixture(tmp_path)
    api.chain.fail = True
    result = await api.credit_recovery.broadcast(approval(review), "admin")
    assert result["state"] == "broadcast_unknown" and len(api.chain.posts) == 1
    await api.credit_recovery.broadcast(approval(review), "admin")
    assert len(api.chain.posts) == 1


async def test_fee_rise_after_funding_check_still_stops_unsigned(tmp_path):
    _, api, _, _, route, _, review, _ = await fixture(tmp_path)
    api.chain.fee_policy = AsyncMock(side_effect=[
        {"fee_unit": "sat/KB", "fee": 100, "mempool_min_fee": 100},
        {"fee_unit": "sat/KB", "fee": 200, "mempool_min_fee": 200},
    ])
    with pytest.raises(WalletError, match="increased"):
        await api.credit_recovery.broadcast(approval(review), "admin")
    assert not api.chain.posts
    assert not api.saved["automatic_credits"][route["route_id"]].get("txid")


async def test_lower_quote_never_changes_exact_reviewed_fee_or_package_totals(tmp_path):
    _, api, _, _, route, _, review, _ = await fixture(tmp_path)
    api.chain.fee_policy = AsyncMock(return_value={"fee_unit": "sat/KB", "fee": 40, "mempool_min_fee": 40})
    await api.credit_recovery.broadcast(approval(review), "admin")
    item = api.saved["automatic_credits"][route["route_id"]]
    assert item["fee_sats"] == 36
    assert item["fee_quote"]["package_fee_sats"] == 46
    assert item["fee_quote"]["package_total_sats"] == 203


async def test_linked_signed_storage_failure_cannot_submit_or_retry(tmp_path):
    _, api, _, _, route, parent, review, _ = await fixture(tmp_path)
    before = copy.deepcopy(parent)
    save = api.store.async_save

    async def fail_signed(saved):
        if saved["automatic_credits"][route["route_id"]].get("txid"):
            raise OSError("Fictional child outbox write failure")
        await save(saved)

    api.store.async_save = fail_signed
    with pytest.raises(OSError):
        await api.credit_recovery.broadcast(approval(review), "admin")
    assert not api.chain.posts and api.credit_recovery._permit is None
    assert parent == before
    api.store.async_save = save
    await api.credit_recovery.broadcast(approval(review), "admin")
    assert not api.chain.posts


async def test_combined_total_cap_not_two_independent_caps(tmp_path):
    _, api, _, _, _, parent, _, _ = await fixture(tmp_path, prepare=False)
    proof, _, _ = await evidence(api, "parent-credit", parent["recipient_address"])
    q = await linked_quote(api, proof, 916)  # 38 + 10 + 916 + 36 = 1000.
    assert q["package_total_sats"] == 1000
    with pytest.raises(WalletError, match="1000"):
        await linked_quote(api, proof, 917)
    expected = int((Decimal("100") * (proof["size_bytes"] + 227) / 1000)
                   .to_integral_value(rounding=ROUND_CEILING))
    assert q["package_fee_sats"] >= expected


async def test_explicit_zero_and_complete_omitted_confirmation_are_supported(tmp_path):
    _, api, _, _, _, parent, _, _ = await fixture(tmp_path, prepare=False)
    tx = Transaction.from_hex(parent["signed_raw"])
    api.chain.details = AsyncMock(return_value={
        "txid": tx.txid(), "hash": tx.txid(), "version": tx.version,
        "locktime": tx.locktime, "size": len(tx.hex())//2, "vin": [{}], "vout": [{}, {}]})
    assert (await evidence(api, "parent-credit", parent["recipient_address"]))[0]["txid"] == tx.txid()


@pytest.mark.parametrize("problem", ["good", "spent", "missing_flag", "duplicate", "wrong_address", "paged", "bool_value"])
async def test_unconfirmed_provider_adapter_fails_closed(problem):
    address = PrivateKey().address()
    row = {"tx_hash": "ab"*32, "tx_pos": 1, "value": 4445, "isSpentInMempoolTx": False}
    data = {"address": address, "result": [row]}
    if problem == "spent":
        row["isSpentInMempoolTx"] = True
    elif problem == "missing_flag":
        del row["isSpentInMempoolTx"]
    elif problem == "duplicate":
        data["result"].append(copy.deepcopy(row))
    elif problem == "wrong_address":
        data["address"] = PrivateKey().address()
    elif problem == "paged":
        data["next-page"] = "more"
    elif problem == "bool_value":
        row["value"] = True
    chain = WoCClient(None)
    chain.request = AsyncMock(return_value=data)
    if problem in ("good", "spent"):
        assert len(await chain.unconfirmed_unspent(address)) == (problem == "good")
    else:
        with pytest.raises(WalletError):
            await chain.unconfirmed_unspent(address)
    chain.request.assert_awaited_once_with("GET", f"/address/{address}/unconfirmed/unspent")
